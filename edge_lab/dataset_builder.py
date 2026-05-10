from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from edge_lab.feature_engine import compute_features, features_json_for_row, has_required_features
from edge_lab.mexc_data import fetch_klines, fetch_tickers
from edge_lab.path_labeler import PATH_TEMPLATES, label_paths_from_arrays
from edge_lab.storage import (
    DB_PATH,
    connect,
    get_last_labeled_timestamp,
    get_status_counts,
    init_storage,
    insert_labels,
    mark_complete,
    mark_failed,
    mark_running,
    mark_skipped,
    upsert_pending,
    utc_now,
)


@dataclass
class EdgeLabConfig:
    universe_mode: str = "all_eligible_safe"
    timeframe: str = "Min15"
    days: int = 90
    rolling_window: int = 500
    min_periods: int = 100
    forward_horizon_candles: int = 96
    batch_size: int = 25
    max_runtime_minutes: int = 45
    mode: str = "backfill"
    resume: bool = False
    min_volume_24h: float = 0.0
    anchor_symbols: list[str] = field(default_factory=lambda: ["BTC_USDT", "ETH_USDT", "SOL_USDT"])
    manual_exclude_symbols: list[str] = field(default_factory=list)
    max_symbols: int | None = None
    symbols: list[str] | None = None
    top_n: int | None = None


def build_dataset(config: EdgeLabConfig) -> dict:
    started = time.monotonic()
    con = connect()
    init_storage(con)

    summary = {
        "mode": config.mode,
        "universe_mode": config.universe_mode,
        "symbols_discovered": 0,
        "symbols_eligible": 0,
        "symbols_processed_this_run": 0,
        "symbols_completed": 0,
        "symbols_skipped": 0,
        "symbols_failed": 0,
        "symbols_remaining": 0,
        "candles_fetched": 0,
        "rows_labeled": 0,
        "rows_inserted_updated": 0,
        "rows_skipped_warmup": 0,
        "rows_skipped_no_future": 0,
        "partial_history_warnings": [],
        "failures": [],
        "runtime_seconds": 0.0,
        "stopped_because": "complete",
        "db_path": str(DB_PATH),
        "generated_at": None,
        "path_templates": list(PATH_TEMPLATES.keys()),
    }

    try:
        tickers, ticker_error = fetch_tickers()
        if ticker_error and not config.symbols:
            summary["stopped_because"] = f"ticker_fetch_failed:{ticker_error}"
            return _finish_summary(con, summary, started, config)

        selected = _select_symbols(tickers, config)
        summary["symbols_discovered"] = len(tickers)
        summary["symbols_eligible"] = len(selected)

        for symbol in selected:
            upsert_pending(con, symbol, config.timeframe)
        con.commit()

        for symbol in selected:
            if _runtime_reached(started, config.max_runtime_minutes):
                summary["stopped_because"] = "max_runtime_reached"
                break

            if config.resume and _should_skip_for_resume(con, symbol, config.timeframe, config.mode):
                continue

            summary["symbols_processed_this_run"] += 1
            try:
                _process_symbol(con, symbol, config, summary)
                con.commit()
            except Exception as exc:
                mark_failed(con, symbol, config.timeframe, str(exc))
                con.commit()
                summary["symbols_failed"] += 1
                summary["failures"].append({"symbol": symbol, "reason": str(exc)[:500]})
                continue

        counts = get_status_counts(con, config.timeframe)
        summary["symbols_completed"] = counts.get("complete", 0)
        summary["symbols_skipped"] = counts.get("skipped", 0)
        summary["symbols_failed"] = counts.get("failed", 0)
        summary["symbols_remaining"] = max(
            0,
            summary["symbols_eligible"] - summary["symbols_completed"] - summary["symbols_skipped"] - summary["symbols_failed"],
        )
        return _finish_summary(con, summary, started, config)
    finally:
        con.close()


def _select_symbols(tickers: list[dict], config: EdgeLabConfig) -> list[str]:
    if config.symbols:
        requested = [s.strip().upper() for s in config.symbols if s.strip()]
        return _cap_symbols(requested, config)

    eligible = []
    exclude = set(config.manual_exclude_symbols)
    ticker_by_symbol = {}
    for t in tickers:
        symbol = str(t.get("symbol") or "").upper()
        ticker_by_symbol[symbol] = t
        if not symbol.endswith("_USDT") or symbol in exclude:
            continue
        last = _as_float(t.get("lastPrice") or t.get("last") or t.get("fairPrice"))
        volume = _as_float(t.get("volume24") or t.get("amount24") or t.get("holdVol") or 0)
        if last is None or last <= 0:
            continue
        if volume is None or volume < config.min_volume_24h:
            continue
        eligible.append((symbol, volume))

    eligible.sort(key=lambda x: x[1], reverse=True)
    if config.top_n:
        eligible = eligible[:config.top_n]

    symbols = [s for s, _ in eligible]
    for anchor in reversed(config.anchor_symbols):
        if anchor in ticker_by_symbol and anchor not in symbols:
            symbols.insert(0, anchor)
    return _cap_symbols(symbols, config)


