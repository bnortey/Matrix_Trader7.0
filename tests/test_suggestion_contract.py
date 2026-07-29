import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import app as mt7


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


learner_analyzer = load_module(
    "mt7_learner_analyzer_contract_test",
    ROOT / "mt-learner" / "analyzer.py",
)
learner_suggester = load_module(
    "mt7_learner_suggester_contract_test",
    ROOT / "mt-learner" / "suggester.py",
)


class SuggestionContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        self.old_paths = {
            "STRATEGY_OVERRIDES_PATH": mt7.STRATEGY_OVERRIDES_PATH,
            "LEARNER_PENDING_PATH": mt7.LEARNER_PENDING_PATH,
            "LEARNER_REJECTED_PATH": mt7.LEARNER_REJECTED_PATH,
            "EXPERIMENT_LEDGER_PATH": mt7.EXPERIMENT_LEDGER_PATH,
            "PAPER_CONFIG_PATH": mt7.PAPER_CONFIG_PATH,
            "GOALS_PATH": mt7.GOALS_PATH,
            "DB_PATH": mt7.DB_PATH,
        }
        mt7.STRATEGY_OVERRIDES_PATH = str(root / "strategy_overrides.json")
        mt7.LEARNER_PENDING_PATH = str(root / "pending.json")
        mt7.LEARNER_REJECTED_PATH = str(root / "rejected.json")
        mt7.EXPERIMENT_LEDGER_PATH = str(root / "experiment_ledger.json")
        mt7.PAPER_CONFIG_PATH = str(root / "paper_config.json")
        mt7.GOALS_PATH = str(root / "goals.json")
        mt7.DB_PATH = str(root / "missing_signals.db")
        Path(mt7.PAPER_CONFIG_PATH).write_text(
            json.dumps({
                "account_balance_usd": 200,
                "risk_pct_per_trade": 5,
                "max_open_positions": 4,
                "paper_margin_mode": "isolated",
            }),
            encoding="utf-8",
        )
        Path(mt7.STRATEGY_OVERRIDES_PATH).write_text(
            json.dumps({"funding_arb": {"min_conviction": 69}}),
            encoding="utf-8",
        )
        mt7.app.config["TESTING"] = True

    def tearDown(self):
        for name, value in self.old_paths.items():
            setattr(mt7, name, value)
        self.tmp.cleanup()

    def test_legacy_threshold_is_normalized_against_runtime_authority(self):
        suggestion = {
            "id": "generic_threshold",
            "type": "threshold",
            "strategy": "funding_arb",
            "status": "pending_review",
            "current_value": 60,
            "suggested_value": 81,
            "sample_size": 81,
            "confidence": "high",
            "reasoning": "Historical threshold comparison.",
            "expected_net_ev_delta": "+5.78 pct/trade",
            "api_payload": {"min_conviction": 81},
        }

        annotated = mt7._annotate_suggestion_authority([suggestion])[0]
        contract = annotated["explainability"]

        self.assertTrue(annotated["baseline_conflict"])
        self.assertEqual(contract["change_set"][0]["current"], 69)
        self.assertEqual(contract["change_set"][0]["proposed"], 81)
        self.assertEqual(
            contract["authority"]["runtime_actual"],
            69,
        )
        self.assertFalse(contract["application_policy"]["auto_apply_allowed"])
        self.assertFalse(contract["application_policy"]["restricted"])
        self.assertTrue(contract["completeness"]["complete"])

    def test_capital_change_discloses_budget_margin_and_concurrent_exposure(self):
        suggestion = {
            "id": "capital_change",
            "type": "paper_risk_adjustment",
            "strategy": "balanced",
            "status": "pending_review",
            "sample_size": 100,
            "confidence": "medium",
            "reasoning": "Test a larger Paper allocation.",
            "change_set": [
                {"field": "risk_pct_per_trade", "current": 5, "proposed": 8},
                {"field": "leverage", "current": 10, "proposed": 20},
                {"field": "position_size_usd", "current": 50, "proposed": 60},
                {"field": "max_open_positions", "current": 4, "proposed": 6},
            ],
        }

        contract = mt7._suggestion_explainability_contract(suggestion)
        impact = contract["capital_impact"]

        self.assertEqual(contract["classification"], "restricted_capital_change")
        self.assertTrue(contract["application_policy"]["restricted"])
        self.assertEqual(
            contract["application_policy"]["confirmation_phrase"],
            "APPROVE capital_change",
        )
        self.assertEqual(impact["target_risk_budget_current_usd"], 10.0)
        self.assertEqual(impact["target_risk_budget_proposed_usd"], 16.0)
        self.assertEqual(impact["estimated_margin_proposed_usd"], 3.0)
        self.assertEqual(impact["max_concurrent_notional_proposed_usd"], 360.0)
        self.assertIn("not a guaranteed maximum loss", impact["loss_semantics"])

    def test_apply_requires_current_acknowledged_contract(self):
        suggestion = {
            "id": "threshold_trial",
            "type": "threshold",
            "strategy": "funding_arb",
            "status": "pending_review",
            "current_value": 69,
            "suggested_value": 75,
            "sample_size": 90,
            "confidence": "high",
            "reasoning": "Forward-test a stricter admission threshold.",
            "api_payload": {"min_conviction": 75},
        }
        Path(mt7.LEARNER_PENDING_PATH).write_text(
            json.dumps({"suggestions": [suggestion]}),
            encoding="utf-8",
        )
        contract = mt7._suggestion_explainability_contract(suggestion)

        with mt7.app.test_client() as client:
            missing_ack = client.post(
                "/api/intelligence/suggestions/threshold_trial/apply",
                json={},
            )
            wrong_fingerprint = client.post(
                "/api/intelligence/suggestions/threshold_trial/apply",
                json={
                    "suggestion_contract_version": mt7.SUGGESTION_CONTRACT_VERSION,
                    "acknowledge_explainability": True,
                    "baseline_fingerprint": "old-contract",
                },
            )
            accepted = client.post(
                "/api/intelligence/suggestions/threshold_trial/apply",
                json={
                    "suggestion_contract_version": mt7.SUGGESTION_CONTRACT_VERSION,
                    "acknowledge_explainability": True,
                    "baseline_fingerprint": contract["audit"]["baseline_fingerprint"],
                },
            )

        self.assertEqual(missing_ack.status_code, 409)
        self.assertEqual(wrong_fingerprint.status_code, 409)
        self.assertEqual(accepted.status_code, 200)
        overrides = json.loads(Path(mt7.STRATEGY_OVERRIDES_PATH).read_text())
        self.assertEqual(overrides["funding_arb"]["min_conviction"], 75)

    def test_restricted_advisory_change_cannot_silently_apply(self):
        suggestion = {
            "id": "leveraged_strategy",
            "type": "new_strategy",
            "strategy": "balanced",
            "status": "pending_review",
            "sample_size": 100,
            "confidence": "medium",
            "reasoning": "Research-only leveraged custom strategy idea.",
            "api_payload": {
                "name": "Balanced Controlled Leverage",
                "base_key": "balanced",
                "leverage_cap": 20,
            },
        }
        contract = mt7._suggestion_explainability_contract(suggestion)
        common = {
            "suggestion_contract_version": mt7.SUGGESTION_CONTRACT_VERSION,
            "acknowledge_explainability": True,
            "baseline_fingerprint": contract["audit"]["baseline_fingerprint"],
        }

        without_phrase = mt7._suggestion_application_gate(suggestion, common)
        with_phrase = mt7._suggestion_application_gate(
            suggestion,
            {**common, "confirmation": "APPROVE leveraged_strategy"},
        )

        self.assertFalse(without_phrase[0])
        self.assertIn("exact confirmation phrase", without_phrase[1])
        self.assertTrue(with_phrase[0])

    def test_analyzer_uses_runtime_threshold_for_any_strategy(self):
        db_path = self.root / "learner.db"
        con = sqlite3.connect(db_path)
        con.execute(
            "CREATE TABLE signals (strategy_key TEXT, result TEXT, pnl_pct REAL, "
            "logged_at TEXT, conviction REAL)"
        )
        rows = []
        for index in range(40):
            rows.append(("balanced", "LOSS", -5.0, f"2026-01-01T00:{index:02d}:00", 60 + index % 9))
        for index in range(80):
            rows.append(("balanced", "WIN", 4.0, f"2026-01-02T00:{index % 60:02d}:00", 75 + index % 10))
        con.executemany("INSERT INTO signals VALUES (?,?,?,?,?)", rows)
        con.commit()
        con.close()
        learner_analyzer.MODELS_DIR = str(self.root / "models")
        Path(learner_analyzer.MODELS_DIR).mkdir()

        result = learner_analyzer.run_threshold_analysis(
            str(db_path),
            runtime_controls={
                "balanced": {
                    "min_conviction": 69,
                    "min_conviction_authority": "strategy_override",
                    "enabled": True,
                    "is_custom": False,
                }
            },
        )
        info = result["strategies"]["balanced"]

        self.assertEqual(info["current_implied_threshold"], 60)
        self.assertEqual(info["runtime_threshold"], 69)
        self.assertEqual(info["runtime_threshold_source"], "strategy_override")
        self.assertEqual(
            info["delta_from_current"],
            info["optimal_threshold"] - 69,
        )

    def test_new_learner_suggestion_carries_universal_contract(self):
        models = self.root / "suggester_models"
        suggestions = self.root / "suggester_suggestions"
        models.mkdir()
        suggestions.mkdir()
        learner_suggester.MODELS_DIR = str(models)
        learner_suggester.SUGGESTIONS_DIR = str(suggestions)
        learner_suggester.PENDING_PATH = str(suggestions / "pending.json")
        learner_suggester.REJECTED_PATH = str(self.root / "suggester_rejected.json")
        (models / "conviction_thresholds.json").write_text(
            json.dumps({
                "strategies": {
                    "balanced": {
                        "optimal_threshold": 81,
                        "runtime_threshold": 69,
                        "runtime_threshold_source": "strategy_override",
                        "runtime_snapshot_at": "2026-07-28T20:00:00+00:00",
                        "current_implied_threshold": 60,
                        "optimal_sample_size": 81,
                        "optimal_net_expectancy": 7.63,
                        "current_net_expectancy": 1.84,
                        "net_expectancy_delta": 5.79,
                        "delta_from_current": 12,
                        "optimal_win_rate": 0.36,
                        "optimal_win_partial_rate": 0.54,
                        "current_win_partial_rate": 0.48,
                        "optimal_max_loss_streak": 4,
                        "current_max_loss_streak": 8,
                        "confidence": "high",
                    }
                }
            }),
            encoding="utf-8",
        )
        Path(learner_suggester.PENDING_PATH).write_text(
            json.dumps({
                "suggestions": [{
                    "id": "old_balanced_threshold",
                    "type": "threshold",
                    "strategy": "balanced",
                    "status": "pending_review",
                    "current_value": 60,
                    "suggested_value": 81,
                    "api_payload": {"min_conviction": 81},
                }]
            }),
            encoding="utf-8",
        )

        result = learner_suggester.run_strategy_proposal_check()
        by_id = {item["id"]: item for item in result["suggestions"]}
        suggestion = next(
            item
            for item in result["suggestions"]
            if item["id"] != "old_balanced_threshold"
        )

        self.assertEqual(by_id["old_balanced_threshold"]["status"], "superseded")
        self.assertEqual(
            by_id["old_balanced_threshold"]["runtime_actual_at_supersession"],
            69,
        )
        self.assertEqual(result["superseded_stale_ids"], ["old_balanced_threshold"])
        self.assertEqual(suggestion["current_value"], 69)
        self.assertEqual(suggestion["change_set"][0]["current"], 69)
        self.assertEqual(
            suggestion["suggestion_contract_version"],
            "mt7_suggestion_v1",
        )
        self.assertFalse(suggestion["approval_policy"]["auto_apply_allowed"])
        self.assertIn("from 69 to 81", suggestion["evidence_summary"])

    def test_threshold_suggester_fails_closed_without_runtime_authority(self):
        models = self.root / "fallback_models"
        suggestions = self.root / "fallback_suggestions"
        models.mkdir()
        suggestions.mkdir()
        learner_suggester.MODELS_DIR = str(models)
        learner_suggester.SUGGESTIONS_DIR = str(suggestions)
        learner_suggester.PENDING_PATH = str(suggestions / "pending.json")
        learner_suggester.REJECTED_PATH = str(self.root / "fallback_rejected.json")
        (models / "conviction_thresholds.json").write_text(
            json.dumps({
                "strategies": {
                    "balanced": {
                        "optimal_threshold": 81,
                        "runtime_threshold": 60,
                        "runtime_threshold_source": "historical_implied_fallback",
                        "current_implied_threshold": 60,
                        "optimal_sample_size": 100,
                        "optimal_net_expectancy": 7.0,
                        "current_net_expectancy": 1.0,
                        "net_expectancy_delta": 6.0,
                        "delta_from_current": 21,
                    }
                }
            }),
            encoding="utf-8",
        )

        result = learner_suggester.run_strategy_proposal_check()

        self.assertEqual(result["suggestions"], [])

    def test_unverified_matching_baseline_is_still_superseded(self):
        suggestion = {
            "id": "unverified_threshold",
            "type": "threshold",
            "strategy": "balanced",
            "status": "pending_review",
            "current_value": 65,
            "suggested_value": 67,
            "baseline_snapshot": {"source": "historical_implied_fallback"},
        }
        changed = learner_suggester._supersede_stale_threshold_proposals(
            [suggestion],
            {
                "strategies": {
                    "balanced": {
                        "runtime_threshold": 65,
                        "runtime_threshold_source": "strategy_override",
                    }
                }
            },
        )

        self.assertEqual(changed, ["unverified_threshold"])
        self.assertEqual(suggestion["status"], "superseded")
        self.assertEqual(
            suggestion["superseded_reason"],
            "runtime_baseline_unverified",
        )

    def test_duplicate_suggestion_ids_are_repaired_and_future_ids_do_not_collide(self):
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        suggestions = [
            {"id": f"thresh_balanced_focus_short_{today}_001"},
            {"id": f"thresh_balanced_focus_short_{today}_001"},
        ]

        repaired = learner_suggester._repair_duplicate_suggestion_ids(suggestions)

        self.assertEqual(
            repaired,
            [{
                "old_id": f"thresh_balanced_focus_short_{today}_001",
                "new_id": f"thresh_balanced_focus_short_{today}_001_r2",
            }],
        )
        self.assertEqual(
            suggestions[0]["id"],
            f"thresh_balanced_focus_short_{today}_001",
        )
        self.assertEqual(
            suggestions[1]["id"],
            f"thresh_balanced_focus_short_{today}_001_r2",
        )
        self.assertEqual(
            suggestions[1]["legacy_duplicate_id"],
            f"thresh_balanced_focus_short_{today}_001",
        )
        next_id = learner_suggester._next_suggestion_id(
            "thresh_balanced_focus_short",
            suggestions,
            [],
        )
        self.assertNotIn(next_id, {item["id"] for item in suggestions})
        self.assertTrue(next_id.endswith("_002"))


if __name__ == "__main__":
    unittest.main()
