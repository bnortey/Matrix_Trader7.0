import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from edge_lab.meta_labeler import (
    AUTHORITY_MODE,
    MetaLabelConfig,
    _feature_before_trade,
    latest_meta_label_overview,
    run_meta_labeler,
    walk_forward_splits,
)
from edge_lab.storage import init_storage, upsert_feature_snapshots


class MetaLabelerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.signals_db = root / "signals.db"
        self.edge_db = root / "edge_lab.db"
        self._build_signals_db()
        self._build_edge_db()

    def tearDown(self):
        self.tmp.cleanup()

    def _build_signals_db(self):
        con = sqlite3.connect(self.signals_db)
        con.execute("""
            CREATE TABLE paper_trades (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                strategy_key TEXT,
                direction TEXT,
                status TEXT,
                result TEXT,
                pnl_pct REAL,
                conviction INTEGER,
                flow_confirmed INTEGER,
                flow_score REAL,
                atr_pct REAL,
                trend_score REAL,
                queued_at TEXT,
                filled_at TEXT,
                opened_at TEXT,
                closed_at TEXT
            )
        """)
        start = 1_750_000_000
        for index in range(180):
            trade_ts = start + (index * 15 * 60)
            strength = index % 10
            positive = strength >= 5
            iso = datetime.fromtimestamp(trade_ts, tz=timezone.utc).isoformat()
            con.execute("""
                INSERT INTO paper_trades(
                    id, symbol, strategy_key, direction, status, result, pnl_pct,
                    conviction, flow_confirmed, flow_score, atr_pct, trend_score,
                    queued_at, filled_at, opened_at, closed_at
                )
                VALUES (?, 'BTC_USDT', ?, ?, 'closed', ?, ?, ?, ?, ?, 2.0, ?,
                        ?, ?, ?, ?)
            """, (
                index + 1,
                "funding_arb" if index % 2 else "balanced",
                "SHORT" if index % 2 else "LONG",
                "WIN" if positive else "LOSS",
                2.0 if positive else -1.0,
                50 + strength * 5,
                int(strength >= 5),
                strength * 10,
                2 if positive else -2,
                iso,
                iso,
                iso,
                iso,
            ))
        for offset in range(2):
            trade_id = 181 + offset
            trade_ts = start + ((180 + offset) * 15 * 60)
            iso = datetime.fromtimestamp(trade_ts, tz=timezone.utc).isoformat()
            con.execute("""
                INSERT INTO paper_trades(
                    id, symbol, strategy_key, direction, status, result, pnl_pct,
                    conviction, flow_confirmed, flow_score, atr_pct, trend_score,
                    queued_at, filled_at, opened_at, closed_at
                )
                VALUES (?, 'BTC_USDT', 'funding_arb', 'SHORT', 'pending', NULL,
                        NULL, 85, 1, 80, 2.0, 2, ?, NULL, ?, NULL)
            """, (trade_id, iso, iso))
        con.commit()
        con.close()

    def _build_edge_db(self):
        con = sqlite3.connect(self.edge_db)
        init_storage(con)
        start = 1_750_000_000
        snapshots = []
        for index in range(182):
            strength = index % 10
            snapshots.append({
                "symbol": "BTC_USDT",
                "timeframe": "Min15",
                "timestamp": start + (index * 15 * 60) - (15 * 60),
                "features": {
                    "volatility_regime": "medium",
                    "trend_state": "bullish" if strength >= 5 else "bearish",
                    "compression_state": "normal",
                    "rsi_15m_decile": strength + 1,
                    "volume_decile": 5,
                    "atr_pct_15m_decile": 5,
                    "stddev_decile": 5,
                    "tags": ["bullish_trend"] if strength >= 5 else ["bearish_trend"],
                },
            })
        upsert_feature_snapshots(con, snapshots)
        con.commit()
        con.close()

    def test_walk_forward_splits_never_train_on_test_or_future_rows(self):
        splits = walk_forward_splits(180, 70, 40, 3)

        self.assertEqual(len(splits), 3)
        previous_end = None
        for train_start, test_start, test_end in splits:
            self.assertEqual(train_start, 0)
            self.assertLessEqual(70, test_start)
            self.assertLess(test_start, test_end)
            if previous_end is not None:
                self.assertEqual(previous_end, test_start)
            previous_end = test_end
        self.assertEqual(previous_end, 180)

    def test_feature_join_uses_only_a_fully_closed_candle(self):
        con = sqlite3.connect(self.edge_db)
        con.row_factory = sqlite3.Row
        trade_ts = 1_750_000_000
        feature, source, age = _feature_before_trade(
            con, "BTC_USDT", trade_ts, 30 * 60
        )
        con.close()

        self.assertEqual(source, "candle_feature_snapshots")
        self.assertEqual(feature["timestamp"], trade_ts - 15 * 60)
        self.assertEqual(age, 0)

    def test_end_to_end_run_is_calibrated_audited_and_zero_authority(self):
        result = run_meta_labeler(MetaLabelConfig(
            signals_db=self.signals_db,
            edge_db=self.edge_db,
            min_training_rows=70,
            min_test_rows=40,
            folds=3,
        ))

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["authority_mode"], AUTHORITY_MODE)
        self.assertFalse(result["authority_eligible"])
        self.assertEqual(result["coverage"]["coverage_pct"], 100.0)
        self.assertEqual(result["metrics"]["model"]["count"], 90)
        self.assertLess(
            result["metrics"]["model"]["brier"],
            result["metrics"]["no_filter_baseline"]["brier"],
        )
        self.assertEqual(len(result["active_scores"]), 2)
        self.assertEqual(result["forward_shadow"]["count"], 0)
        self.assertEqual(result["newly_evaluated_forward_scores"], 0)
        self.assertTrue(all(
            score["decision"] in {"shadow_allow", "shadow_block", "abstain"}
            for score in result["active_scores"]
        ))

        signals_con = sqlite3.connect(self.signals_db)
        signals_con.execute("""
            UPDATE paper_trades
            SET status='closed', result='WIN', pnl_pct=1.5, closed_at=opened_at
            WHERE id=181
        """)
        signals_con.commit()
        tables = {
            row[0]
            for row in signals_con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        count = signals_con.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
        signals_con.close()
        self.assertEqual(tables, {"paper_trades"})
        self.assertEqual(count, 182)

        second = run_meta_labeler(MetaLabelConfig(
            signals_db=self.signals_db,
            edge_db=self.edge_db,
            min_training_rows=70,
            min_test_rows=40,
            folds=3,
        ))
        self.assertEqual(second["newly_evaluated_forward_scores"], 1)
        self.assertEqual(second["forward_shadow"]["count"], 1)
        self.assertFalse(second["forward_shadow"]["ready"])
        self.assertEqual(second["active_scores"], [])

        edge_con = sqlite3.connect(self.edge_db)
        active_rows = edge_con.execute("""
            SELECT COUNT(*), SUM(CASE WHEN label IS NOT NULL THEN 1 ELSE 0 END)
            FROM meta_label_predictions
            WHERE split='active_shadow'
        """).fetchone()
        edge_con.close()
        self.assertEqual(active_rows, (2, 1))

        overview = latest_meta_label_overview(self.edge_db)
        self.assertTrue(overview["available"])
        self.assertFalse(overview["authority_eligible"])
        self.assertEqual(overview["authority_mode"], AUTHORITY_MODE)
        self.assertEqual(overview["forward_shadow"]["count"], 1)
        self.assertTrue(all(value is False for value in overview["safety"].values()))


if __name__ == "__main__":
    unittest.main()
