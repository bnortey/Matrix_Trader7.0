import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.ai_benchmark import (
    BENCHMARK_CASES,
    benchmark_overview,
    create_benchmark_run,
    ensure_benchmark_tables,
    execute_benchmark_run,
    record_promotion,
    score_response,
)
from lib.ai_client import AIResult


class AIBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "signals.db"

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def perfect_response(case):
        return json.dumps({
            "verdict": case["verdict"],
            "confidence": case["confidence_range"][0],
            "risk_flags": case["required_flags"],
            "facts": case["required_facts"],
            "summary": "The deterministic evidence supports this control verdict.",
        })

    def test_perfect_structured_response_scores_100(self):
        case = BENCHMARK_CASES["signal_agents"][0]
        scored = score_response(self.perfect_response(case), case)

        self.assertEqual(scored["total_score"], 100)
        self.assertEqual(scored["format_score"], 20)
        self.assertEqual(scored["correctness_score"], 40)
        self.assertEqual(scored["risk_score"], 30)
        self.assertEqual(scored["calibration_score"], 10)

    def test_invalid_json_scores_zero_and_forbidden_action_loses_risk_points(self):
        case = BENCHMARK_CASES["signal_agents"][0]
        invalid = score_response("BLOCK because it is stale.", case)
        unsafe_payload = json.loads(self.perfect_response(case))
        unsafe_payload["summary"] = "Execute the order now."
        unsafe = score_response(json.dumps(unsafe_payload), case)

        self.assertEqual(invalid["total_score"], 0)
        self.assertEqual(unsafe["risk_score"], 20)
        self.assertEqual(unsafe["total_score"], 90)

    def test_completed_run_recommends_only_model_that_clears_all_gates(self):
        feature = "strategy_analysis"
        candidates = [
            {"provider": "groq", "model": "good-model"},
            {"provider": "deepseek", "model": "bad-model"},
        ]
        run_id = create_benchmark_run(self.db_path, feature, candidates)

        def fake_call(system, user, *args, **kwargs):
            model = kwargs["model"]
            case = next(item for item in BENCHMARK_CASES[feature] if item["prompt"] == user)
            text = self.perfect_response(case) if model == "good-model" else "not json"
            return AIResult(
                text=text,
                provider=kwargs["provider"],
                model=model,
                feature=kwargs["feature"],
                latency_ms=500 if model == "good-model" else 200,
                fallback_used=False,
                attempts=1,
                called_at="2026-07-27T00:00:00+00:00",
            )

        with patch("lib.ai_benchmark.call_ai", side_effect=fake_call):
            execute_benchmark_run(self.db_path, run_id, feature, candidates)

        overview = benchmark_overview(self.db_path)
        run = overview["runs"][0]
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["suite_version"], "mt7_static_v1")
        self.assertEqual(run["completed_models"], 2)
        self.assertEqual(
            run["champion"],
            {"provider": "groq", "model": "good-model"},
        )
        good = next(item for item in run["models"] if item["model"] == "good-model")
        bad = next(item for item in run["models"] if item["model"] == "bad-model")
        self.assertEqual(good["status"], "champion")
        self.assertTrue(good["eligible"])
        self.assertEqual(bad["status"], "needs_work")
        self.assertFalse(bad["eligible"])

    def test_run_stores_scores_not_response_text(self):
        ensure_benchmark_tables(self.db_path)
        con = sqlite3.connect(str(self.db_path))
        try:
            columns = {
                row[1]
                for row in con.execute(
                    "PRAGMA table_info(ai_benchmark_results)"
                ).fetchall()
            }
        finally:
            con.close()

        self.assertNotIn("response", columns)
        self.assertNotIn("prompt", columns)
        self.assertIn("total_score", columns)
        self.assertIn("risk_score", columns)

    def test_promotion_audit_records_old_and_new_routes(self):
        ensure_benchmark_tables(self.db_path)
        record_promotion(
            self.db_path,
            run_id=7,
            feature="coach_review",
            old_route={"provider": "deepseek", "model": "old"},
            new_route={"provider": "groq", "model": "new"},
        )
        con = sqlite3.connect(str(self.db_path))
        try:
            row = con.execute(
                """
                SELECT run_id, feature, old_provider, old_model, new_provider, new_model
                FROM ai_benchmark_promotions
                """
            ).fetchone()
        finally:
            con.close()

        self.assertEqual(row, (7, "coach_review", "deepseek", "old", "groq", "new"))


if __name__ == "__main__":
    unittest.main()
