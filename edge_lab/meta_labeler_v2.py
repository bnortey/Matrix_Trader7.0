"""Grouped-time, risk-normalized challenger to the frozen v1 meta-labeler."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

VERSION = "mt7_meta_label_v2_challenger"
AUTHORITY_MODE = "shadow_read_only"


def run_meta_labeler_v2(
    signals_db: Path,
    edge_db: Path,
    since: str | None = None,
    min_train: int = 80,
    min_test: int = 50,
) -> dict:
    rows, coverage = _load_rows(signals_db, since)
    result = _evaluate(rows, min_train, min_test)
    gates = _gates(coverage, result, min_test)
    run_id = _persist(edge_db, since, coverage, result, gates)
    return {
        "success": True,
        "run_id": run_id,
        "version": VERSION,
        "authority_mode": AUTHORITY_MODE,
        "authority_eligible": False,
        "coverage": coverage,
        "metrics": result,
        "gates": gates,
        "target": (
            "net leverage-normalized paper P&L; positive values are profitable "
            "after recorded paper fees/slippage"
        ),
        "split_policy": (
            "expanding walk-forward by hourly entry group; trades sharing an "
            "entry-time group cannot cross the train/test boundary"
        ),
    }


def latest_meta_label_v2_overview(edge_db: Path) -> dict:
    if not Path(edge_db).exists():
        return {"available": False, "version": VERSION}
    con = sqlite3.connect(edge_db)
    _init(con)
    row = con.execute(
        "SELECT * FROM meta_label_v2_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        con.close()
        return {"available": False, "version": VERSION}
    names = [item[1] for item in con.execute("PRAGMA table_info(meta_label_v2_runs)")]
    data = dict(zip(names, row))
    con.close()
    return {
        "available": True,
        "run_id": data["id"],
        "version": data["version"],
        "generated_at": data["generated_at"],
        "coverage": json.loads(data["coverage_json"]),
        "metrics": json.loads(data["metrics_json"]),
        "gates": json.loads(data["gates_json"]),
        "authority_mode": AUTHORITY_MODE,
        "authority_eligible": False,
    }


def _load_rows(signals_db: Path, since: str | None) -> tuple[list[dict], dict]:
    con = sqlite3.connect(f"file:{Path(signals_db)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    params = []
    where = ""
    if since:
        where = "AND COALESCE(p.filled_at,p.opened_at,p.queued_at,p.closed_at) >= ?"
        params.append(since)
    raw = [dict(row) for row in con.execute(f"""
        SELECT p.id, p.strategy_key, p.direction, p.pnl_pct, p.leverage,
               p.conviction, p.flow_confirmed, p.flow_score, p.atr_pct,
               p.trend_score, COALESCE(p.filled_at,p.opened_at,p.queued_at) AS entry_at,
               s.funding_rate, s.volatility, s.signal_json
        FROM paper_trades AS p
        LEFT JOIN signals AS s ON s.id=p.signal_id
        WHERE p.status='closed' AND p.pnl_pct IS NOT NULL {where}
        ORDER BY COALESCE(p.filled_at,p.opened_at,p.queued_at), p.id
    """, params).fetchall()]
    con.close()
    rows = []
    exact = 0
    for row in raw:
        try:
            payload = json.loads(row.get("signal_json") or "{}")
        except Exception:
            payload = {}
        if payload:
            exact += 1
        ts = _iso_epoch(row.get("entry_at"))
        if ts is None:
            continue
        leverage = max(1.0, float(row.get("leverage") or 1.0))
        rows.append({
            **row,
            "entry_ts": ts,
            "time_group": ts // 3600,
            "target": float(row.get("pnl_pct") or 0.0) / leverage,
            "agent_regime": str(payload.get("agent_regime") or "unknown").lower(),
            "volatility": str(
                row.get("volatility")
                or payload.get("volatility_regime")
                or "unknown"
            ).lower(),
        })
    return rows, {
        "eligible_count": len(raw),
        "joined_count": len(rows),
        "exact_signal_snapshot_count": exact,
        "coverage_pct": round(len(rows) / len(raw) * 100.0, 2) if raw else 0.0,
        "exact_snapshot_pct": round(exact / len(raw) * 100.0, 2) if raw else 0.0,
    }


def _iso_epoch(value) -> int | None:
    try:
        text = str(value or "")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except Exception:
        return None


def _spec(rows: list[dict]) -> dict:
    strategies = sorted({str(row.get("strategy_key") or "unknown") for row in rows})
    volatility = sorted({str(row.get("volatility") or "unknown") for row in rows})
    regimes = sorted({str(row.get("agent_regime") or "unknown") for row in rows})
    return {"strategies": strategies, "volatility": volatility, "regimes": regimes}


def _matrix(rows: list[dict], spec: dict) -> np.ndarray:
    values = []
    for row in rows:
        vector = [
            float(row.get("conviction") or 0.0) / 100.0,
            1.0 if row.get("flow_confirmed") else 0.0,
            float(row.get("flow_score") or 0.0) / 100.0,
            min(3.0, float(row.get("atr_pct") or 0.0) / 5.0),
            float(row.get("trend_score") or 0.0) / 5.0,
            1.0 if str(row.get("direction") or "").upper() == "SHORT" else 0.0,
            float(row.get("funding_rate") or 0.0) * 1000.0,
        ]
        vector.extend(
            1.0 if row.get("strategy_key") == level else 0.0
            for level in spec["strategies"]
        )
        vector.extend(
            1.0 if row.get("volatility") == level else 0.0
            for level in spec["volatility"]
        )
        vector.extend(
            1.0 if row.get("agent_regime") == level else 0.0
            for level in spec["regimes"]
        )
        values.append(vector)
    return np.asarray(values, dtype=float)


def _fit(rows: list[dict]) -> dict:
    spec = _spec(rows)
    x = _matrix(rows, spec)
    means = x.mean(axis=0)
    scales = x.std(axis=0)
    scales[scales < 1e-6] = 1.0
    design = np.column_stack([np.ones(len(rows)), (x - means) / scales])
    y = np.asarray([float(row["target"]) for row in rows])
    # Winsorization prevents one leveraged outlier from defining the model.
    low, high = np.quantile(y, [0.05, 0.95]) if len(y) >= 20 else (y.min(), y.max())
    y_fit = np.clip(y, low, high)
    penalty = np.eye(design.shape[1]) * 2.0
    penalty[0, 0] = 0.0
    weights = np.linalg.pinv(design.T @ design + penalty) @ design.T @ y_fit
    return {
        "spec": spec,
        "means": means,
        "scales": scales,
        "weights": weights,
    }


def _predict(model: dict, rows: list[dict]) -> np.ndarray:
    x = _matrix(rows, model["spec"])
    design = np.column_stack([
        np.ones(len(rows)),
        (x - model["means"]) / model["scales"],
    ])
    return design @ model["weights"]


def _evaluate(rows: list[dict], min_train: int, min_test: int) -> dict:
    groups = sorted({int(row["time_group"]) for row in rows})
    if len(rows) < min_train + min_test or len(groups) < 4:
        return {"status": "insufficient_data", "count": len(rows)}
    first_test_group = groups[max(1, len(groups) // 2)]
    test_groups = [group for group in groups if group >= first_test_group]
    chunks = [chunk.tolist() for chunk in np.array_split(test_groups, min(3, len(test_groups))) if len(chunk)]
    predictions = []
    actuals = []
    baselines = []
    fold_rows = []
    for index, chunk in enumerate(chunks, start=1):
        start_group = min(chunk)
        train = [row for row in rows if row["time_group"] < start_group]
        test = [row for row in rows if row["time_group"] in set(chunk)]
        if len(train) < min_train or not test:
            continue
        model = _fit(train)
        pred = _predict(model, test)
        base = float(np.mean([row["target"] for row in train]))
        predictions.extend(pred.tolist())
        actuals.extend(float(row["target"]) for row in test)
        baselines.extend([base] * len(test))
        fold_rows.append({
            "fold": index,
            "train_count": len(train),
            "test_count": len(test),
            "train_group_end": max(
                int(row["time_group"]) for row in train
            ),
            "test_group_start": min(chunk),
            "test_group_end": max(chunk),
        })
    if not actuals:
        return {"status": "insufficient_data", "count": len(rows)}
    y = np.asarray(actuals)
    pred = np.asarray(predictions)
    base = np.asarray(baselines)
    allow = pred > 0.0
    return {
        "status": "complete",
        "test_count": len(y),
        "folds": fold_rows,
        "rmse": round(float(np.sqrt(np.mean((pred - y) ** 2))), 6),
        "baseline_rmse": round(float(np.sqrt(np.mean((base - y) ** 2))), 6),
        "mae": round(float(np.mean(np.abs(pred - y))), 6),
        "correlation": round(float(np.corrcoef(pred, y)[0, 1]), 6)
        if len(y) > 2 and np.std(pred) > 0 and np.std(y) > 0 else None,
        "all_avg_target": round(float(y.mean()), 6),
        "shadow_allow_count": int(allow.sum()),
        "shadow_allow_avg_target": round(float(y[allow].mean()), 6)
        if allow.any() else None,
        "shadow_allow_max_drawdown": _drawdown(y[allow]),
    }


def _drawdown(values: np.ndarray) -> float:
    running = 0.0
    peak = 0.0
    worst = 0.0
    for value in values.tolist():
        running += float(value)
        peak = max(peak, running)
        worst = min(worst, running - peak)
    return round(abs(worst), 6)


def _gates(coverage: dict, metrics: dict, min_test: int) -> dict:
    checks = {
        "coverage_at_least_95pct": float(coverage.get("coverage_pct") or 0) >= 95,
        "exact_snapshot_at_least_90pct": float(coverage.get("exact_snapshot_pct") or 0) >= 90,
        "grouped_test_sample": int(metrics.get("test_count") or 0) >= min_test,
        "rmse_beats_temporal_baseline": (
            metrics.get("rmse") is not None
            and metrics.get("baseline_rmse") is not None
            and metrics["rmse"] < metrics["baseline_rmse"]
        ),
        "shadow_allow_sample_at_least_20": int(metrics.get("shadow_allow_count") or 0) >= 20,
        "shadow_allow_positive_net_utility": (
            metrics.get("shadow_allow_avg_target") is not None
            and metrics["shadow_allow_avg_target"] > 0
        ),
        "shadow_allow_beats_unfiltered": (
            metrics.get("shadow_allow_avg_target") is not None
            and metrics.get("all_avg_target") is not None
            and metrics["shadow_allow_avg_target"] > metrics["all_avg_target"]
        ),
    }
    return {
        "checks": checks,
        "passed": sum(bool(value) for value in checks.values()),
        "total": len(checks),
        "evidence_ready": all(checks.values()),
        "authority_mode": AUTHORITY_MODE,
        "authority_eligible": False,
    }


def _init(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS meta_label_v2_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            since_timestamp TEXT,
            coverage_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            gates_json TEXT NOT NULL
        )
    """)
    con.commit()


def _persist(
    edge_db: Path,
    since: str | None,
    coverage: dict,
    metrics: dict,
    gates: dict,
) -> int:
    con = sqlite3.connect(edge_db)
    _init(con)
    cur = con.execute("""
        INSERT INTO meta_label_v2_runs(
            version, generated_at, since_timestamp,
            coverage_json, metrics_json, gates_json
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (
        VERSION,
        datetime.now(timezone.utc).isoformat(),
        since,
        json.dumps(coverage, sort_keys=True, separators=(",", ":")),
        json.dumps(metrics, sort_keys=True, separators=(",", ":")),
        json.dumps(gates, sort_keys=True, separators=(",", ":")),
    ))
    con.commit()
    run_id = int(cur.lastrowid)
    con.close()
    return run_id
