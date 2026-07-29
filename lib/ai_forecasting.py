"""Forward-only, shadow-only probabilistic forecasting for Matrix Trader.

This module records structured model probabilities and evaluates them after
fixed horizons. It has no function that can change signal conviction, strategy
configuration, paper orders, leverage, or live execution.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Callable

from lib.ai_client import AIResult, call_ai, provider_status


FORECAST_VERSION = "mt7_forward_v1"
HORIZONS = (15, 60, 240)
RETURN_THRESHOLDS = {15: 0.15, 60: 0.30, 240: 0.60}
ROUND_TRIP_COST_PCT = 0.12
MAX_MODELS = 2
MAX_DAILY_CALL_CAP = 50
FRESH_SIGNAL_MINUTES = 5
MIN_EVALUATION_DELAY_SECONDS = 90

FORECAST_SYSTEM = """You are a probabilistic shadow forecaster inside Matrix Trader 7.
You do not give trade advice and you cannot change signal conviction, risk gates,
position size, leverage, paper orders, or execution.

Forecast market-price direction relative to snapshot_price at 15, 60, and 240
minutes after signal_logged_at. Return exactly one JSON object and no markdown:
{
  "abstain": false,
  "risk_flags": ["snake_case"],
  "drivers": ["snake_case"],
  "horizons": {
    "15": {"p_up": 0.34, "p_flat": 0.33, "p_down": 0.33},
    "60": {"p_up": 0.34, "p_flat": 0.33, "p_down": 0.33},
    "240": {"p_up": 0.34, "p_flat": 0.33, "p_down": 0.33}
  }
}
Each horizon's probabilities must be numbers from 0 to 1 and sum to 1.
Use abstain=true when evidence is insufficient or internally contradictory.
Drivers and risk_flags must be concise labels, not narrative. Never claim certainty."""


def ensure_forecast_tables(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10)
    try:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS ai_shadow_forecasts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id        INTEGER NOT NULL,
                created_at       TEXT NOT NULL,
                forecast_version TEXT NOT NULL,
                provider         TEXT NOT NULL,
                model            TEXT NOT NULL,
                role             TEXT NOT NULL,
                status           TEXT NOT NULL,
                signal_logged_at TEXT NOT NULL,
                symbol           TEXT NOT NULL,
                exchange         TEXT NOT NULL,
                signal_direction TEXT NOT NULL,
                signal_price     REAL NOT NULL,
                conviction       INTEGER NOT NULL,
                strategy_key     TEXT,
                atr_pct          REAL,
                volatility       TEXT,
                funding_rate     REAL,
                rsi_1h           REAL,
                trend_score      REAL,
                flow_score       REAL,
                tags             TEXT,
                latency_ms       INTEGER NOT NULL DEFAULT 0,
                response_valid   INTEGER NOT NULL DEFAULT 0,
                abstain          INTEGER NOT NULL DEFAULT 0,
                risk_flags       TEXT,
                drivers          TEXT,
                error            TEXT,
                UNIQUE(signal_id, provider, model, forecast_version)
            );
            CREATE INDEX IF NOT EXISTS idx_ai_shadow_forecasts_created
            ON ai_shadow_forecasts (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_shadow_forecasts_model
            ON ai_shadow_forecasts (provider, model, role, status);

            CREATE TABLE IF NOT EXISTS ai_shadow_forecast_horizons (
                id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                forecast_id                INTEGER NOT NULL,
                horizon_minutes            INTEGER NOT NULL,
                due_at                     TEXT NOT NULL,
                p_up                       REAL NOT NULL,
                p_flat                     REAL NOT NULL,
                p_down                     REAL NOT NULL,
                predicted_class            TEXT NOT NULL,
                confidence                 REAL NOT NULL,
                evaluated_at               TEXT,
                actual_price               REAL,
                actual_return_pct           REAL,
                actual_class               TEXT,
                threshold_pct              REAL NOT NULL,
                brier_score                REAL,
                direction_correct          INTEGER,
                abstention_correct          INTEGER,
                model_net_return_pct        REAL,
                flat_baseline_brier         REAL,
                signal_baseline_brier       REAL,
                signal_baseline_class       TEXT,
                signal_baseline_net_return_pct REAL,
                FOREIGN KEY(forecast_id) REFERENCES ai_shadow_forecasts(id),
                UNIQUE(forecast_id, horizon_minutes)
            );
            CREATE INDEX IF NOT EXISTS idx_ai_shadow_horizons_due
            ON ai_shadow_forecast_horizons (evaluated_at, due_at);
        """)
        con.commit()
    finally:
        con.close()


