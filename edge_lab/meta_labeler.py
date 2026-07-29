"""Leakage-safe, shadow-only statistical meta-labeler for MT7 paper trades.

This module reads paper outcomes from signals.db and pre-entry market features
from edge_lab.db. It writes research runs and shadow scores only to edge_lab.db.
It never imports app.py and has no path to strategy config, scoring, sizing,
orders, leverage, or execution.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

META_LABEL_VERSION = "mt7_meta_label_v1"
AUTHORITY_MODE = "shadow_read_only"
TIMEFRAME = "Min15"
TIMEFRAME_SECONDS = 15 * 60

FIXED_CATEGORY_LEVELS = {
    "volatility_regime": ["low", "medium", "high", "extreme"],
    "trend_state": ["bullish", "bearish", "neutral"],
    "compression_state": ["compressed", "normal", "expanded"],
}

BASE_FEATURES = [
    "conviction",
    "flow_confirmed",
    "flow_score",
    "atr_pct",
    "trend_score",
    "direction_short",
    "rsi_decile",
    "volume_decile",
    "atr_decile",
    "stddev_decile",
    "tag_compressed",
    "tag_expanded",
    "tag_bullish_trend",
    "tag_bearish_trend",
    "tag_extreme_vol",
    "tag_low_vol",
]


@dataclass
class MetaLabelConfig:
    signals_db: Path
    edge_db: Path
    since: str | None = None
    min_training_rows: int = 70
    min_test_rows: int = 40
    folds: int = 3
    allow_threshold: float = 0.60
    block_threshold: float = 0.40
    calibration_fraction: float = 0.20
    max_feature_age_minutes: int = 30


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return int(parsed.timestamp())
    except Exception:
        return None


def init_meta_storage(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS meta_label_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            authority_mode TEXT NOT NULL,
            since_timestamp TEXT,
            eligible_count INTEGER NOT NULL DEFAULT 0,
            joined_count INTEGER NOT NULL DEFAULT 0,
            coverage_pct REAL,
            train_count INTEGER NOT NULL DEFAULT 0,
            test_count INTEGER NOT NULL DEFAULT 0,
            metrics_json TEXT NOT NULL,
            gates_json TEXT NOT NULL,
            drift_json TEXT NOT NULL,
            model_json TEXT NOT NULL,
            error TEXT
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_meta_label_runs_generated
        ON meta_label_runs(generated_at DESC)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS meta_label_predictions (
            run_id INTEGER NOT NULL,
            trade_id INTEGER NOT NULL,
            split TEXT NOT NULL,
            trade_timestamp INTEGER,
            status_at_score TEXT,
            label INTEGER,
            pnl_pct REAL,
            probability REAL NOT NULL,
            decision TEXT NOT NULL,
            feature_timestamp INTEGER,
            feature_source TEXT,
            created_at TEXT NOT NULL,
            evaluated_at TEXT,
            PRIMARY KEY(run_id, trade_id, split),
            FOREIGN KEY(run_id) REFERENCES meta_label_runs(id)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_meta_label_predictions_run_split
        ON meta_label_predictions(run_id, split, probability)
    """)
    columns = {
        row[1]
        for row in con.execute("PRAGMA table_info(meta_label_predictions)").fetchall()
    }
    if "evaluated_at" not in columns:
        con.execute("ALTER TABLE meta_label_predictions ADD COLUMN evaluated_at TEXT")
    con.commit()


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone())


def _load_paper_trades(signals_db: Path, since: str | None) -> tuple[list[dict], list[dict]]:
    con = sqlite3.connect(signals_db)
    con.row_factory = sqlite3.Row
    where_since = ""
    params: list[Any] = []
    if since:
        where_since = "AND COALESCE(filled_at, opened_at, queued_at, closed_at, '') >= ?"
        params.append(str(since))
    columns = """
        id, symbol, strategy_key, direction, status, result, pnl_pct,
        conviction, flow_confirmed, flow_score, atr_pct, trend_score,
        queued_at, filled_at, opened_at, closed_at
    """
    closed = [
        dict(row)
        for row in con.execute(
            f"""
            SELECT {columns}
            FROM paper_trades
            WHERE status='closed' AND pnl_pct IS NOT NULL
              {where_since}
            ORDER BY COALESCE(filled_at, opened_at, queued_at, closed_at), id
            """,
            params,
        ).fetchall()
    ]
    active = [
        dict(row)
        for row in con.execute(
            f"""
            SELECT {columns}
            FROM paper_trades
            WHERE status IN ('open', 'pending')
              {where_since}
            ORDER BY COALESCE(filled_at, opened_at, queued_at, closed_at), id
            """,
            params,
        ).fetchall()
    ]
    con.close()
    return closed, active


