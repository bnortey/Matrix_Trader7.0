import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import app as mt7


class HermesMetricContractTests(unittest.TestCase):
    def test_signal_audit_separates_live_shadow_and_recent_windows(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("""
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                logged_at TEXT,
                result_at TEXT,
                symbol TEXT,
                direction TEXT,
                strategy_key TEXT,
                source TEXT,
                result TEXT,
                pnl_pct REAL,
                signal_json TEXT
            )
        """)
        recent = datetime.utcnow().isoformat()
        old = (datetime.utcnow() - timedelta(days=45)).isoformat()
        rows = [
            (recent, recent, "BTC_USDT", "LONG", "balanced", "live", "WIN", 1.0),
            (recent, recent, "BTC_USDT", "LONG", "balanced", "live", "WIN", 2.0),
            (recent, recent, "BTC_USDT", "LONG", "balanced", "live", "LOSS", 3.0),
            (old, old, "OLD_USDT", "LONG", "balanced", "live", "LOSS", -900.0),
            (recent, recent, "SHADOW_USDT", "SHORT", "momentum_breakout", "disabled_shadow", "LOSS", -100.0),
            (recent, recent, "SHADOW_USDT", "SHORT", "momentum_breakout", "disabled_shadow", "LOSS", -100.0),
            (recent, recent, "SHADOW_USDT", "SHORT", "momentum_breakout", "disabled_shadow", "LOSS", -100.0),
            (recent, recent, "PAPER_USDT", "SHORT", "funding_arb", "paper", "LOSS", -500.0),
        ]
        con.executemany("""
            INSERT INTO signals(
                logged_at, result_at, symbol, direction, strategy_key,
                source, result, pnl_pct, signal_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}')
        """, rows)
        con.commit()

        audit = mt7._hermes_signal_audit(con)
        con.close()

        self.assertEqual(audit["all_time"]["closed"], 4)
        self.assertEqual(audit["recent_30d"]["closed"], 3)
        self.assertEqual(audit["dataset"]["source_lane"], "live")
        self.assertEqual(audit["strategies"][0]["n"], 3)
        self.assertEqual(audit["strategies"][0]["total_pnl_pct"], 6.0)
        self.assertNotIn("total_pnl", audit["strategies"][0])
        self.assertEqual(audit["worst_symbols"][0]["total_unit"], "percentage_points")
        self.assertEqual(
            audit["disabled_shadow"]["recent_30d"]["closed"],
            3,
        )
        self.assertEqual(
            audit["disabled_shadow"]["strategies"][0]["total_pnl_pct"],
            -300.0,
        )

    def test_paper_audit_uses_size_weighted_usd_and_isolated_cohort(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("""
            CREATE TABLE paper_trades (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                strategy_key TEXT,
                result TEXT,
                pnl_pct REAL,
                gross_pnl_pct REAL,
                fee_cost_pct REAL,
                slippage_cost_pct REAL,
                atr_pct REAL,
                trend_score REAL,
                status TEXT,
                size_usd REAL,
                closed_at TEXT
            )
        """)
        recent = datetime.utcnow().isoformat()
        old = (datetime.utcnow() - timedelta(days=10)).isoformat()
        con.executemany("""
            INSERT INTO paper_trades(
                symbol, strategy_key, result, pnl_pct, gross_pnl_pct,
                fee_cost_pct, slippage_cost_pct, atr_pct, trend_score,
                status, size_usd, closed_at
            )
            VALUES (?, 'balanced', 'LOSS', ?, ?, 0.1, 0.2, 2.0, 10, 'closed', ?, ?)
        """, [
            ("KAT_USDT", -20.0, -19.7, 50.0, old),
            ("OGN_USDT", -50.0, -49.7, 20.0, recent),
        ])
        con.execute("""
            INSERT INTO paper_trades(
                symbol, strategy_key, status, size_usd, closed_at
            )
            VALUES ('BTC_USDT', 'balanced', 'pending', 50.0, NULL)
        """)
        con.commit()

        with patch.object(
            mt7,
            "_load_paper_config",
            return_value={
                "current_cohort_label": "test-cohort",
                "current_cohort_started_at": (
                    datetime.utcnow() - timedelta(days=1)
                ).isoformat(),
            },
        ):
            audit = mt7._hermes_paper_audit(con)
        con.close()

        self.assertEqual(audit["total_pnl_pct"], -70.0)
        self.assertEqual(audit["total_pnl_usd"], -20.0)
        self.assertEqual(audit["pending"], 1)
        self.assertEqual(audit["current_policy_cohort"]["closed"], 1)
        self.assertEqual(
            audit["current_policy_cohort"]["total_pnl_usd"],
            -10.0,
        )
        self.assertEqual(
            audit["worst_symbols_by_usd"][0]["total_unit"],
            "USD",
        )

    def test_threshold_proposal_flags_stale_runtime_baseline(self):
        suggestion = {
            "id": "funding-threshold",
            "type": "threshold",
            "strategy": "funding_arb",
            "status": "pending_review",
            "current_value": 60,
            "suggested_value": 81,
            "api_payload": {"min_conviction": 81},
        }
        with patch.object(
            mt7,
            "_load_strategy_overrides",
            return_value={"funding_arb": {"min_conviction": 69}},
        ):
            annotated = mt7._annotate_suggestion_authority([suggestion])[0]

        self.assertEqual(
            annotated["control_authority"]["runtime_actual"],
            69,
        )
        self.assertFalse(
            annotated["control_authority"]["proposal_baseline_consistent"]
        )
        self.assertTrue(annotated["baseline_conflict"])


if __name__ == "__main__":
    unittest.main()
