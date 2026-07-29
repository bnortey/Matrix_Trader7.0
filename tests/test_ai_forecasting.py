import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from lib.ai_client import AIResult
from lib.ai_forecasting import (
    FORECAST_VERSION,
    classify_return,
    collect_forecasts_once,
    evaluate_due_forecasts,
    forecast_overview,
    multiclass_brier,
    parse_forecast_response,
    signal_baseline_probabilities,
)


class AIShadowForecastTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "signals.db"
        con = sqlite3.connect(str(self.db_path))
        con.execute("""
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT,
                direction TEXT,
                price REAL,
                conviction INTEGER,
                strategy_key TEXT,
                atr_pct REAL,
                volatility TEXT,
                funding_rate REAL,
                rsi_1h REAL,
                trend_score REAL,
                flow_score REAL,
                tags TEXT,
                signal_json TEXT,
                data_quality TEXT,
                source TEXT
            )
        """)
        con.execute(
            """
            INSERT INTO signals
                (logged_at, symbol, exchange, direction, price, conviction,
                 strategy_key, atr_pct, volatility, funding_rate, rsi_1h,
                 trend_score, flow_score, tags, signal_json, data_quality, source)
            VALUES (?, 'BTC_USDT', 'MEXC', 'LONG', 100, 82, 'balanced',
                    2.1, 'medium', -0.0001, 58, 2, 65, 'momentum',
                    '{"agent_regime":"trending","change_24h_pct":2.4}',
                    'current', 'live')
            """,
            ((datetime.utcnow() - timedelta(minutes=1)).isoformat(),),
        )
        con.commit()
        con.close()
        self.settings = {
            "shadow_forecasting_enabled": True,
            "shadow_forecast_min_conviction": 70,
            "shadow_forecast_daily_call_cap": 12,
            "shadow_forecast_target": 20,
            "shadow_forecast_models": [
                {"provider": "groq", "model": "test-model", "role": "champion"},
            ],
        }

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def valid_response():
        return json.dumps({
            "abstain": False,
            "risk_flags": ["volatility"],
            "drivers": ["momentum", "funding"],
            "horizons": {
                "15": {"p_up": 0.60, "p_flat": 0.25, "p_down": 0.15},
                "60": {"p_up": 0.55, "p_flat": 0.25, "p_down": 0.20},
                "240": {"p_up": 0.50, "p_flat": 0.25, "p_down": 0.25},
            },
        })

    def test_probability_parser_and_math_helpers(self):
        parsed = parse_forecast_response(self.valid_response())
        invalid = parse_forecast_response(json.dumps({
            "horizons": {
                "15": {"p_up": 0.8, "p_flat": 0.8, "p_down": 0.1},
                "60": {"p_up": 0.4, "p_flat": 0.3, "p_down": 0.3},
                "240": {"p_up": 0.4, "p_flat": 0.3, "p_down": 0.3},
            }
        }))

        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["horizons"][15]["predicted_class"], "UP")
        self.assertFalse(invalid["valid"])
        self.assertEqual(classify_return(0.2, 0.15), "UP")
        self.assertEqual(classify_return(-0.2, 0.15), "DOWN")
        self.assertEqual(classify_return(0.1, 0.15), "FLAT")
        self.assertAlmostEqual(
            multiclass_brier({"UP": 0.6, "FLAT": 0.25, "DOWN": 0.15}, "UP"),
            0.245,
        )
        baseline = signal_baseline_probabilities("LONG", 75)
        self.assertGreater(baseline["UP"], baseline["DOWN"])
        self.assertAlmostEqual(sum(baseline.values()), 1)

    def test_collection_persists_structured_scores_not_raw_response(self):
        def fake_call(*args, **kwargs):
            return AIResult(
                text=self.valid_response(),
                provider=kwargs["provider"],
                model=kwargs["model"],
                feature=kwargs["feature"],
                latency_ms=321,
                fallback_used=False,
                attempts=1,
                called_at=datetime.now(timezone.utc).isoformat(),
            )

        status = [{
            "provider": "groq",
            "available": True,
            "circuit": {"state": "closed"},
        }]
        with patch("lib.ai_forecasting.provider_status", return_value=status), patch(
            "lib.ai_forecasting.call_ai", side_effect=fake_call
        ):
            result = collect_forecasts_once(self.db_path, self.settings)

        self.assertEqual(result["completed"], 1)
        con = sqlite3.connect(str(self.db_path))
        try:
            forecast = con.execute(
                """
                SELECT status, response_valid, latency_ms, forecast_version
                FROM ai_shadow_forecasts
                """
            ).fetchone()
            horizons = con.execute(
                "SELECT COUNT(*) FROM ai_shadow_forecast_horizons"
            ).fetchone()[0]
            columns = {
                row[1]
                for row in con.execute(
                    "PRAGMA table_info(ai_shadow_forecasts)"
                ).fetchall()
            }
        finally:
            con.close()

        self.assertEqual(forecast, ("complete", 1, 321, FORECAST_VERSION))
        self.assertEqual(horizons, 3)
        self.assertNotIn("response", columns)
        self.assertNotIn("prompt", columns)

    def test_due_evaluation_scores_model_and_both_baselines(self):
        status = [{
            "provider": "groq",
            "available": True,
            "circuit": {"state": "closed"},
        }]
        fake_result = AIResult(
            text=self.valid_response(),
            provider="groq",
            model="test-model",
            feature="shadow_forecast",
            latency_ms=100,
            fallback_used=False,
            attempts=1,
            called_at=datetime.now(timezone.utc).isoformat(),
        )
        with patch("lib.ai_forecasting.provider_status", return_value=status), patch(
            "lib.ai_forecasting.call_ai", return_value=fake_result
        ):
            collect_forecasts_once(self.db_path, self.settings)

        con = sqlite3.connect(str(self.db_path))
        con.execute(
            "UPDATE ai_shadow_forecast_horizons SET due_at=?",
            ((datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),),
        )
        con.commit()
        con.close()

        evaluation = evaluate_due_forecasts(
            self.db_path,
            lambda row, target_ts: (
                101.0,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        overview = forecast_overview(self.db_path, self.settings)

        self.assertEqual(evaluation["evaluated"], 3)
        con = sqlite3.connect(str(self.db_path))
        try:
            row = con.execute(
                """
                SELECT actual_class, brier_score, flat_baseline_brier,
                       signal_baseline_brier, model_net_return_pct
                FROM ai_shadow_forecast_horizons
                WHERE horizon_minutes=15
                """
            ).fetchone()
        finally:
            con.close()

        self.assertEqual(row[0], "UP")
        self.assertIsNotNone(row[1])
        self.assertIsNotNone(row[2])
        self.assertIsNotNone(row[3])
        self.assertAlmostEqual(row[4], 0.88)
        self.assertEqual(overview["total_forecasts"], 1)
        self.assertEqual(overview["total_evaluated_horizons"], 3)
        self.assertEqual(overview["models"][0]["status"], "collecting")

    def test_daily_cap_prevents_additional_calls(self):
        limited = {
            **self.settings,
            "shadow_forecast_daily_call_cap": 1,
        }
        status = [{
            "provider": "groq",
            "available": True,
            "circuit": {"state": "closed"},
        }]
        fake_result = AIResult(
            text=self.valid_response(),
            provider="groq",
            model="test-model",
            feature="shadow_forecast",
            latency_ms=100,
            fallback_used=False,
            attempts=1,
            called_at=datetime.now(timezone.utc).isoformat(),
        )
        with patch("lib.ai_forecasting.provider_status", return_value=status), patch(
            "lib.ai_forecasting.call_ai", return_value=fake_result
        ) as mocked:
            first = collect_forecasts_once(self.db_path, limited)
            second = collect_forecasts_once(self.db_path, limited)

        self.assertEqual(first["completed"], 1)
        self.assertTrue(second["daily_cap_reached"])
        self.assertEqual(mocked.call_count, 1)


if __name__ == "__main__":
    unittest.main()