def normalized_forecast_config(settings: dict | None) -> dict:
    settings = settings if isinstance(settings, dict) else {}
    models = []
    seen_roles = set()
    seen_models = set()
    for item in settings.get("shadow_forecast_models") or []:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip()
        model = str(item.get("model") or "").strip()
        role = str(item.get("role") or "").strip().lower()
        key = (provider, model)
        if (
            provider
            and model
            and role in {"champion", "challenger"}
            and role not in seen_roles
            and key not in seen_models
        ):
            models.append({"provider": provider, "model": model, "role": role})
            seen_roles.add(role)
            seen_models.add(key)
        if len(models) >= MAX_MODELS:
            break
    try:
        min_conviction = int(settings.get("shadow_forecast_min_conviction", 70))
    except (TypeError, ValueError):
        min_conviction = 70
    try:
        daily_call_cap = int(settings.get("shadow_forecast_daily_call_cap", 12))
    except (TypeError, ValueError):
        daily_call_cap = 12
    try:
        target = int(settings.get("shadow_forecast_target", 50))
    except (TypeError, ValueError):
        target = 50
    return {
        "enabled": bool(settings.get("shadow_forecasting_enabled", False)),
        "models": models,
        "min_conviction": max(55, min(100, min_conviction)),
        "daily_call_cap": max(1, min(MAX_DAILY_CALL_CAP, daily_call_cap)),
        "target": max(20, min(500, target)),
        "horizons": list(HORIZONS),
        "fresh_signal_minutes": FRESH_SIGNAL_MINUTES,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "version": FORECAST_VERSION,
    }