def _cap_symbols(symbols: list[str], config: EdgeLabConfig) -> list[str]:
    deduped = list(dict.fromkeys(symbols))
    if config.max_symbols is not None:
        return deduped[:config.max_symbols]
    return deduped


def _process_symbol(con, symbol: str, config: EdgeLabConfig, summary: dict) -> None:
    mark_running(con, symbol, config.timeframe)
    con.commit()

    start_ts = None
    if config.mode == "incremental":
        last = get_last_labeled_timestamp(con, symbol, config.timeframe)
        if last:
            start_ts = max(0, last - (config.rolling_window + config.forward_horizon_candles) * 15 * 60)

    candles, meta = fetch_klines(symbol, interval=config.timeframe, days=config.days, start_ts=start_ts)
    candles_fetched = int(len(candles))
    summary["candles_fetched"] += candles_fetched
    if meta.get("partial_history"):
        summary["partial_history_warnings"].append({"symbol": symbol, "candles": candles_fetched})

    min_needed = config.min_periods + config.forward_horizon_candles + 1
    if candles_fetched < min_needed:
        mark_skipped(con, symbol, config.timeframe, "insufficient_history", candles_fetched)
        summary["symbols_skipped"] += 1
        return

    features = compute_features(candles, config.rolling_window, config.min_periods)
    labels: list[dict] = []
    rows_skipped_warmup = 0
    rows_skipped_no_future = 0
    last_labeled_ts = None
    incremental_floor = get_last_labeled_timestamp(con, symbol, config.timeframe) if config.mode == "incremental" else None
    highs = features["high"].to_numpy(dtype=float)
    lows = features["low"].to_numpy(dtype=float)
    closes = features["close"].to_numpy(dtype=float)

    for idx, row in features.iterrows():
        ts = int(row["timestamp"])
        if incremental_floor is not None and ts <= incremental_floor:
            continue
        if not has_required_features(row):
            rows_skipped_warmup += 1
            continue
        paths = label_paths_from_arrays(highs, lows, closes, idx, config.forward_horizon_candles)
        if paths is None:
            rows_skipped_no_future += 1
            continue
        labels.append({
            "symbol": symbol,
            "timeframe": config.timeframe,
            "timestamp": ts,
            "features": features_json_for_row(row),
            "paths": paths,
        })
        last_labeled_ts = ts

    inserted = 0
    for i in range(0, len(labels), config.batch_size):
        inserted += insert_labels(con, labels[i:i + config.batch_size])

    mark_complete(con, symbol, config.timeframe, candles_fetched, len(labels), last_labeled_ts)
    summary["rows_skipped_warmup"] += rows_skipped_warmup
    summary["rows_skipped_no_future"] += rows_skipped_no_future
    summary["rows_labeled"] += len(labels)
    summary["rows_inserted_updated"] += inserted


def _should_skip_for_resume(con, symbol: str, timeframe: str, mode: str) -> bool:
    if mode == "incremental":
        return _status_for_symbol(con, symbol, timeframe) in ("failed", "skipped")
    return _status_for_symbol(con, symbol, timeframe) in ("complete", "failed", "skipped")


def _status_for_symbol(con, symbol: str, timeframe: str) -> str | None:
    row = con.execute("""
        SELECT status FROM edge_lab_symbol_status
        WHERE symbol=? AND exchange='MEXC' AND timeframe=?
    """, (symbol, timeframe)).fetchone()
    return row[0] if row else None


def _runtime_reached(started: float, max_runtime_minutes: int) -> bool:
    return (time.monotonic() - started) >= max_runtime_minutes * 60


def _finish_summary(con, summary: dict, started: float, config: EdgeLabConfig) -> dict:
    counts = get_status_counts(con, config.timeframe)
    summary["symbols_completed"] = counts.get("complete", summary["symbols_completed"])
    summary["symbols_skipped"] = counts.get("skipped", summary["symbols_skipped"])
    summary["symbols_failed"] = counts.get("failed", summary["symbols_failed"])
    summary["runtime_seconds"] = round(time.monotonic() - started, 2)
    summary["generated_at"] = utc_now()
    return summary


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
