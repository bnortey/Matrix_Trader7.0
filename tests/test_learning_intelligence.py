import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app as mt7
from lib.learning_intelligence import (
    cohort_metrics,
    evaluate_experiment,
    stable_fingerprint,
    validate_strategy_factory_contract,
)


class LearningIntelligencePureTests(unittest.TestCase):
    def test_fingerprint_is_canonical_and_policy_sensitive(self):
        self.assertEqual(
            stable_fingerprint({"b": 2, "a": 1}),
            stable_fingerprint({"a": 1, "b": 2}),
        )
        self.assertNotEqual(
            stable_fingerprint({"min_conviction": 70}),
            stable_fingerprint({"min_conviction": 71}),
        )

    def test_metrics_are_net_and_outlier_aware(self):
        rows = [
            {"result": "WIN", "pnl_pct": 10, "size_usd": 100},
            {"result": "LOSS", "pnl_pct": -2, "size_usd": 100},
            {"result": "PARTIAL", "pnl_pct": 1, "size_usd": 100},
        ]
        metrics = cohort_metrics(rows)
        self.assertEqual(metrics["count"], 3)
        self.assertEqual(metrics["win_partial_rate"], 66.67)
        self.assertEqual(metrics["leave_best_out_avg_pnl_pct"], -0.5)
        self.assertEqual(metrics["net_pnl_usd"], 9.0)

    def test_forward_evaluation_falsifies_mature_underperformance(self):
        treatment = [
            {"result": "LOSS", "pnl_pct": -1, "size_usd": 100}
            for _ in range(50)
        ]
        control = [
            {"result": "WIN", "pnl_pct": 1, "size_usd": 100}
            for _ in range(50)
        ]
        activated = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        result = evaluate_experiment(treatment, control, activated)
        self.assertEqual(result["verdict"], "falsified")
        self.assertTrue(result["requires_user_action"])
        self.assertFalse(result["automatic_config_change"])

    def test_strategy_factory_requires_falsifiable_full_contract(self):
        incomplete = validate_strategy_factory_contract({
            "name": "Renamed clone",
            "base_key": "balanced",
            "api_payload": {"name": "Renamed clone", "base_key": "balanced"},
        })
        self.assertFalse(incomplete["valid"])
        self.assertIn("falsification_criteria", incomplete["missing"])

        complete = validate_strategy_factory_contract({
            "name": "Balanced high-vol filter",
            "base_key": "balanced",
            "hypothesis": "High-vol signals have stronger forward net EV.",
            "mechanism": "A volatility filter isolates the treatment cohort.",
            "entry_rules": {"allowed_volatility": ["high"]},
            "exit_rules": {"inherit_base": True},
            "failure_regimes": ["regime_shift"],
            "data_requirements": ["exact policy fingerprints"],
            "cost_assumptions": {"source": "paper_config"},
            "control_strategy": "balanced",
            "novelty_claim": "One declared admission filter.",
            "falsification_criteria": {"minimum_closed_trades": 50},
            "promotion_criteria": {"minimum_closed_trades": 50},
            "api_payload": {
                "name": "Balanced high-vol filter",
                "base_key": "balanced",
                "allowed_volatility": ["high"],
            },
        })
        self.assertTrue(complete["valid"])
        self.assertEqual(complete["authority"], "shadow_only")


class LearningRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = {
            "DB_PATH": mt7.DB_PATH,
            "PAPER_CONFIG_PATH": mt7.PAPER_CONFIG_PATH,
            "STRATEGY_OVERRIDES_PATH": mt7.STRATEGY_OVERRIDES_PATH,
        }
        mt7.DB_PATH = str(root / "learning.db")
        mt7.PAPER_CONFIG_PATH = str(root / "paper_config.json")
        mt7.STRATEGY_OVERRIDES_PATH = str(root / "strategy_overrides.json")
        Path(mt7.PAPER_CONFIG_PATH).write_text(
            json.dumps({"account_balance_usd": 200, "min_conviction": 55}),
            encoding="utf-8",
        )
        Path(mt7.STRATEGY_OVERRIDES_PATH).write_text("{}", encoding="utf-8")
        mt7.init_db()

    def tearDown(self):
        for key, value in self.old.items():
            setattr(mt7, key, value)
        self.tmp.cleanup()

    def _register(self):
        suggestion = {
            "id": "learn_threshold",
            "type": "threshold",
            "strategy": "balanced",
            "reasoning": "A stricter threshold should remove weak entries.",
            "mechanism": "Admission quality rises while scoring stays fixed.",
        }
        contract = {
            "scope": {"strategy": "balanced", "target_mode": "paper"},
            "change_set": [
                {"field": "min_conviction", "current": 55, "proposed": 65}
            ],
            "rollback_plan": {"action": "restore 55"},
            "application_policy": {"restricted": False},
        }
        activated = datetime.now(timezone.utc).isoformat()
        return mt7._learning_register_suggestion_experiment(
            suggestion,
            contract,
            {"applied_at": activated},
            activated,
        )

    def test_registry_hash_chain_detects_tampering(self):
        experiment = self._register()
        con = sqlite3.connect(mt7.DB_PATH)
        valid = mt7._learning_verify_event_chain(
            con, experiment["experiment_id"]
        )
        con.execute(
            """UPDATE learning_experiment_events
               SET payload_json='{"tampered":true}'
               WHERE experiment_id=? AND sequence=1""",
            (experiment["experiment_id"],),
        )
        con.commit()
        invalid = mt7._learning_verify_event_chain(
            con, experiment["experiment_id"]
        )
        con.close()
        self.assertTrue(valid["valid"])
        self.assertFalse(invalid["valid"])

    def test_trade_attribution_matches_active_policy(self):
        experiment = self._register()
        attribution = mt7._learning_attribution_for_trade(
            "balanced",
            mt7._load_paper_config(),
        )
        self.assertEqual(
            attribution["experiment_id"],
            experiment["experiment_id"],
        )
        self.assertEqual(
            attribution["policy_fingerprint"],
            experiment["policy_fingerprint"],
        )

    def test_same_strategy_cannot_run_overlapping_experiments(self):
        self._register()
        with self.assertRaisesRegex(ValueError, "active learning experiment"):
            self._register()


if __name__ == "__main__":
    unittest.main()
