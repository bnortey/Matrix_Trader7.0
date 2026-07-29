import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from edge_lab.factor_report import run_factor_analysis
from edge_lab.feature_engine import FEATURE_VERSION
from edge_lab.materializer import MATERIALIZER_VERSION, materialize
from edge_lab.meta_labeler_v2 import _evaluate
from edge_lab.path_labeler import label_paths_from_arrays
from edge_lab.storage import init_storage, insert_labels
from edge_lab.strategy_evaluator import evaluate_strategy_economics


class EdgeLabPathV2Tests(unittest.TestCase):
    def test_excursions_stop_at_exit_and_horizon_has_realized_return(self):
        closes = np.asarray([100.0, 100.2, 90.0, 90.0])
        highs = np.asarray([100.0, 100.6, 100.3, 100.1])
        lows = np.asarray([100.0, 99.8, 89.0, 89.0])
        paths = label_paths_from_arrays(
            highs, lows, closes, 0, forward_horizon_candles=3
        )
        row = paths["TP0_5_SL0_5"]
        self.assertTrue(row["long_tp_hit_first"])
        self.assertEqual(row["long_exit_type"], "tp")
        self.assertEqual(row["long_gross_pnl_pct"], 0.5)
        self.assertAlmostEqual(row["long_mfe_pct"], 0.6, places=6)
        self.assertAlmostEqual(row["long_mae_pct"], -0.2, places=6)

        flat_closes = np.asarray([100.0, 100.1, 100.15, 100.2])
        flat_highs = np.asarray([100.0, 100.2, 100.3, 100.4])
        flat_lows = np.asarray([100.0, 99.9, 99.8, 99.7])
        horizon = label_paths_from_arrays(
            flat_highs, flat_lows, flat_closes, 0, forward_horizon_candles=3
        )["TP0_5_SL0_5"]
        self.assertTrue(horizon["long_neither_hit"])
        self.assertEqual(horizon["long_exit_type"], "horizon")
        self.assertAlmostEqual(horizon["long_gross_pnl_pct"], 0.2, places=6)

    def test_ambiguous_paths_are_bounded_not_forced_to_win_or_loss(self):
        closes = np.asarray([100.0, 100.0])
        highs = np.asarray([100.0, 100.6])
        lows = np.asarray([100.0, 99.4])
        row = label_paths_from_arrays(
            highs, lows, closes, 0, forward_horizon_candles=1
        )["TP0_5_SL0_5"]
        self.assertTrue(row["long_ambiguous_hit"])
        self.assertIsNone(row["long_gross_pnl_pct"])
        self.assertEqual(row["long_ambiguity_pnl_low_pct"], -0.5)
        self.assertEqual(row["long_ambiguity_pnl_high_pct"], 0.5)


class EdgeLabMaterializerV2Tests(unittest.TestCase):
    def test_changed_source_rows_are_rematerialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "edge.db"
            con = sqlite3.connect(db)
            init_storage(con)
            paths = label_paths_from_arrays(
                np.asarray([100.0, 100.6]),
                np.asarray([100.0, 99.9]),
                np.asarray([100.0, 100.5]),
                0,
                forward_horizon_candles=1,
            )
            feature = {
                "feature_version": FEATURE_VERSION,
                "volatility_regime": "low",
                "trend_state": "bullish",
                "compression_state": "normal",
                "rsi_15m_decile": 5,
                "volume_decile": 5,
                "atr_pct_15m_decile": 5,
                "stddev_decile": 5,
                "tags": ["low_vol", "bullish_trend"],
            }
            insert_labels(con, [{
                "symbol": "TEST_USDT", "timeframe": "Min15", "timestamp": 1,
                "features": feature, "paths": paths,
            }])
            con.commit()
            con.close()
            first = materialize(db, batch_size=10)
            self.assertEqual(first["rows_upserted"], 1)

            con = sqlite3.connect(db)
            row = con.execute("""
                SELECT t05_l_tp, t05_l_gross, materializer_version
                FROM candle_features
            """).fetchone()
            self.assertEqual(row, (1, 0.5, MATERIALIZER_VERSION))
            feature["trend_state"] = "neutral"
            insert_labels(con, [{
                "symbol": "TEST_USDT", "timeframe": "Min15", "timestamp": 1,
                "features": feature, "paths": paths,
            }])
            con.commit()
            con.close()
            second = materialize(db, batch_size=10)
            self.assertEqual(second["rows_upserted"], 1)
            con = sqlite3.connect(db)
            self.assertEqual(
                con.execute("SELECT trend_state FROM candle_features").fetchone()[0],
                "neutral",
            )
            con.close()

    def test_legacy_rows_wait_for_bounded_version_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "edge.db"
            con = sqlite3.connect(db)
            init_storage(con)
            con.execute(
                """
                INSERT INTO candle_labels(
                    symbol, exchange, timeframe, timestamp,
                    features_json, paths_json, feature_version,
                    label_version, generated_at
                ) VALUES (?, 'MEXC', 'Min15', ?, '{}', '{}', '', '', ?)
                """,
                ("LEGACY_USDT", 1, "legacy"),
            )
            con.commit()
            con.close()

            summary = materialize(db, batch_size=10)

            self.assertEqual(summary["source_rows"], 1)
            self.assertEqual(summary["eligible_source_rows"], 0)
            self.assertEqual(summary["rows_upserted"], 0)
            con = sqlite3.connect(db)
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM candle_features").fetchone()[0],
                0,
            )
            con.close()