def _snapshot_payload(row: sqlite3.Row) -> dict:
    raw = dict(row)
    try:
        features = json.loads(raw.pop("features_json", "") or "{}")
    except Exception:
        features = {}
    tags = set(features.get("tags") or []) if isinstance(features.get("tags"), list) else set()
    raw.update({
        "volatility_regime": features.get("volatility_regime"),
        "trend_state": features.get("trend_state"),
        "compression_state": features.get("compression_state"),
        "rsi_decile": features.get("rsi_15m_decile"),
        "volume_decile": features.get("volume_decile"),
        "atr_decile": features.get("atr_pct_15m_decile"),
        "stddev_decile": features.get("stddev_decile"),
        "tag_compressed": int("compressed" in tags),
        "tag_expanded": int("expanded" in tags),
        "tag_bullish_trend": int("bullish_trend" in tags),
        "tag_bearish_trend": int("bearish_trend" in tags),
        "tag_extreme_vol": int("extreme_vol" in tags),
        "tag_low_vol": int("low_vol" in tags),
    })
    return raw


def _feature_before_trade(
    edge_con: sqlite3.Connection,
    symbol: str,
    trade_ts: int,
    max_age_seconds: int,
) -> tuple[dict | None, str | None, int | None]:
    cutoff = int(trade_ts) - TIMEFRAME_SECONDS
    symbol = str(symbol or "").upper()
    candidate = None
    source = None
    if _table_exists(edge_con, "candle_feature_snapshots"):
        row = edge_con.execute("""
            SELECT symbol, timeframe, timestamp, features_json
            FROM candle_feature_snapshots
            WHERE symbol=? AND exchange='MEXC' AND timeframe=? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (symbol, TIMEFRAME, cutoff)).fetchone()
        if row:
            candidate = _snapshot_payload(row)
            source = "candle_feature_snapshots"
    if candidate is None and _table_exists(edge_con, "candle_features"):
        row = edge_con.execute("""
            SELECT symbol, timeframe, timestamp, volatility_regime, trend_state,
                   compression_state, rsi_decile, volume_decile, atr_decile,
                   stddev_decile, tag_compressed, tag_expanded, tag_bullish_trend,
                   tag_bearish_trend, tag_extreme_vol, tag_low_vol
            FROM candle_features
            WHERE symbol=? AND timeframe=? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (symbol, TIMEFRAME, cutoff)).fetchone()
        if row:
            candidate = dict(row)
            source = "candle_features"
    if candidate is None:
        return None, None, None
    age_seconds = int(trade_ts - (int(candidate.get("timestamp") or 0) + TIMEFRAME_SECONDS))
    if age_seconds < 0 or age_seconds > max_age_seconds:
        return None, source, age_seconds
    return candidate, source, age_seconds


def build_joined_rows(
    trades: list[dict],
    edge_con: sqlite3.Connection,
    max_feature_age_minutes: int = 30,
) -> tuple[list[dict], dict]:
    joined = []
    reasons: dict[str, int] = {}
    for trade in trades:
        trade_ts = parse_iso_epoch(
            trade.get("filled_at")
            or trade.get("opened_at")
            or trade.get("queued_at")
            or trade.get("closed_at")
        )
        if trade_ts is None:
            reasons["invalid_trade_timestamp"] = reasons.get("invalid_trade_timestamp", 0) + 1
            continue
        feature, source, age_seconds = _feature_before_trade(
            edge_con,
            trade.get("symbol") or "",
            trade_ts,
            max_feature_age_minutes * 60,
        )
        if feature is None:
            reason = "stale_feature" if age_seconds is not None else "feature_unavailable"
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        pnl_pct = _float(trade.get("pnl_pct"))
        row = {
            **trade,
            **feature,
            "trade_timestamp": trade_ts,
            "feature_timestamp": int(feature.get("timestamp") or 0),
            "feature_source": source,
            "feature_age_seconds": age_seconds,
            "label": int(pnl_pct is not None and pnl_pct > 0),
            "pnl_pct": pnl_pct,
        }
        joined.append(row)
        reasons["matched"] = reasons.get("matched", 0) + 1
    joined.sort(key=lambda row: (int(row["trade_timestamp"]), int(row["id"])))
    return joined, {
        "eligible_count": len(trades),
        "joined_count": len(joined),
        "coverage_pct": round(len(joined) / len(trades) * 100.0, 2) if trades else 0.0,
        "reasons": dict(sorted(reasons.items())),
    }


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def feature_spec(rows: list[dict], min_strategy_count: int = 4, max_strategies: int = 12) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("strategy_key") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    strategies = [
        key
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if count >= min_strategy_count
    ][:max_strategies]
    feature_names = list(BASE_FEATURES)
    for field, levels in FIXED_CATEGORY_LEVELS.items():
        feature_names.extend(f"{field}={level}" for level in levels)
    feature_names.extend(f"strategy={strategy}" for strategy in strategies)
    return {"feature_names": feature_names, "strategies": strategies}


def _scaled(value: Any, divisor: float, low: float = -3.0, high: float = 3.0) -> float:
    number = _float(value, 0.0) or 0.0
    return float(np.clip(number / divisor, low, high))


