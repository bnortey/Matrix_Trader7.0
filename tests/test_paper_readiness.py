import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import app as mt7


def synthetic_trades(values: list[float]) -> list[dict]:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return [
        {
            "id": index + 1,
            "symbol": "BTC_USDT",
            "strategy_key": "balanced_focus_short",
            "direction": "SHORT",
            "status": "closed",
            "result": "WIN" if value > 0 else "LOSS",
            "pnl_pct": value,
            "size_usd": 50.0,
            "closed_at": (start + timedelta(hours=index)).isoformat(),
        }
        for index, value in enumerate(values)
    ]


class PaperReadinessTests(unittest.TestCase):
    def test_outlier_dominated_profit_fails_robust_metrics(self):
        stats = mt7._paper_evidence_stats(
            synthetic_trades([100.0] + [-1.0] * 49),
            starting_balance=200.0,
        )

        self.assertGreater(stats["avg_pnl"], 0)
        self.assertGreater(stats["profit_factor"], 1.25)
        self.assertLess(stats["trimmed_avg_pnl"], 0)
        self.assertLess(stats["leave_best_out_total_pnl_usd"], 0)
        self.assertLess(stats["rolling_windows"]["20"]["avg_pnl"], 0)

    def test_broad_edge_passes_trimmed_recent_and_drawdown_metrics(self):
        stats = mt7._paper_evidence_stats(
            synthetic_trades(([2.0] * 3 + [-1.0] * 2) * 12),
            starting_balance=200.0,
        )

        self.assertEqual(stats["count"], 60)
        self.assertEqual(stats["win_partial_rate"], 60.0)
        self.assertGreaterEqual(stats["profit_factor"], 1.25)
        self.assertGreater(stats["trimmed_avg_pnl"], 0)
        self.assertGreater(stats["leave_best_out_total_pnl_usd"], 0)
        self.assertLessEqual(
            stats["max_drawdown_pct"],
            mt7.PAPER_READINESS_MAX_COHORT_DRAWDOWN_PCT,
        )
        for window in ("20", "50"):
            self.assertGreater(stats["rolling_windows"][window]["avg_pnl"], 0)
            self.assertGreaterEqual(
                stats["rolling_windows"][window]["profit_factor"],
                mt7.PAPER_READINESS_RECENT_PF_FLOOR,
            )

    def test_recent_decay_is_visible_despite_positive_full_sample(self):
        stats = mt7._paper_evidence_stats(
            synthetic_trades([3.0] * 40 + [-1.0] * 20),
            starting_balance=200.0,
        )

        self.assertGreater(stats["avg_pnl"], 0)
        self.assertGreater(stats["profit_factor"], 1.25)
        self.assertLess(stats["rolling_windows"]["20"]["avg_pnl"], 0)
        self.assertEqual(stats["rolling_windows"]["20"]["profit_factor"], 0.0)

    def test_cohort_review_requires_50_and_can_clear_all_robust_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "signals.db"
            con = sqlite3.connect(db_path)
            con.execute("""
                CREATE TABLE paper_trades (
                    id INTEGER PRIMARY KEY,
                    symbol TEXT,
                    strategy_key TEXT,
                    direction TEXT,
                    status TEXT,
                    result TEXT,
                    pnl_pct REAL,
                    size_usd REAL,
                    queued_at TEXT,
                    filled_at TEXT,
                    opened_at TEXT,
                    closed_at TEXT,
                    fee_cost_pct REAL,
                    slippage_cost_pct REAL,
                    flow_confirmed INTEGER,
                    flow_score REAL
                )
            """)
            trades = synthetic_trades(([2.0] * 3 + [-1.0] * 2) * 12)
            for trade in trades:
                con.execute("""
                    INSERT INTO paper_trades(
                        id, symbol, strategy_key, direction, status, result,
                        pnl_pct, size_usd, queued_at, filled_at, opened_at,
                        closed_at, fee_cost_pct, slippage_cost_pct,
                        flow_confirmed, flow_score
                    )
                    VALUES (?, ?, ?, ?, 'closed', ?, ?, ?, ?, ?, ?, ?, 0.1, 0.1, 1, 80)
                """, (
                    trade["id"],
                    trade["symbol"],
                    trade["strategy_key"],
                    trade["direction"],
                    trade["result"],
                    trade["pnl_pct"],
                    trade["size_usd"],
                    trade["closed_at"],
                    trade["closed_at"],
                    trade["closed_at"],
                    trade["closed_at"],
                ))
            con.commit()
            con.close()

            config = {
                "current_cohort_started_at": "2026-07-01T00:00:00+00:00",
                "current_cohort_target_count": 20,
                "current_cohort_label": "robust-test",
                "account_balance_usd": 200,
                "disabled_strategies": [],
            }
            goals = {
                **mt7.DEFAULT_GOALS,
                "evaluation_window_trades": 20,
            }
            with (
                patch.object(mt7, "DB_PATH", str(db_path)),
                patch.object(mt7, "_load_paper_config", return_value=config),
                patch.object(mt7, "_load_goals", return_value=goals),
                patch.object(
                    mt7,
                    "_hermes_research_pipeline_summary",
                    return_value={
                        "library": {"source_count": 0},
                        "shadow_experiment_count": 0,
                        "research_memo": {},
                    },
                ),
                patch.object(
                    mt7,
                    "get_strategy_registry",
                    return_value={"balanced_focus_short": {}},
                ),
            ):
                review = mt7._build_paper_cohort_review()

        self.assertEqual(review["configured_target_count"], 20)
        self.assertEqual(review["target_count"], 50)
        self.assertTrue(review["evidence_ready"])
        self.assertEqual(review["decision"], "ready_next_test")
        blocking = [gate for gate in review["gates"] if gate["blocking"]]
        self.assertTrue(all(gate["status"] == "pass" for gate in blocking))


if __name__ == "__main__":
    unittest.main()
