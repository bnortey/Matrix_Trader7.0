import json
import tempfile
import unittest
from pathlib import Path

import app as mt7


class LearnerAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_paths = {
            "STRATEGY_OVERRIDES_PATH": mt7.STRATEGY_OVERRIDES_PATH,
            "LEARNER_PENDING_PATH": mt7.LEARNER_PENDING_PATH,
            "LEARNER_REJECTED_PATH": mt7.LEARNER_REJECTED_PATH,
            "EXPERIMENT_LEDGER_PATH": mt7.EXPERIMENT_LEDGER_PATH,
        }
        mt7.STRATEGY_OVERRIDES_PATH = str(root / "strategy_overrides.json")
        mt7.LEARNER_PENDING_PATH = str(root / "pending.json")
        mt7.LEARNER_REJECTED_PATH = str(root / "rejected.json")
        mt7.EXPERIMENT_LEDGER_PATH = str(root / "experiment_ledger.json")
        mt7.app.config["TESTING"] = True

    def tearDown(self):
        for name, value in self.old_paths.items():
            setattr(mt7, name, value)
        self.tmp.cleanup()

    def _write_suggestions(self, suggestions):
        Path(mt7.LEARNER_PENDING_PATH).write_text(
            json.dumps({"suggestions": suggestions}),
            encoding="utf-8",
        )

    def _write_overrides(self, overrides):
        Path(mt7.STRATEGY_OVERRIDES_PATH).write_text(
            json.dumps(overrides),
            encoding="utf-8",
        )

    def test_reconcile_preserves_controls_and_assigns_one_legacy_owner(self):
        suggestions = [
            {
                "id": "balanced_owner",
                "type": "regime_suppress",
                "strategy": "balanced",
                "status": "shadow_evaluating",
                "regime": "low_liquidity",
                "applied_at": "2026-05-25T00:00:00",
                "api_payload": {"blocked_agent_regimes": ["low_liquidity"]},
            },
            {
                "id": "balanced_duplicate",
                "type": "regime_suppress",
                "strategy": "balanced",
                "status": "shadow_evaluating",
                "regime": "low_liquidity",
                "applied_at": "2026-06-12T00:00:00",
                "api_payload": {"blocked_agent_regimes": ["low_liquidity"]},
            },
            {
                "id": "funding_owner",
                "type": "regime_suppress",
                "strategy": "funding_arb",
                "status": "parked",
                "regime": "choppy",
                "applied_at": "2026-06-07T00:00:00",
                "api_payload": {"blocked_agent_regimes": ["choppy"]},
            },
            {
                "id": "pure_shadow",
                "type": "threshold",
                "strategy": "funding_arb",
                "status": "shadow_evaluating",
                "suggested_value": 81,
                "api_payload": {"min_conviction": 81},
            },
            {
                "id": "balanced_threshold",
                "type": "threshold",
                "strategy": "balanced",
                "status": "applied",
                "applied_at": "2026-05-23T00:00:00",
                "suggested_value": 60,
                "api_payload": {"min_conviction": 60},
            },
        ]
        overrides = {
            "balanced": {
                "min_conviction": 60,
                "blocked_agent_regimes": ["low_liquidity"],
            },
            "funding_arb": {
                "min_conviction": 69,
                "blocked_agent_regimes": ["choppy"],
            },
        }
        self._write_suggestions(suggestions)
        self._write_overrides(overrides)

        result = mt7._reconcile_legacy_suggestion_authority()

        self.assertEqual(result["changed_count"], 3)
        self.assertEqual(json.loads(Path(mt7.STRATEGY_OVERRIDES_PATH).read_text()), overrides)
        reconciled = {
            item["id"]: item
            for item in json.loads(Path(mt7.LEARNER_PENDING_PATH).read_text())["suggestions"]
        }
        self.assertEqual(reconciled["balanced_owner"]["status"], "applied")
        self.assertEqual(reconciled["balanced_duplicate"]["status"], "superseded")
        self.assertEqual(
            reconciled["balanced_duplicate"]["superseded_by"],
            "balanced_owner",
        )
        self.assertEqual(reconciled["funding_owner"]["status"], "applied")
        self.assertEqual(reconciled["pure_shadow"]["status"], "shadow_evaluating")

        annotated = mt7._annotate_suggestion_authority(list(reconciled.values()))
        by_id = {item["id"]: item for item in annotated}
        self.assertFalse(any(item["state_conflict"] for item in annotated))
        self.assertEqual(
            by_id["balanced_owner"]["control_authority"]["mode"],
            "applied_config",
        )
        self.assertEqual(
            by_id["pure_shadow"]["control_authority"]["mode"],
            "shadow_read_only",
        )
        audit = json.loads(Path(mt7.EXPERIMENT_LEDGER_PATH).read_text())
        self.assertEqual(audit[-1]["type"], "suggestion_authority_reconciled")
        self.assertFalse(audit[-1]["scan_behavior_changed"])

    def test_parking_applied_trial_rolls_back_only_its_exact_control(self):
        self._write_suggestions([
            {
                "id": "regime_trial",
                "type": "regime_suppress",
                "strategy": "balanced",
                "status": "evaluating",
                "regime": "low_liquidity",
                "applied_at": "2026-07-01T00:00:00",
                "api_payload": {"blocked_agent_regimes": ["low_liquidity"]},
            }
        ])
        self._write_overrides({
            "balanced": {
                "min_conviction": 60,
                "blocked_agent_regimes": ["low_liquidity", "choppy"],
            }
        })

        with mt7.app.test_client() as client:
            response = client.post(
                "/api/intelligence/suggestions/regime_trial/park",
                json={"reason": "pause trial"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["control_rollback"]["changed"])
        overrides = json.loads(Path(mt7.STRATEGY_OVERRIDES_PATH).read_text())
        self.assertEqual(overrides["balanced"]["min_conviction"], 60)
        self.assertEqual(overrides["balanced"]["blocked_agent_regimes"], ["choppy"])
        suggestion = json.loads(Path(mt7.LEARNER_PENDING_PATH).read_text())["suggestions"][0]
        self.assertEqual(suggestion["status"], "parked")
        self.assertTrue(suggestion["control_rolled_back"])

    def test_shadow_resume_refuses_matching_active_override(self):
        self._write_suggestions([
            {
                "id": "parked_trial",
                "type": "regime_suppress",
                "strategy": "funding_arb",
                "status": "parked",
                "regime": "choppy",
                "api_payload": {"blocked_agent_regimes": ["choppy"]},
            }
        ])
        self._write_overrides({
            "funding_arb": {"blocked_agent_regimes": ["choppy"]}
        })

        with mt7.app.test_client() as client:
            response = client.post(
                "/api/intelligence/suggestions/parked_trial/resume"
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Cannot resume as shadow", response.get_json()["error"])
        suggestion = json.loads(Path(mt7.LEARNER_PENDING_PATH).read_text())["suggestions"][0]
        self.assertEqual(suggestion["status"], "parked")


if __name__ == "__main__":
    unittest.main()