def vectorize(rows: list[dict], spec: dict) -> np.ndarray:
    values: list[list[float]] = []
    strategies = list(spec.get("strategies") or [])
    for row in rows:
        vector = [
            _scaled(row.get("conviction"), 100.0, 0.0, 1.0),
            1.0 if row.get("flow_confirmed") else 0.0,
            _scaled(row.get("flow_score"), 100.0, -1.0, 1.0),
            _scaled(row.get("atr_pct"), 10.0, 0.0, 2.0),
            _scaled(row.get("trend_score"), 5.0, -2.0, 2.0),
            1.0 if str(row.get("direction") or "").upper() == "SHORT" else 0.0,
            _scaled(row.get("rsi_decile"), 10.0, 0.0, 1.0),
            _scaled(row.get("volume_decile"), 10.0, 0.0, 1.0),
            _scaled(row.get("atr_decile"), 10.0, 0.0, 1.0),
            _scaled(row.get("stddev_decile"), 10.0, 0.0, 1.0),
            1.0 if row.get("tag_compressed") else 0.0,
            1.0 if row.get("tag_expanded") else 0.0,
            1.0 if row.get("tag_bullish_trend") else 0.0,
            1.0 if row.get("tag_bearish_trend") else 0.0,
            1.0 if row.get("tag_extreme_vol") else 0.0,
            1.0 if row.get("tag_low_vol") else 0.0,
        ]
        for field, levels in FIXED_CATEGORY_LEVELS.items():
            current = str(row.get(field) or "").lower()
            vector.extend(1.0 if current == level else 0.0 for level in levels)
        strategy = str(row.get("strategy_key") or "unknown")
        vector.extend(1.0 if strategy == level else 0.0 for level in strategies)
        values.append(vector)
    width = len(spec.get("feature_names") or [])
    return np.asarray(values, dtype=float).reshape((len(values), width))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _fit_irls(design: np.ndarray, labels: np.ndarray, l2: float = 1.0, iterations: int = 80) -> np.ndarray:
    weights = np.zeros(design.shape[1], dtype=float)
    base_rate = float(np.clip(labels.mean(), 0.02, 0.98))
    weights[0] = math.log(base_rate / (1.0 - base_rate))
    penalty = np.eye(design.shape[1], dtype=float) * l2
    penalty[0, 0] = 0.0
    for _ in range(iterations):
        probabilities = _sigmoid(design @ weights)
        variance = np.clip(probabilities * (1.0 - probabilities), 1e-5, None)
        gradient = design.T @ (probabilities - labels) + penalty @ weights
        hessian = (design.T * variance) @ design + penalty
        try:
            delta = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            delta = np.linalg.pinv(hessian) @ gradient
        weights -= delta
        if float(np.max(np.abs(delta))) < 1e-7:
            break
    return weights


def fit_base_model(rows: list[dict]) -> dict:
    spec = feature_spec(rows)
    matrix = vectorize(rows, spec)
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales[scales < 1e-6] = 1.0
    normalized = (matrix - means) / scales
    design = np.column_stack([np.ones(len(rows)), normalized])
    labels = np.asarray([int(row["label"]) for row in rows], dtype=float)
    weights = _fit_irls(design, labels, l2=1.0)
    return {
        "spec": spec,
        "means": means.tolist(),
        "scales": scales.tolist(),
        "weights": weights.tolist(),
    }


def raw_logits(model: dict, rows: list[dict]) -> np.ndarray:
    matrix = vectorize(rows, model["spec"])
    means = np.asarray(model["means"], dtype=float)
    scales = np.asarray(model["scales"], dtype=float)
    normalized = (matrix - means) / scales
    design = np.column_stack([np.ones(len(rows)), normalized])
    return design @ np.asarray(model["weights"], dtype=float)


def fit_platt(logits: np.ndarray, labels: np.ndarray) -> dict:
    if len(labels) < 12 or len(set(labels.astype(int).tolist())) < 2:
        return {"slope": 1.0, "intercept": 0.0, "status": "identity_insufficient_calibration"}
    design = np.column_stack([np.ones(len(logits)), logits])
    weights = _fit_irls(design, labels, l2=2.0, iterations=60)
    slope = max(0.05, float(weights[1]))
    return {
        "slope": slope,
        "intercept": float(weights[0]),
        "status": "platt_temporal_holdout",
    }


def fit_temporally_calibrated_model(rows: list[dict], calibration_fraction: float = 0.20) -> dict:
    split = max(20, int(round(len(rows) * (1.0 - calibration_fraction))))
    split = min(split, len(rows) - 12)
    if split < 20:
        split = len(rows)
    base_rows = rows[:split]
    calibration_rows = rows[split:]
    model = fit_base_model(base_rows)
    calibrator = {"slope": 1.0, "intercept": 0.0, "status": "identity"}
    if calibration_rows:
        logits = raw_logits(model, calibration_rows)
        labels = np.asarray([int(row["label"]) for row in calibration_rows], dtype=float)
        calibrator = fit_platt(logits, labels)
    model["calibrator"] = calibrator
    model["base_training_count"] = len(base_rows)
    model["calibration_count"] = len(calibration_rows)
    return model