class EdgeLabFactorV2Tests(unittest.TestCase):
    def test_report_uses_dynamic_baseline_effective_n_and_net_economics(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "edge.db"
            con = sqlite3.connect(db)
            con.execute("""
                CREATE TABLE candle_features(
                    candle_id INTEGER PRIMARY KEY, symbol TEXT, timeframe TEXT,
                    timestamp INTEGER, volatility_regime TEXT, trend_state TEXT,
                    compression_state TEXT, rsi_decile INTEGER,
                    volume_decile INTEGER, atr_decile INTEGER, stddev_decile INTEGER,
                    tag_compressed INTEGER, tag_expanded INTEGER,
                    tag_bullish_trend INTEGER, tag_bearish_trend INTEGER,
                    tag_extreme_vol INTEGER, tag_low_vol INTEGER,
                    feature_version TEXT, label_version TEXT, materializer_version TEXT,
                    t05_l_tp INTEGER, t05_l_sl INTEGER, t05_l_neither INTEGER,
                    t05_l_ambig INTEGER, t05_l_mfe REAL, t05_l_mae REAL,
                    t05_l_ttp REAL, t05_l_gross REAL,
                    t05_s_tp INTEGER, t05_s_sl INTEGER, t05_s_neither INTEGER,
                    t05_s_ambig INTEGER, t05_s_mfe REAL, t05_s_mae REAL,
                    t05_s_ttp REAL, t05_s_gross REAL
                )
            """)
            payload = []
            for index in range(120):
                favored = index < 60
                win = index % 10 < (8 if favored else 4)
                payload.append((
                    index, f"S{index % 4}_USDT", "Min15",
                    1_700_000_000 + index * 900,
                    "low" if favored else "medium", "bullish", "normal",
                    5, 5, 5, 5, 0, 0, 1, 0, 0, 1,
                    "edge_features_v2", "edge_path_v2", "edge_materializer_v2",
                    int(win), int(not win), 0, 0, 0.6, -0.4, 30.0,
                    0.5 if win else -0.5,
                    int(not win), int(win), 0, 0, 0.6, -0.4, 30.0,
                    0.5 if not win else -0.5,
                ))
            con.executemany(
                "INSERT INTO candle_features VALUES (" + ",".join("?" * 36) + ")",
                payload,
            )
            legacy_payload = []
            for index in range(120, 180):
                legacy_payload.append((
                    index, "LEGACY_USDT", "Min15",
                    1_700_000_000 + index * 900,
                    "low", "bullish", "normal",
                    5, 5, 5, 5, 0, 0, 1, 0, 0, 1,
                    "legacy", "legacy", "legacy",
                    1, 0, 0, 0, 0.6, -0.4, 30.0, 0.5,
                    0, 1, 0, 0, 0.6, -0.4, 30.0, -0.5,
                ))
            con.executemany(
                "INSERT INTO candle_features VALUES (" + ",".join("?" * 36) + ")",
                legacy_payload,
            )
            con.commit()
            con.close()
            report = run_factor_analysis(
                db, top_n=5, templates=["TP0_5_SL0_5"], verbose=False
            )
            row = next(
                item for item in report["groups"]["volatility_regime"]["TP0_5_SL0_5.long"]
                if item["group_key"] == "low"
            )
            self.assertGreater(row["edge_delta"], 0)
            self.assertLess(row["effective_n"], row["raw_n"])
            self.assertGreater(row["net_expectancy_pct"], 0)
            self.assertEqual(report["meta"]["baseline_method"], "current_run_dynamic")
            self.assertTrue(report["meta"]["dataset_fingerprint"])
            self.assertEqual(report["meta"]["symbol_count"], 4)
            self.assertEqual(
                report["meta"]["symbol_count_scope"], "paired_v2_eligible"
            )
            self.assertAlmostEqual(
                report["meta"]["paired_v2_coverage_pct"], 66.6667, places=3
            )
            for state in report["top_long_states"] + report["top_short_states"]:
                symbols = {
                    item["symbol"]
                    for item in (state.get("symbol_concentration") or {}).get(
                        "top_symbols", []
                    )
                }
                self.assertNotIn("LEGACY_USDT", symbols)


class StrategyValidationTests(unittest.TestCase):
    def test_drafts_are_filter_only_and_zero_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "signals.db"
            con = sqlite3.connect(db)
            con.execute("""
                CREATE TABLE signals(
                    id INTEGER PRIMARY KEY, funding_rate REAL,
                    volatility TEXT, signal_json TEXT
                )
            """)
            con.execute("""
                CREATE TABLE paper_trades(
                    id INTEGER PRIMARY KEY, signal_id INTEGER, symbol TEXT,
                    strategy_key TEXT, direction TEXT, result TEXT, pnl_pct REAL,
                    size_usd REAL, leverage REAL, conviction REAL,
                    flow_confirmed INTEGER, flow_score REAL, atr_pct REAL,
                    trend_score REAL, fee_cost_pct REAL, slippage_cost_pct REAL,
                    opened_at TEXT, closed_at TEXT, status TEXT
                )
            """)
            for index in range(80):
                confirmed = index < 40
                pnl = 2.0 if confirmed else (-1.0 if index % 2 else 0.2)
                con.execute(
                    "INSERT INTO signals VALUES (?,?,?,?)",
                    (index, -0.001, "low", json.dumps({"agent_regime": "balanced"})),
                )
                con.execute(
                    "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        index, index, f"S{index}_USDT", "balanced", "SHORT",
                        "WIN" if pnl > 0 else "LOSS", pnl, 100.0, 2.0, 70,
                        int(confirmed), 80 if confirmed else 20, 1.0, 1.0,
                        0.02, 0.03, "2026-01-01", "2026-01-02", "closed",
                    ),
                )
            con.commit()
            con.close()
            result = evaluate_strategy_economics(db)
            draft = next(
                item for item in result["suggestion_drafts"]
                if item["condition"] == "flow_confirmed:True"
            )
            self.assertFalse(draft["authority_eligible"])
            self.assertEqual(draft["risk_impact"]["leverage_change"], "none")
            self.assertFalse(draft["risk_impact"]["automatic_risk_increase"])

    def test_rejected_candidates_receive_v2_counterfactual_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            signals_db = Path(tmp) / "signals.db"
            edge_db = Path(tmp) / "edge.db"
            now = datetime.now(timezone.utc).replace(microsecond=0)
            candidate_ts = int(now.timestamp())
            con = sqlite3.connect(signals_db)
            con.execute("""
                CREATE TABLE signals(
                    id INTEGER PRIMARY KEY, funding_rate REAL,
                    volatility TEXT, signal_json TEXT
                )
            """)
            con.execute("""
                CREATE TABLE paper_trades(
                    id INTEGER PRIMARY KEY, signal_id INTEGER, symbol TEXT,
                    strategy_key TEXT, direction TEXT, result TEXT, pnl_pct REAL,
                    size_usd REAL, leverage REAL, conviction REAL,
                    flow_confirmed INTEGER, flow_score REAL, atr_pct REAL,
                    trend_score REAL, fee_cost_pct REAL, slippage_cost_pct REAL,
                    opened_at TEXT, closed_at TEXT, status TEXT
                )
            """)
            con.execute("""
                CREATE TABLE filtered_candidates(
                    id INTEGER PRIMARY KEY, logged_at TEXT, symbol TEXT,
                    direction TEXT, strategy_key TEXT, gate_key TEXT
                )
            """)
            con.execute(
                "INSERT INTO filtered_candidates VALUES (?,?,?,?,?,?)",
                (
                    1, now.isoformat(), "TEST_USDT", "LONG",
                    "momentum_breakout", "breakout_volume",
                ),
            )
            con.commit()
            con.close()
            con = sqlite3.connect(edge_db)
            con.execute("""
                CREATE TABLE candle_features(
                    symbol TEXT, timeframe TEXT, timestamp INTEGER,
                    label_version TEXT,
                    t05_l_gross REAL, t05_l_ambig INTEGER
                )
            """)
            con.execute(
                "INSERT INTO candle_features VALUES (?,?,?,?,?,?)",
                (
                    "TEST_USDT", "Min15", candidate_ts - 60,
                    "edge_path_v2", 0.5, 0,
                ),
            )
            con.commit()
            con.close()
            result = evaluate_strategy_economics(
                signals_db, edge_db=edge_db
            )
            counter = result["blocked_opportunities"]["counterfactual_paths"]
            self.assertEqual(counter["matched_candidates"], 1)
            self.assertEqual(counter["results"][0]["gate_key"], "breakout_volume")
            self.assertGreater(counter["results"][0]["net_expectancy_pct"], 0)


class MetaLabelV2Tests(unittest.TestCase):
    def test_grouped_temporal_folds_never_cross_entry_episode(self):
        rows = []
        for group in range(20):
            for index in range(10):
                rows.append({
                    "time_group": group,
                    "target": 0.2 if (group + index) % 2 else -0.1,
                    "conviction": 60,
                    "flow_confirmed": index % 2,
                    "flow_score": 70,
                    "atr_pct": 1.0,
                    "trend_score": index - 5,
                    "direction": "LONG" if index % 2 else "SHORT",
                    "funding_rate": 0.0001,
                    "strategy_key": "balanced",
                    "volatility": "low",
                    "agent_regime": "balanced",
                })
        metrics = _evaluate(rows, min_train=50, min_test=30)
        self.assertEqual(metrics["status"], "complete")
        for fold in metrics["folds"]:
            self.assertLess(
                fold["train_group_end"], fold["test_group_start"]
            )


if __name__ == "__main__":
    unittest.main()
