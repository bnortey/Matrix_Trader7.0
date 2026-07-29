import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app as mt7
from edge_lab.dataset_builder import EdgeLabConfig, _process_symbol
from edge_lab.storage import init_storage, upsert_feature_snapshots


def _candles(count: int = 180) -> pd.DataFrame:
    rows = []
    start = 1_750_000_000
    for index in range(count):
        wave = ((index % 17) - 8) * 0.15
        close = 100.0 + (index * 0.08) + wave
        rows.append({
            "timestamp": start + (index * 15 * 60),
            "open": close - 0.1,
            "high": close + 0.5 + ((index % 3) * 0.05),
            "low": close - 0.5 - ((index % 4) * 0.04),
            "close": close,
            "volume": 1_000 + ((index * 37) % 400),
        })
    return pd.DataFrame(rows)


def _summary() -> dict:
    return {
        "candles_fetched": 0,
        "partial_history_warnings": [],
        "symbols_skipped": 0,
        "rows_skipped_warmup": 0,
        "rows_skipped_no_future": 0,
        "rows_labeled": 0,
        "rows_inserted_updated": 0,
        "feature_rows_upserted": 0,
    }


class EdgeLabFeatureSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "edge_lab.db"
        self.con = sqlite3.connect(self.db_path)
        self.con.row_factory = sqlite3.Row
        init_storage(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_current_features_are_stored_without_waiting_for_future_labels(self):
        candles = _candles()
        config = EdgeLabConfig(
            mode="backfill",
            rolling_window=50,
            min_periods=30,
            forward_horizon_candles=12,
            batch_size=25,
        )
        summary = _summary()

        with patch(
            "edge_lab.dataset_builder.fetch_klines",
            return_value=(candles, {"partial_history": False}),
        ):
            _process_symbol(self.con, "BTC_USDT", config, summary)
        self.con.commit()

        snapshot = self.con.execute(
            "SELECT COUNT(*), MAX(timestamp) FROM candle_feature_snapshots"
        ).fetchone()
        labels = self.con.execute(
            "SELECT COUNT(*), MAX(timestamp) FROM candle_labels"
        ).fetchone()

        self.assertGreater(snapshot[0], labels[0])
        self.assertEqual(snapshot[1], int(candles.iloc[-1]["timestamp"]))
        self.assertLessEqual(labels[1], snapshot[1] - (12 * 15 * 60))
        self.assertEqual(summary["feature_rows_upserted"], snapshot[0])
        self.assertEqual(summary["rows_labeled"], labels[0])

    def test_matcher_reports_snapshot_staleness_and_missing_symbols(self):
        upsert_feature_snapshots(self.con, [{
            "symbol": "BTC_USDT",
            "timeframe": "Min15",
            "timestamp": 1_750_000_000,
            "features": {
                "volatility_regime": "medium",
                "trend_state": "bullish",
                "compression_state": "normal",
                "rsi_15m_decile": 7,
                "volume_decile": 6,
                "atr_pct_15m_decile": 5,
                "stddev_decile": 4,
                "tags": ["bullish_trend"],
            },
        }])
        self.con.commit()

        matched = mt7._edge_feature_match(
            self.con, "btc_usdt", 1_750_000_000 + 20 * 60, 30 * 60
        )
        stale = mt7._edge_feature_match(
            self.con, "BTC_USDT", 1_750_000_000 + 60 * 60, 30 * 60
        )
        missing = mt7._edge_feature_match(
            self.con, "NOT_REAL_USDT", 1_750_000_000, 30 * 60
        )

        self.assertTrue(matched["matched"])
        self.assertEqual(matched["coverage_reason"], "matched_snapshot")
        self.assertEqual(matched["feature"]["rsi_decile"], 7)
        self.assertEqual(matched["feature"]["tag_bullish_trend"], 1)
        self.assertFalse(stale["matched"])
        self.assertEqual(stale["coverage_reason"], "stale_feature")
        self.assertEqual(missing["coverage_reason"], "symbol_not_ingested")


if __name__ == "__main__":
    unittest.main()