def _safe_json_object(text: str) -> dict | None:
    clean = (text or "").strip()
    if clean.startswith("```"):
        clean = clean.replace("```json", "", 1).replace("```", "", 1).strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(clean[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def parse_forecast_response(text: str) -> dict:
    parsed = _safe_json_object(text)
    if parsed is None:
        return {"valid": False, "error": "invalid JSON response"}
    horizons_raw = parsed.get("horizons")
    if not isinstance(horizons_raw, dict):
        return {"valid": False, "error": "missing horizons object"}

    horizons = {}
    for horizon in HORIZONS:
        raw = horizons_raw.get(str(horizon))
        if not isinstance(raw, dict):
            return {"valid": False, "error": f"missing {horizon} minute horizon"}
        try:
            values = [
                float(raw["p_up"]),
                float(raw["p_flat"]),
                float(raw["p_down"]),
            ]
        except (KeyError, TypeError, ValueError):
            return {"valid": False, "error": f"invalid {horizon} minute probabilities"}
        if any(not math.isfinite(value) or value < 0 or value > 1 for value in values):
            return {"valid": False, "error": f"out-of-range {horizon} minute probabilities"}
        total = sum(values)
        if total < 0.98 or total > 1.02:
            return {"valid": False, "error": f"{horizon} minute probabilities do not sum to 1"}
        p_up, p_flat, p_down = [value / total for value in values]
        classes = {"UP": p_up, "FLAT": p_flat, "DOWN": p_down}
        predicted = max(classes, key=classes.get)
        horizons[horizon] = {
            "p_up": round(p_up, 6),
            "p_flat": round(p_flat, 6),
            "p_down": round(p_down, 6),
            "predicted_class": predicted,
            "confidence": round(classes[predicted], 6),
        }

    def labels(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [
            str(item).strip().lower()[:64]
            for item in value[:12]
            if str(item).strip()
        ]

    return {
        "valid": True,
        "abstain": bool(parsed.get("abstain", False)),
        "risk_flags": labels(parsed.get("risk_flags")),
        "drivers": labels(parsed.get("drivers")),
        "horizons": horizons,
        "error": "",
    }


def classify_return(return_pct: float, threshold_pct: float) -> str:
    if return_pct > threshold_pct:
        return "UP"
    if return_pct < -threshold_pct:
        return "DOWN"
    return "FLAT"


def multiclass_brier(probabilities: dict[str, float], actual_class: str) -> float:
    return round(sum(
        (float(probabilities.get(label, 0)) - (1.0 if label == actual_class else 0.0)) ** 2
        for label in ("UP", "FLAT", "DOWN")
    ), 6)


def signal_baseline_probabilities(direction: str, conviction: int) -> dict[str, float]:
    directional = min(0.70, max(0.50, 0.50 + (int(conviction) - 55) * 0.005))
    flat = 0.20
    opposite = 1.0 - directional - flat
    if str(direction).upper() == "SHORT":
        return {"UP": opposite, "FLAT": flat, "DOWN": directional}
    return {"UP": directional, "FLAT": flat, "DOWN": opposite}


def _snapshot_signal(row: dict) -> dict:
    signal_json = {}
    try:
        signal_json = json.loads(row.get("signal_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        signal_json = {}
    return {
        "signal_id": int(row["id"]),
        "signal_logged_at": row["logged_at"],
        "symbol": row["symbol"],
        "exchange": str(row.get("exchange") or "MEXC").upper(),
        "signal_direction": str(row.get("direction") or "").upper(),
        "signal_price": float(row.get("price") or 0),
        "conviction": int(row.get("conviction") or 0),
        "strategy_key": row.get("strategy_key") or "",
        "atr_pct": row.get("atr_pct"),
        "volatility": row.get("volatility") or "",
        "funding_rate": row.get("funding_rate"),
        "rsi_1h": row.get("rsi_1h"),
        "trend_score": row.get("trend_score"),
        "flow_score": row.get("flow_score"),
        "tags": row.get("tags") or "",
        "agent_regime": signal_json.get("agent_regime") or "",
        "change_24h_pct": signal_json.get("change_24h_pct"),
        "data_quality": row.get("data_quality") or "",
    }


def _forecast_prompt(snapshot: dict) -> str:
    public_snapshot = {
        key: value
        for key, value in snapshot.items()
        if key not in {"signal_id"}
    }
    return (
        "Produce the required probability object for this frozen MT7 market snapshot. "
        "Do not infer any data newer than signal_logged_at.\n"
        + json.dumps(public_snapshot, sort_keys=True, default=str)
    )


def _insert_running_forecast(
    db_path: str | Path,
    snapshot: dict,
    candidate: dict,
) -> int | None:
    con = sqlite3.connect(str(db_path), timeout=10)
    try:
        cursor = con.execute(
            """
            INSERT OR IGNORE INTO ai_shadow_forecasts
                (signal_id, created_at, forecast_version, provider, model, role,
                 status, signal_logged_at, symbol, exchange, signal_direction,
                 signal_price, conviction, strategy_key, atr_pct, volatility,
                 funding_rate, rsi_1h, trend_score, flow_score, tags)
            VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["signal_id"],
                datetime.now(timezone.utc).isoformat(),
                FORECAST_VERSION,
                candidate["provider"],
                candidate["model"],
                candidate["role"],
                snapshot["signal_logged_at"],
                snapshot["symbol"],
                snapshot["exchange"],
                snapshot["signal_direction"],
                snapshot["signal_price"],
                snapshot["conviction"],
                snapshot["strategy_key"],
                snapshot["atr_pct"],
                snapshot["volatility"],
                snapshot["funding_rate"],
                snapshot["rsi_1h"],
                snapshot["trend_score"],
                snapshot["flow_score"],
                snapshot["tags"],
            ),
        )
        con.commit()
        return int(cursor.lastrowid) if cursor.rowcount else None
    finally:
        con.close()


def _complete_forecast(
    db_path: str | Path,
    forecast_id: int,
    snapshot: dict,
    parsed: dict,
    latency_ms: int,
) -> None:
    con = sqlite3.connect(str(db_path), timeout=10)
    try:
        con.execute(
            """
            UPDATE ai_shadow_forecasts
            SET status='complete', latency_ms=?, response_valid=1, abstain=?,
                risk_flags=?, drivers=?, error=NULL
            WHERE id=?
            """,
            (
                int(latency_ms),
                int(bool(parsed["abstain"])),
                json.dumps(parsed["risk_flags"]),
                json.dumps(parsed["drivers"]),
                forecast_id,
            ),
        )
        logged_dt = datetime.fromisoformat(snapshot["signal_logged_at"])
        if logged_dt.tzinfo is None:
            logged_dt = logged_dt.replace(tzinfo=timezone.utc)
        signal_probs = signal_baseline_probabilities(
            snapshot["signal_direction"],
            snapshot["conviction"],
        )
        signal_class = max(signal_probs, key=signal_probs.get)
        for horizon, probabilities in parsed["horizons"].items():
            due_at = (logged_dt + timedelta(minutes=int(horizon))).isoformat()
            con.execute(
                """
                INSERT INTO ai_shadow_forecast_horizons
                    (forecast_id, horizon_minutes, due_at, p_up, p_flat, p_down,
                     predicted_class, confidence, threshold_pct, signal_baseline_class)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    forecast_id,
                    int(horizon),
                    due_at,
                    probabilities["p_up"],
                    probabilities["p_flat"],
                    probabilities["p_down"],
                    probabilities["predicted_class"],
                    probabilities["confidence"],
                    RETURN_THRESHOLDS[int(horizon)],
                    signal_class,
                ),
            )
        con.commit()
    finally:
        con.close()


def _fail_forecast(
    db_path: str | Path,
    forecast_id: int,
    error: str,
    latency_ms: int = 0,
) -> None:
    con = sqlite3.connect(str(db_path), timeout=10)
    try:
        con.execute(
            """
            UPDATE ai_shadow_forecasts
            SET status='failed', latency_ms=?, response_valid=0, error=?
            WHERE id=?
            """,
            (int(latency_ms), (error or "forecast failed")[:300], forecast_id),
        )
        con.commit()
    finally:
        con.close()


def collect_forecasts_once(
    db_path: str | Path,
    settings: dict,
    *,
    max_signals_per_cycle: int = 2,
) -> dict:
    """Collect bounded forecasts for fresh signals. Safe to call repeatedly."""
    ensure_forecast_tables(db_path)
    config = normalized_forecast_config(settings)
    if not config["enabled"]:
        return {"enabled": False, "created": 0, "completed": 0, "failed": 0}
    if not config["models"]:
        return {"enabled": True, "created": 0, "completed": 0, "failed": 0, "error": "no models configured"}

    status = {item["provider"]: item for item in provider_status(settings)}
    runnable = []
    skipped_circuits = []
    for candidate in config["models"]:
        provider = status.get(candidate["provider"]) or {}
        circuit_state = (provider.get("circuit") or {}).get("state") or "closed"
        if provider.get("available") and circuit_state in {"closed", "probe_ready"}:
            runnable.append(candidate)
        else:
            skipped_circuits.append({
                "provider": candidate["provider"],
                "state": circuit_state if provider.get("available") else "unconfigured",
            })
    if not runnable:
        return {
            "enabled": True,
            "created": 0,
            "completed": 0,
            "failed": 0,
            "skipped_providers": skipped_circuits,
        }

    con = sqlite3.connect(str(db_path), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        con.execute(
            """
            UPDATE ai_shadow_forecasts
            SET status='failed', error='collector interrupted before completion'
            WHERE status='running' AND created_at < ?
            """,
            (stale_cutoff,),
        )
        day_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
        used_today = int(con.execute(
            """
            SELECT COUNT(*) FROM ai_shadow_forecasts
            WHERE created_at >= ? AND forecast_version=?
            """,
            (day_start, FORECAST_VERSION),
        ).fetchone()[0])
        remaining = max(0, config["daily_call_cap"] - used_today)
        if remaining < len(runnable):
            con.commit()
            return {
                "enabled": True,
                "created": 0,
                "completed": 0,
                "failed": 0,
                "daily_cap_reached": True,
                "used_today": used_today,
            }
        fresh_cutoff = (
            datetime.utcnow() - timedelta(minutes=FRESH_SIGNAL_MINUTES)
        ).isoformat()
        rows = con.execute(
            """
            SELECT id, logged_at, symbol, exchange, direction, price, conviction,
                   strategy_key, atr_pct, volatility, funding_rate, rsi_1h,
                   trend_score, flow_score, tags, signal_json, data_quality
            FROM signals
            WHERE logged_at >= ?
              AND conviction >= ?
              AND price > 0
              AND direction IN ('LONG', 'SHORT')
              AND COALESCE(source, 'live') = 'live'
              AND COALESCE(data_quality, 'current') = 'current'
            ORDER BY conviction DESC, logged_at DESC
            LIMIT 30
            """,
            (fresh_cutoff, config["min_conviction"]),
        ).fetchall()
        con.commit()
    finally:
        con.close()

    selected = []
    seen_symbols = set()
    for row in rows:
        snapshot = _snapshot_signal(dict(row))
        symbol_key = (snapshot["exchange"], snapshot["symbol"])
        if symbol_key in seen_symbols:
            continue
        con = sqlite3.connect(str(db_path), timeout=10)
        try:
            existing = {
                (row[0], row[1])
                for row in con.execute(
                """
                SELECT provider, model FROM ai_shadow_forecasts
                WHERE signal_id=? AND forecast_version=?
                """,
                (snapshot["signal_id"], FORECAST_VERSION),
                ).fetchall()
            }
        finally:
            con.close()
        if all(
            (candidate["provider"], candidate["model"]) in existing
            for candidate in runnable
        ):
            continue
        selected.append(snapshot)
        seen_symbols.add(symbol_key)
        if len(selected) >= max(1, int(max_signals_per_cycle)):
            break

    created = completed = failed = 0
    for snapshot in selected:
        if created + len(runnable) > remaining:
            break
        for candidate in runnable:
            forecast_id = _insert_running_forecast(db_path, snapshot, candidate)
            if forecast_id is None:
                continue
            created += 1
            result = call_ai(
                FORECAST_SYSTEM,
                _forecast_prompt(snapshot),
                max_tokens=500,
                provider=candidate["provider"],
                model=candidate["model"],
                feature="shadow_forecast",
                return_result=True,
                fallback_policy_override="selected_only",
            )
            if not isinstance(result, AIResult):
                _fail_forecast(
                    db_path,
                    forecast_id,
                    "provider unavailable, failed, or circuit open",
                )
                failed += 1
                continue
            parsed = parse_forecast_response(result.text)
            if not parsed["valid"]:
                _fail_forecast(
                    db_path,
                    forecast_id,
                    parsed["error"],
                    result.latency_ms,
                )
                failed += 1
                continue
            _complete_forecast(
                db_path,
                forecast_id,
                snapshot,
                parsed,
                result.latency_ms,
            )
            completed += 1
            time.sleep(1)
    return {
        "enabled": True,
        "created": created,
        "completed": completed,
        "failed": failed,
        "used_today_before_cycle": used_today,
        "daily_call_cap": config["daily_call_cap"],
        "skipped_providers": skipped_circuits,
    }


def evaluate_due_forecasts(
    db_path: str | Path,
    price_resolver: Callable[[dict, int], tuple[float, str] | None],
    *,
    limit: int = 60,
) -> dict:
    """Resolve due horizons with exchange candles and persist forward scores."""
    ensure_forecast_tables(db_path)
    now = datetime.now(timezone.utc)
    due_cutoff = (now - timedelta(seconds=MIN_EVALUATION_DELAY_SECONDS)).isoformat()
    con = sqlite3.connect(str(db_path), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in con.execute(
                """
                SELECT h.*, f.provider, f.model, f.role, f.symbol, f.exchange,
                       f.signal_price, f.signal_direction, f.conviction,
                       f.abstain, f.volatility, f.signal_logged_at
                FROM ai_shadow_forecast_horizons h
                JOIN ai_shadow_forecasts f ON f.id=h.forecast_id
                WHERE h.evaluated_at IS NULL
                  AND h.due_at <= ?
                  AND f.status='complete'
                ORDER BY h.due_at ASC
                LIMIT ?
                """,
                (due_cutoff, max(1, min(int(limit), 500))),
            ).fetchall()
        ]
    finally:
        con.close()

    evaluated = unresolved = 0
    cache: dict[tuple[str, str, int], tuple[float, str] | None] = {}
    for row in rows:
        target_dt = datetime.fromisoformat(row["due_at"])
        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=timezone.utc)
        target_ts = int(target_dt.timestamp())
        cache_key = (row["exchange"], row["symbol"], target_ts)
        if cache_key not in cache:
            cache[cache_key] = price_resolver(row, target_ts)
        resolved = cache[cache_key]
        if not resolved:
            unresolved += 1
            continue
        actual_price, price_at = resolved
        signal_price = float(row["signal_price"])
        if signal_price <= 0 or actual_price <= 0:
            unresolved += 1
            continue
        return_pct = (actual_price / signal_price - 1.0) * 100
        threshold = float(row["threshold_pct"])
        actual_class = classify_return(return_pct, threshold)
        probabilities = {
            "UP": float(row["p_up"]),
            "FLAT": float(row["p_flat"]),
            "DOWN": float(row["p_down"]),
        }
        model_brier = multiclass_brier(probabilities, actual_class)
        flat_probs = {"UP": 0.25, "FLAT": 0.50, "DOWN": 0.25}
        flat_brier = multiclass_brier(flat_probs, actual_class)
        signal_probs = signal_baseline_probabilities(
            row["signal_direction"],
            int(row["conviction"]),
        )
        signal_brier = multiclass_brier(signal_probs, actual_class)
        predicted = row["predicted_class"]
        abstain = bool(row["abstain"])
        direction_correct = None if abstain else int(predicted == actual_class)
        abstention_correct = int(actual_class == "FLAT") if abstain else None

        if abstain or predicted == "FLAT":
            model_net = 0.0
        elif predicted == "UP":
            model_net = return_pct - ROUND_TRIP_COST_PCT
        else:
            model_net = -return_pct - ROUND_TRIP_COST_PCT

        signal_class = row["signal_baseline_class"]
        if signal_class == "UP":
            signal_net = return_pct - ROUND_TRIP_COST_PCT
        elif signal_class == "DOWN":
            signal_net = -return_pct - ROUND_TRIP_COST_PCT
        else:
            signal_net = 0.0

        con = sqlite3.connect(str(db_path), timeout=10)
        try:
            con.execute(
                """
                UPDATE ai_shadow_forecast_horizons
                SET evaluated_at=?, actual_price=?, actual_return_pct=?,
                    actual_class=?, brier_score=?, direction_correct=?,
                    abstention_correct=?, model_net_return_pct=?,
                    flat_baseline_brier=?, signal_baseline_brier=?,
                    signal_baseline_net_return_pct=?
                WHERE id=? AND evaluated_at IS NULL
                """,
                (
                    price_at,
                    actual_price,
                    round(return_pct, 6),
                    actual_class,
                    model_brier,
                    direction_correct,
                    abstention_correct,
                    round(model_net, 6),
                    flat_brier,
                    signal_brier,
                    round(signal_net, 6),
                    row["id"],
                ),
            )
            con.commit()
        finally:
            con.close()
        evaluated += 1
    return {
        "due": len(rows),
        "evaluated": evaluated,
        "unresolved": unresolved,
    }


def _max_drawdown(values: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += float(value)
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 4)


def _profit_factor(values: list[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    if losses <= 0:
        return None if gains <= 0 else 999.0
    return round(gains / losses, 3)


def forecast_overview(db_path: str | Path, settings: dict) -> dict:
    ensure_forecast_tables(db_path)
    config = normalized_forecast_config(settings)
    con = sqlite3.connect(str(db_path), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        forecast_rows = [
            dict(row)
            for row in con.execute(
                """
                SELECT provider, model, role, status, response_valid, latency_ms,
                       created_at, volatility
                FROM ai_shadow_forecasts
                WHERE forecast_version=?
                ORDER BY id
                """,
                (FORECAST_VERSION,),
            ).fetchall()
        ]
        horizon_rows = [
            dict(row)
            for row in con.execute(
                """
                SELECT f.provider, f.model, f.role, f.abstain, f.volatility,
                       h.horizon_minutes, h.evaluated_at, h.actual_class,
                       h.predicted_class, h.brier_score, h.direction_correct,
                       h.abstention_correct, h.model_net_return_pct,
                       h.flat_baseline_brier, h.signal_baseline_brier,
                       h.signal_baseline_net_return_pct
                FROM ai_shadow_forecast_horizons h
                JOIN ai_shadow_forecasts f ON f.id=h.forecast_id
                WHERE f.forecast_version=?
                ORDER BY h.evaluated_at, h.id
                """,
                (FORECAST_VERSION,),
            ).fetchall()
        ]
    finally:
        con.close()

    call_groups: dict[tuple[str, str], dict] = {}
    for row in forecast_rows:
        key = (row["provider"], row["model"])
        group = call_groups.setdefault(key, {
            "provider": row["provider"],
            "model": row["model"],
            "role": row["role"],
            "calls": 0,
            "valid_calls": 0,
            "failed_calls": 0,
            "latencies": [],
        })
        group["calls"] += 1
        group["valid_calls"] += int(bool(row["response_valid"]))
        group["failed_calls"] += int(row["status"] == "failed")
        if row["latency_ms"]:
            group["latencies"].append(int(row["latency_ms"]))

    horizon_groups: dict[tuple[str, str, int], list[dict]] = {}
    regime_groups: dict[tuple[str, str, str], list[dict]] = {}
    for row in horizon_rows:
        if not row["evaluated_at"]:
            continue
        key = (row["provider"], row["model"], int(row["horizon_minutes"]))
        horizon_groups.setdefault(key, []).append(row)
        regime_key = (
            row["provider"],
            row["model"],
            str(row.get("volatility") or "unknown"),
        )
        regime_groups.setdefault(regime_key, []).append(row)

    models = []
    for key, calls in call_groups.items():
        provider, model = key
        horizon_stats = []
        evidence_ready = True
        for horizon in HORIZONS:
            rows = horizon_groups.get((provider, model, horizon), [])
            briers = [float(row["brier_score"]) for row in rows if row["brier_score"] is not None]
            flat_briers = [
                float(row["flat_baseline_brier"])
                for row in rows if row["flat_baseline_brier"] is not None
            ]
            signal_briers = [
                float(row["signal_baseline_brier"])
                for row in rows if row["signal_baseline_brier"] is not None
            ]
            net_returns = [
                float(row["model_net_return_pct"] or 0)
                for row in rows
            ]
            signal_returns = [
                float(row["signal_baseline_net_return_pct"] or 0)
                for row in rows
            ]
            actionable = [
                row for row in rows
                if not row["abstain"] and row["predicted_class"] in {"UP", "DOWN"}
            ]
            correct = sum(int(row["direction_correct"] or 0) for row in actionable)
            avg_brier = sum(briers) / len(briers) if briers else None
            avg_flat = sum(flat_briers) / len(flat_briers) if flat_briers else None
            avg_signal = sum(signal_briers) / len(signal_briers) if signal_briers else None
            avg_net = sum(net_returns) / len(net_returns) if net_returns else None
            horizon_ready = bool(
                len(rows) >= config["target"]
                and avg_brier is not None
                and avg_signal is not None
                and avg_brier < avg_signal
                and avg_net is not None
                and avg_net > 0
                and (
                    (correct / len(actionable) * 100) >= 50
                    if actionable else False
                )
            )
            evidence_ready = evidence_ready and horizon_ready
            horizon_stats.append({
                "horizon_minutes": horizon,
                "evaluated": len(rows),
                "target": config["target"],
                "brier_score": round(avg_brier, 4) if avg_brier is not None else None,
                "flat_baseline_brier": round(avg_flat, 4) if avg_flat is not None else None,
                "signal_baseline_brier": round(avg_signal, 4) if avg_signal is not None else None,
                "skill_vs_flat_pct": (
                    round((1 - avg_brier / avg_flat) * 100, 2)
                    if avg_brier is not None and avg_flat
                    else None
                ),
                "skill_vs_signal_pct": (
                    round((1 - avg_brier / avg_signal) * 100, 2)
                    if avg_brier is not None and avg_signal
                    else None
                ),
                "actionable_predictions": len(actionable),
                "direction_accuracy_pct": (
                    round(correct / len(actionable) * 100, 1)
                    if actionable else None
                ),
                "abstention_rate_pct": (
                    round(sum(int(row["abstain"]) for row in rows) / len(rows) * 100, 1)
                    if rows else None
                ),
                "avg_net_return_pct": round(avg_net, 4) if avg_net is not None else None,
                "signal_baseline_avg_net_return_pct": (
                    round(sum(signal_returns) / len(signal_returns), 4)
                    if signal_returns else None
                ),
                "profit_factor": _profit_factor(net_returns),
                "shadow_max_drawdown_pct": _max_drawdown(net_returns),
                "evidence_ready": horizon_ready,
            })

        calls_count = calls["calls"]
        valid_rate = calls["valid_calls"] / calls_count * 100 if calls_count else 0
        evidence_ready = evidence_ready and valid_rate >= 95
        models.append({
            "provider": provider,
            "model": model,
            "role": calls["role"],
            "calls": calls_count,
            "valid_calls": calls["valid_calls"],
            "valid_rate_pct": round(valid_rate, 1),
            "failed_calls": calls["failed_calls"],
            "median_latency_ms": (
                int(median(calls["latencies"])) if calls["latencies"] else None
            ),
            "status": "evidence_ready" if evidence_ready else "collecting",
            "horizons": horizon_stats,
        })

    regimes = []
    for (provider, model, volatility), rows in regime_groups.items():
        net = [float(row["model_net_return_pct"] or 0) for row in rows]
        briers = [float(row["brier_score"]) for row in rows if row["brier_score"] is not None]
        regimes.append({
            "provider": provider,
            "model": model,
            "volatility": volatility,
            "evaluated": len(rows),
            "avg_net_return_pct": round(sum(net) / len(net), 4) if net else None,
            "brier_score": round(sum(briers) / len(briers), 4) if briers else None,
        })
    regimes.sort(key=lambda item: (item["provider"], item["model"], -item["evaluated"]))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    calls_today = sum(1 for row in forecast_rows if row["created_at"] >= today)
    return {
        "config": config,
        "calls_today": calls_today,
        "remaining_calls_today": max(0, config["daily_call_cap"] - calls_today),
        "models": models,
        "regimes": regimes,
        "total_forecasts": len(forecast_rows),
        "total_evaluated_horizons": sum(
            len(rows) for rows in horizon_groups.values()
        ),
        "safety": {
            "shadow_only": True,
            "affects_conviction": False,
            "affects_orders": False,
            "affects_execution": False,
            "stores_raw_responses": False,
            "unleveraged_research_returns": True,
        },
    }