def predict_probabilities(model: dict, rows: list[dict]) -> np.ndarray:
    if not rows:
        return np.asarray([], dtype=float)
    logits = raw_logits(model, rows)
    calibrator = model.get("calibrator") or {}
    calibrated_logits = (
        float(calibrator.get("intercept") or 0.0)
        + float(calibrator.get("slope") or 1.0) * logits
    )
    return _sigmoid(calibrated_logits)


def walk_forward_splits(
    row_count: int,
    min_training_rows: int,
    min_test_rows: int,
    folds: int,
) -> list[tuple[int, int, int]]:
    if row_count < min_training_rows + min_test_rows:
        return []
    first_test = max(min_training_rows, row_count // 2)
    remaining = row_count - first_test
    min_fold_rows = max(12, int(math.ceil(min_test_rows / max(1, folds))))
    fold_count = max(1, min(int(folds), remaining // min_fold_rows))
    boundaries = np.linspace(first_test, row_count, fold_count + 1, dtype=int)
    splits = []
    for index in range(fold_count):
        test_start = int(boundaries[index])
        test_end = int(boundaries[index + 1])
        if test_end > test_start:
            splits.append((0, test_start, test_end))
    return splits


def _strategy_baselines(train_rows: list[dict], test_rows: list[dict]) -> np.ndarray:
    labels = [int(row["label"]) for row in train_rows]
    global_rate = float(np.mean(labels)) if labels else 0.5
    totals: dict[str, list[int]] = {}
    for row in train_rows:
        key = str(row.get("strategy_key") or "unknown")
        totals.setdefault(key, []).append(int(row["label"]))
    output = []
    for row in test_rows:
        values = totals.get(str(row.get("strategy_key") or "unknown"), [])
        output.append((sum(values) + 10.0 * global_rate) / (len(values) + 10.0))
    return np.asarray(output, dtype=float)


def probability_metrics(probabilities: np.ndarray, labels: np.ndarray) -> dict:
    if not len(labels):
        return {}
    probabilities = np.clip(probabilities.astype(float), 1e-6, 1.0 - 1e-6)
    labels = labels.astype(float)
    bins = np.linspace(0.0, 1.0, 6)
    ece = 0.0
    calibration = []
    for index in range(len(bins) - 1):
        lower, upper = bins[index], bins[index + 1]
        mask = (probabilities >= lower) & (
            probabilities <= upper if index == len(bins) - 2 else probabilities < upper
        )
        count = int(mask.sum())
        if not count:
            continue
        predicted = float(probabilities[mask].mean())
        actual = float(labels[mask].mean())
        ece += (count / len(labels)) * abs(predicted - actual)
        calibration.append({
            "lower": round(float(lower), 2),
            "upper": round(float(upper), 2),
            "count": count,
            "mean_probability": round(predicted, 4),
            "actual_positive_rate": round(actual, 4),
        })
    return {
        "count": int(len(labels)),
        "brier": round(float(np.mean((probabilities - labels) ** 2)), 6),
        "log_loss": round(float(-np.mean(
            labels * np.log(probabilities) + (1.0 - labels) * np.log(1.0 - probabilities)
        )), 6),
        "accuracy": round(float(np.mean((probabilities >= 0.5) == labels)), 4),
        "ece": round(float(ece), 6),
        "mean_probability": round(float(probabilities.mean()), 4),
        "actual_positive_rate": round(float(labels.mean()), 4),
        "calibration_bins": calibration,
    }


def _pnl_summary(rows: list[dict], mask: np.ndarray | None = None) -> dict:
    selected = rows if mask is None else [row for row, keep in zip(rows, mask.tolist()) if keep]
    pnls = [float(row.get("pnl_pct") or 0.0) for row in selected]
    gains = sum(value for value in pnls if value > 0)
    losses = abs(sum(value for value in pnls if value < 0))
    running = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in pnls:
        running += value
        peak = max(peak, running)
        drawdown = min(drawdown, running - peak)
    return {
        "count": len(selected),
        "avg_pnl_pct": round(float(np.mean(pnls)), 4) if pnls else None,
        "total_pnl_pct": round(float(sum(pnls)), 4) if pnls else 0.0,
        "positive_rate": round(sum(1 for value in pnls if value > 0) / len(pnls), 4) if pnls else None,
        "profit_factor": round(gains / losses, 4) if losses else (round(gains, 4) if gains else None),
        "max_drawdown_pct_points": round(drawdown, 4),
    }


def decision_for_probability(probability: float, allow_threshold: float, block_threshold: float) -> str:
    if probability >= allow_threshold:
        return "shadow_allow"
    if probability <= block_threshold:
        return "shadow_block"
    return "abstain"


def decision_metrics(
    rows: list[dict],
    probabilities: np.ndarray,
    allow_threshold: float,
    block_threshold: float,
) -> dict:
    allow_mask = probabilities >= allow_threshold
    block_mask = probabilities <= block_threshold
    abstain_mask = ~(allow_mask | block_mask)
    return {
        "thresholds": {"allow": allow_threshold, "block": block_threshold},
        "all": _pnl_summary(rows),
        "shadow_allow": _pnl_summary(rows, allow_mask),
        "shadow_block": _pnl_summary(rows, block_mask),
        "abstain": _pnl_summary(rows, abstain_mask),
    }


def _feature_psi(train: np.ndarray, test: np.ndarray) -> float:
    if not len(train) or not len(test):
        return 0.0
    unique = np.unique(train)
    if len(unique) <= 3:
        categories = np.unique(np.concatenate([train, test]))
        train_props = np.asarray([(train == value).mean() for value in categories])
        test_props = np.asarray([(test == value).mean() for value in categories])
    else:
        edges = np.unique(np.quantile(train, np.linspace(0.0, 1.0, 6)))
        if len(edges) < 3:
            return 0.0
        edges[0], edges[-1] = -np.inf, np.inf
        train_props = np.histogram(train, bins=edges)[0] / len(train)
        test_props = np.histogram(test, bins=edges)[0] / len(test)
    train_props = np.clip(train_props, 1e-4, None)
    test_props = np.clip(test_props, 1e-4, None)
    return float(np.sum((test_props - train_props) * np.log(test_props / train_props)))


def drift_metrics(train_rows: list[dict], test_rows: list[dict], spec: dict) -> dict:
    if not train_rows or not test_rows:
        return {"max_psi": None, "mean_psi": None, "features": []}
    train = vectorize(train_rows, spec)
    test = vectorize(test_rows, spec)
    values = []
    for index, name in enumerate(spec.get("feature_names") or []):
        psi = _feature_psi(train[:, index], test[:, index])
        values.append({"feature": name, "psi": round(psi, 6)})
    values.sort(key=lambda item: item["psi"], reverse=True)
    return {
        "max_psi": round(max(item["psi"] for item in values), 6) if values else 0.0,
        "mean_psi": round(float(np.mean([item["psi"] for item in values])), 6) if values else 0.0,
        "features": values[:10],
    }


def evaluate_walk_forward(rows: list[dict], config: MetaLabelConfig) -> dict:
    splits = walk_forward_splits(
        len(rows),
        config.min_training_rows,
        config.min_test_rows,
        config.folds,
    )
    predictions = []
    fold_metrics = []
    first_test_start = None
    for fold_index, (_, test_start, test_end) in enumerate(splits, start=1):
        train_rows = rows[:test_start]
        test_rows = rows[test_start:test_end]
        model = fit_temporally_calibrated_model(train_rows, config.calibration_fraction)
        probabilities = predict_probabilities(model, test_rows)
        labels = np.asarray([int(row["label"]) for row in test_rows], dtype=float)
        global_rate = float(np.mean([int(row["label"]) for row in train_rows]))
        no_filter = np.full(len(test_rows), global_rate, dtype=float)
        strategy = _strategy_baselines(train_rows, test_rows)
        model_metrics = probability_metrics(probabilities, labels)
        no_filter_metrics = probability_metrics(no_filter, labels)
        strategy_metrics = probability_metrics(strategy, labels)
        fold_metrics.append({
            "fold": fold_index,
            "train_count": len(train_rows),
            "test_count": len(test_rows),
            "test_start_timestamp": test_rows[0]["trade_timestamp"],
            "test_end_timestamp": test_rows[-1]["trade_timestamp"],
            "model": model_metrics,
            "no_filter_baseline": no_filter_metrics,
            "strategy_baseline": strategy_metrics,
        })
        first_test_start = test_start if first_test_start is None else first_test_start
        for row, probability, no_filter_p, strategy_p in zip(
            test_rows, probabilities, no_filter, strategy
        ):
            predictions.append({
                "row": row,
                "probability": float(probability),
                "no_filter_probability": float(no_filter_p),
                "strategy_probability": float(strategy_p),
                "fold": fold_index,
            })

    if not predictions:
        return {
            "status": "insufficient_data",
            "folds": [],
            "predictions": [],
            "metrics": {},
            "drift": {"max_psi": None, "mean_psi": None, "features": []},
        }

    test_rows = [item["row"] for item in predictions]
    probabilities = np.asarray([item["probability"] for item in predictions], dtype=float)
    labels = np.asarray([int(row["label"]) for row in test_rows], dtype=float)
    no_filter = np.asarray([item["no_filter_probability"] for item in predictions], dtype=float)
    strategy = np.asarray([item["strategy_probability"] for item in predictions], dtype=float)
    model_metrics = probability_metrics(probabilities, labels)
    no_filter_metrics = probability_metrics(no_filter, labels)
    strategy_metrics = probability_metrics(strategy, labels)
    decision = decision_metrics(
        test_rows,
        probabilities,
        config.allow_threshold,
        config.block_threshold,
    )
    train_for_drift = rows[: int(first_test_start or 0)]
    drift_spec = feature_spec(train_for_drift)
    drift = drift_metrics(train_for_drift, test_rows, drift_spec)
    fold_wins = sum(
        1
        for fold in fold_metrics
        if (fold["model"].get("brier") or 1.0) < (fold["no_filter_baseline"].get("brier") or 0.0)
    )
    model_metrics["brier_skill_vs_no_filter"] = round(
        1.0 - model_metrics["brier"] / no_filter_metrics["brier"], 6
    ) if no_filter_metrics.get("brier") else None
    model_metrics["brier_skill_vs_strategy"] = round(
        1.0 - model_metrics["brier"] / strategy_metrics["brier"], 6
    ) if strategy_metrics.get("brier") else None
    return {
        "status": "complete",
        "folds": fold_metrics,
        "predictions": predictions,
        "metrics": {
            "model": model_metrics,
            "no_filter_baseline": no_filter_metrics,
            "strategy_baseline": strategy_metrics,
            "decisions": decision,
            "fold_brier_wins_vs_no_filter": fold_wins,
            "fold_count": len(fold_metrics),
        },
        "drift": drift,
    }


def evidence_gates(
    coverage: dict,
    evaluation: dict,
    min_test_rows: int,
) -> dict:
    metrics = evaluation.get("metrics") or {}
    model = metrics.get("model") or {}
    no_filter = metrics.get("no_filter_baseline") or {}
    strategy = metrics.get("strategy_baseline") or {}
    decisions = metrics.get("decisions") or {}
    allow = decisions.get("shadow_allow") or {}
    all_rows = decisions.get("all") or {}
    drift = evaluation.get("drift") or {}
    checks = {
        "coverage_at_least_95pct": float(coverage.get("coverage_pct") or 0.0) >= 95.0,
        "walk_forward_test_sample": int(model.get("count") or 0) >= int(min_test_rows),
        "brier_beats_no_filter": (
            model.get("brier") is not None
            and no_filter.get("brier") is not None
            and float(model["brier"]) < float(no_filter["brier"])
        ),
        "brier_beats_strategy": (
            model.get("brier") is not None
            and strategy.get("brier") is not None
            and float(model["brier"]) < float(strategy["brier"])
        ),
        "calibration_ece_at_most_0_10": (
            model.get("ece") is not None and float(model["ece"]) <= 0.10
        ),
        "shadow_allow_sample_at_least_20": int(allow.get("count") or 0) >= 20,
        "shadow_allow_positive_ev": (
            allow.get("avg_pnl_pct") is not None and float(allow["avg_pnl_pct"]) > 0.0
        ),
        "shadow_allow_beats_unfiltered_ev": (
            allow.get("avg_pnl_pct") is not None
            and all_rows.get("avg_pnl_pct") is not None
            and float(allow["avg_pnl_pct"]) > float(all_rows["avg_pnl_pct"])
        ),
        "drift_max_psi_at_most_0_25": (
            drift.get("max_psi") is not None and float(drift["max_psi"]) <= 0.25
        ),
        "majority_fold_brier_wins": (
            int(metrics.get("fold_brier_wins_vs_no_filter") or 0)
            >= max(1, math.ceil(int(metrics.get("fold_count") or 0) / 2))
        ),
    }
    return {
        "checks": checks,
        "passed": sum(1 for value in checks.values() if value),
        "total": len(checks),
        "evidence_ready": bool(checks) and all(checks.values()),
        "authority_mode": AUTHORITY_MODE,
        "authority_eligible": False,
        "note": "Evidence readiness is descriptive only. This run cannot change MT7 behavior.",
    }


def _persist_run(
    con: sqlite3.Connection,
    config: MetaLabelConfig,
    coverage: dict,
    evaluation: dict,
    gates: dict,
    final_model: dict,
    active_scores: list[dict],
    error: str | None = None,
) -> int:
    generated_at = utc_now()
    metrics = evaluation.get("metrics") or {}
    status = evaluation.get("status") or ("failed" if error else "unknown")
    test_count = int(((metrics.get("model") or {}).get("count") or 0))
    train_count = max(0, int(coverage.get("joined_count") or 0) - test_count)
    cur = con.execute("""
        INSERT INTO meta_label_runs(
            version, generated_at, status, authority_mode, since_timestamp,
            eligible_count, joined_count, coverage_pct, train_count, test_count,
            metrics_json, gates_json, drift_json, model_json, error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        META_LABEL_VERSION,
        generated_at,
        status,
        AUTHORITY_MODE,
        config.since,
        int(coverage.get("eligible_count") or 0),
        int(coverage.get("joined_count") or 0),
        float(coverage.get("coverage_pct") or 0.0),
        train_count,
        test_count,
        json.dumps(metrics, separators=(",", ":"), sort_keys=True),
        json.dumps(gates, separators=(",", ":"), sort_keys=True),
        json.dumps(evaluation.get("drift") or {}, separators=(",", ":"), sort_keys=True),
        json.dumps(final_model, separators=(",", ":"), sort_keys=True),
        error,
    ))
    run_id = int(cur.lastrowid)
    payload = []
    for item in evaluation.get("predictions") or []:
        row = item["row"]
        probability = float(item["probability"])
        payload.append((
            run_id,
            int(row["id"]),
            "walk_forward",
            int(row["trade_timestamp"]),
            str(row.get("status") or "closed"),
            int(row["label"]),
            float(row.get("pnl_pct") or 0.0),
            probability,
            decision_for_probability(
                probability, config.allow_threshold, config.block_threshold
            ),
            int(row.get("feature_timestamp") or 0),
            row.get("feature_source"),
            generated_at,
        ))
    for item in active_scores:
        row = item["row"]
        probability = float(item["probability"])
        payload.append((
            run_id,
            int(row["id"]),
            "active_shadow",
            int(row["trade_timestamp"]),
            str(row.get("status") or "unknown"),
            None,
            None,
            probability,
            decision_for_probability(
                probability, config.allow_threshold, config.block_threshold
            ),
            int(row.get("feature_timestamp") or 0),
            row.get("feature_source"),
            generated_at,
        ))
    con.executemany("""
        INSERT INTO meta_label_predictions(
            run_id, trade_id, split, trade_timestamp, status_at_score, label,
            pnl_pct, probability, decision, feature_timestamp, feature_source,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, payload)
    con.commit()
    return run_id


def _evaluate_prior_active_scores(
    edge_con: sqlite3.Connection,
    signals_db: Path,
) -> int:
    pending_ids = [
        int(row[0])
        for row in edge_con.execute("""
            SELECT DISTINCT trade_id
            FROM meta_label_predictions
            WHERE split='active_shadow' AND label IS NULL
        """).fetchall()
    ]
    if not pending_ids:
        return 0
    signals_con = sqlite3.connect(signals_db)
    placeholders = ",".join("?" for _ in pending_ids)
    outcomes = signals_con.execute(
        f"""
        SELECT id, pnl_pct
        FROM paper_trades
        WHERE id IN ({placeholders})
          AND status='closed'
          AND pnl_pct IS NOT NULL
        """,
        pending_ids,
    ).fetchall()
    signals_con.close()
    evaluated_at = utc_now()
    for trade_id, pnl_pct in outcomes:
        pnl = float(pnl_pct)
        edge_con.execute("""
            UPDATE meta_label_predictions
            SET label=?, pnl_pct=?, evaluated_at=?
            WHERE trade_id=? AND split='active_shadow' AND label IS NULL
        """, (int(pnl > 0), pnl, evaluated_at, int(trade_id)))
    edge_con.commit()
    return len(outcomes)


def _forward_shadow_metrics(
    con: sqlite3.Connection,
    allow_threshold: float,
    block_threshold: float,
) -> dict:
    rows = [
        dict(row)
        for row in con.execute("""
            SELECT trade_id, trade_timestamp, label, pnl_pct, probability,
                   decision, feature_timestamp, feature_source, created_at,
                   evaluated_at
            FROM meta_label_predictions
            WHERE split='active_shadow' AND label IS NOT NULL
            ORDER BY created_at, run_id
        """).fetchall()
    ]
    first_by_trade: dict[int, dict] = {}
    for row in rows:
        first_by_trade.setdefault(int(row["trade_id"]), row)
    selected = list(first_by_trade.values())
    if not selected:
        return {
            "count": 0,
            "target": 50,
            "ready": False,
            "probability": {},
            "decisions": {},
            "note": "Waiting for pre-outcome shadow scores to close.",
        }
    probabilities = np.asarray([float(row["probability"]) for row in selected])
    labels = np.asarray([int(row["label"]) for row in selected], dtype=float)
    return {
        "count": len(selected),
        "target": 50,
        "ready": len(selected) >= 50,
        "probability": probability_metrics(probabilities, labels),
        "decisions": decision_metrics(
            selected, probabilities, allow_threshold, block_threshold
        ),
        "note": "First pre-outcome score per trade only; later rescoring is excluded.",
    }


def run_meta_labeler(config: MetaLabelConfig) -> dict:
    edge_con = sqlite3.connect(config.edge_db)
    edge_con.row_factory = sqlite3.Row
    init_meta_storage(edge_con)
    newly_evaluated_forward_scores = _evaluate_prior_active_scores(
        edge_con, config.signals_db
    )
    closed, active = _load_paper_trades(config.signals_db, config.since)
    joined, coverage = build_joined_rows(
        closed, edge_con, config.max_feature_age_minutes
    )
    active_joined, active_coverage = build_joined_rows(
        active, edge_con, config.max_feature_age_minutes
    )
    evaluation = evaluate_walk_forward(joined, config)
    final_model = {}
    active_scores = []
    error = None
    try:
        if len(joined) >= config.min_training_rows:
            final_model = fit_temporally_calibrated_model(
                joined, config.calibration_fraction
            )
            active_probabilities = predict_probabilities(final_model, active_joined)
            previously_scored = {
                int(row[0])
                for row in edge_con.execute("""
                    SELECT DISTINCT trade_id
                    FROM meta_label_predictions
                    WHERE split='active_shadow'
                """).fetchall()
            }
            active_scores = [
                {"row": row, "probability": float(probability)}
                for row, probability in zip(active_joined, active_probabilities)
                if int(row["id"]) not in previously_scored
            ]
    except Exception as exc:
        error = str(exc)
        evaluation["status"] = "failed"
    gates = evidence_gates(coverage, evaluation, config.min_test_rows)
    evaluation.setdefault("metrics", {})["forward_shadow"] = _forward_shadow_metrics(
        edge_con, config.allow_threshold, config.block_threshold
    )
    run_id = _persist_run(
        edge_con,
        config,
        coverage,
        evaluation,
        gates,
        final_model,
        active_scores,
        error,
    )
    edge_con.close()
    return {
        "success": error is None,
        "run_id": run_id,
        "version": META_LABEL_VERSION,
        "status": evaluation.get("status"),
        "authority_mode": AUTHORITY_MODE,
        "authority_eligible": False,
        "coverage": coverage,
        "active_coverage": active_coverage,
        "metrics": evaluation.get("metrics") or {},
        "drift": evaluation.get("drift") or {},
        "gates": gates,
        "folds": evaluation.get("folds") or [],
        "active_scores": [
            {
                "trade_id": int(item["row"]["id"]),
                "symbol": item["row"].get("symbol"),
                "strategy_key": item["row"].get("strategy_key"),
                "probability": round(float(item["probability"]), 6),
                "decision": decision_for_probability(
                    float(item["probability"]),
                    config.allow_threshold,
                    config.block_threshold,
                ),
            }
            for item in active_scores
        ],
        "newly_evaluated_forward_scores": newly_evaluated_forward_scores,
        "forward_shadow": (evaluation.get("metrics") or {}).get("forward_shadow") or {},
        "error": error,
    }


def latest_meta_label_overview(edge_db: Path) -> dict:
    if not edge_db.exists():
        return {
            "available": False,
            "authority_mode": AUTHORITY_MODE,
            "authority_eligible": False,
            "note": "Edge Lab database is missing.",
        }
    con = sqlite3.connect(edge_db)
    con.row_factory = sqlite3.Row
    init_meta_storage(con)
    if not _table_exists(con, "meta_label_runs"):
        con.close()
        return {
            "available": False,
            "authority_mode": AUTHORITY_MODE,
            "authority_eligible": False,
            "note": "No meta-labeler run has been recorded.",
        }
    run = con.execute(
        "SELECT * FROM meta_label_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not run:
        con.close()
        return {
            "available": False,
            "authority_mode": AUTHORITY_MODE,
            "authority_eligible": False,
            "note": "No meta-labeler run has been recorded.",
        }
    row = dict(run)
    predictions = [
        dict(item)
        for item in con.execute("""
            SELECT trade_id, split, trade_timestamp, status_at_score, label,
                   pnl_pct, probability, decision, feature_timestamp,
                   feature_source, created_at, evaluated_at
            FROM meta_label_predictions
            WHERE run_id=?
            ORDER BY trade_timestamp DESC, trade_id DESC
            LIMIT 40
        """, (row["id"],)).fetchall()
    ]
    forward_shadow = _forward_shadow_metrics(con, 0.60, 0.40)
    con.close()
    return {
        "available": True,
        "run_id": row["id"],
        "version": row["version"],
        "generated_at": row["generated_at"],
        "status": row["status"],
        "authority_mode": row["authority_mode"],
        "authority_eligible": False,
        "since_timestamp": row["since_timestamp"],
        "eligible_count": row["eligible_count"],
        "joined_count": row["joined_count"],
        "coverage_pct": row["coverage_pct"],
        "train_count": row["train_count"],
        "test_count": row["test_count"],
        "metrics": json.loads(row["metrics_json"] or "{}"),
        "gates": json.loads(row["gates_json"] or "{}"),
        "drift": json.loads(row["drift_json"] or "{}"),
        "error": row["error"],
        "recent_predictions": predictions,
        "forward_shadow": forward_shadow,
        "safety": {
            "changes_conviction": False,
            "changes_strategy_config": False,
            "changes_position_size": False,
            "places_orders": False,
            "execution_authority": False,
        },
    }
