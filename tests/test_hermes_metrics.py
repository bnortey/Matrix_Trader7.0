import sqlite3
import unittest
import re
from datetime import datetime, timedelta
from unittest.mock import patch

import app as mt7
from scripts.hermes_memo_integrity import (
    has_linked_sample_mislabel,
    inject_authoritative_snapshot,
)


class HermesMetricContractTests(unittest.TestCase):
    def test_authoritative_snapshot_preserves_zero_cohort_progress(self):
        packet = {
            "audit": {
                "paper": {"closed": 308},
                "readiness": {
                    "current_cohort_sample_n": 0,
                    "current_cohort_target_n": 50,
                },
                "goal_actuals": {
                    "paper_ev_sample_n": 17,
                    "current_value_usd": 286.44,
                    "total_pnl_usd": 86.44,
                },
            }
        }

        memo = inject_authoritative_snapshot(
            "# HERMES ADVISORY MEMO\n\nGenerated analysis.",
            packet,
        )

        self.assertIn("All-time Paper simulated sample: 308", memo)
        self.assertIn("Current policy cohort progress: 0/50", memo)
        self.assertIn("Current simulated Paper equity: $286.44", memo)
        self.assertIsNotNone(re.search(r"\b0\b[\s\S]{0,100}\b50\b", memo))
        self.assertEqual(
            inject_authoritative_snapshot(memo, packet).count(
                "## Authoritative MT7 Snapshot"
            ),
            1,
        )

    def test_authoritative_snapshot_normalizes_provider_preamble_and_heading(self):
        memo = inject_authoritative_snapshot(
            "Here is the Hermes Advisory Memo.\n\n---\n\n"
            "## Hermes Advisory Group — Memo 2026-07-31\n\nAnalysis.",
            {"audit": {}},
        )

        self.assertTrue(memo.startswith("# Hermes Advisory Memo\n\nAnalysis."))
        self.assertNotIn("Here is the Hermes", memo)
        self.assertEqual(memo.count("Hermes Advisory Memo"), 1)

    def test_authoritative_snapshot_extracts_last_complete_provider_memo(self):
        memo = inject_authoritative_snapshot(
            "┊ review diff\n-# Hermes Advisory Memo — old\n-old text\n"
            "The memo has been written. Here is the complete advisory:\n\n"
            "# MT7 Hermes Advisory Memo — 2026-07-31\n\nFinal report.",
            {"audit": {}},
        )

        self.assertTrue(memo.startswith("# Hermes Advisory Memo\n\nFinal report."))
        self.assertNotIn("review diff", memo)
        self.assertNotIn("old text", memo)

    def test_authoritative_snapshot_keeps_linked_sample_label_unambiguous(self):
        memo = inject_authoritative_snapshot(
            "# Hermes Advisory Memo\n\nAnalysis.",
            {
                "audit": {
                    "paper": {"closed": 308},
                    "goal_actuals": {"paper_ev_sample_n": 203},
                }
            },
        )

        snapshot = memo.split("## Authoritative MT7 Snapshot", 1)[1]
        self.assertIn("30-day linked-signal Paper sample: 203 outcomes.", snapshot)
        self.assertNotIn("203 outcomes; this is not the all-time", snapshot)

    def test_linked_sample_validator_allows_a_correct_all_time_comparison(self):
        line = (
            "30-day linked-signal Paper sample (n=203): EV is lower but "
            "directionally consistent with the all-time figure."
        )
        self.assertFalse(has_linked_sample_mislabel(line, 203, 308))

    def test_linked_sample_validator_rejects_an_all_time_mislabel(self):
        self.assertTrue(
            has_linked_sample_mislabel(
                "All-time Paper sample: 203 closed trades.",
                203,
                308,
            )
        )

    def test_authoritative_snapshot_repairs_linked_sample_model_mislabel(self):
        memo = inject_authoritative_snapshot(
            "# Hermes Advisory Memo\n\n"
            "**All-time Paper EV** (paper_ev_sample_n = 54): historical average.\n"
            "Do not substitute the 30-day linked-signal Paper sample (n=54).",
            {
                "audit": {
                    "paper": {"closed": 308},
                    "goal_actuals": {"paper_ev_sample_n": 203},
                }
            },
        )

        self.assertIn("30-day linked-signal Paper sample (n=203)", memo)
        self.assertIn("308-trade all-time Paper record", memo)
        self.assertNotIn("All-time Paper EV", memo)
        self.assertNotIn("paper_ev_sample_n = 54", memo)
        self.assertNotIn("linked-signal Paper sample (n=54)", memo)
        self.assertFalse(has_linked_sample_mislabel(memo, 203, 308))

    def test_authoritative_snapshot_binds_stale_control_warning(self):
        packet = {
            "audit": {
                "suggestions": {
                    "baseline_conflict_ids": ["stale-threshold"],
                    "active": [{
                        "id": "stale-threshold",
                        "current_value": 60,
                        "suggested_value": 81,
                        "control_authority": {"runtime_actual": 69},
                    }],
                }
            }
        }

        memo = inject_authoritative_snapshot(
            "# HERMES ADVISORY MEMO\n\nGenerated analysis.",
            packet,
        )

        self.assertIn("stale-threshold", memo)
        self.assertIn("proposal_current=60", memo)
        self.assertIn("runtime_actual=69", memo)
        self.assertIn("suggested=81", memo)
        self.assertIn("Do not apply this stale proposal", memo)

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
