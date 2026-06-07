"""
Local MEXC public data fetchers for Edge Lab.

No imports from app.py. No private endpoints. All failures return safe empty
results with a reason so the builder can continue with other symbols.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests

BASE_URL = "https://contract.mexc.com/api/v1"
DEFAULT_TIMEOUT = 8
DEFAULT_RETRIES = 2
KLINE_LIMIT = 2000
MAX_CHUNK_ATTEMPTS = 20
INTERVAL_SECONDS = {
    "Min1": 60,
    "Min5": 5 * 60,
    "Min15": 15 * 60,
    "Min30": 30 * 60,
    "Min60": 60 * 60,
    "Hour4": 4 * 60 * 60,
    "Hour8": 8 * 60 * 60,
    "Day1": 24 * 60 * 60,
}


def _request_json(path: str, params: dict | None = None, retries: int = DEFAULT_RETRIES) -> tuple[Any, str | None]:
    url = f"{BASE_URL}{path}"
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params or {}, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            if not payload.get("success", False):
                return None, f"api_success_false:{payload.get('code') or payload.get('message')}"
            return payload.get("data"), None
        except Exception as exc:
            last_error = str(exc)
            if attempt < retries - 1:
                time.sleep(0.7 * (attempt + 1))
    return None, last_error or "request_failed"


def fetch_tickers() -> tuple[list[dict], str | None]:
    data, error = _request_json("/contract/ticker")
    if error:
        print(f"[edge_lab:mexc] ticker fetch failed: {error}", file=sys.stderr)
        return [], error
    if not isinstance(data, list):
        return [], "ticker_data_not_list"
    return [t for t in data if isinstance(t, dict)], None


def _kline_frame(raw: Any) -> pd.DataFrame:
    if not isinstance(raw, dict):
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame({
        "timestamp": raw.get("time", []),
        "open": raw.get("open", []),
        "high": raw.get("high", []),
        "low": raw.get("low", []),
        "close": raw.get("close", []),
        "volume": raw.get("vol", []),
    })
    if df.empty:
        return df

    for col in ["timestamp", "open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["timestamp", "open", "high", "low", "close"])
    df["timestamp"] = df["timestamp"].astype("int64")
    return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def fetch_klines(
    symbol: str,
    interval: str = "Min15",
    days: int = 90,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Fetch MEXC contract klines as timestamp/open/high/low/close/volume.

    MEXC caps klines at 2000 candles per request. This function walks
    backwards in time using KLINE_LIMIT-sized windows until the full
    requested range is covered or MAX_CHUNK_ATTEMPTS is reached.
    """
    now = datetime.now(timezone.utc)
    end = int(end_ts or now.timestamp())
    start = int(start_ts or (now - timedelta(days=days)).timestamp())
    errors: list[str] = []

    all_frames: list[pd.DataFrame] = []
    window_end = end

    for attempt in range(MAX_CHUNK_ATTEMPTS):
        params = {"interval": interval, "limit": KLINE_LIMIT, "end": window_end}
        raw, error = _request_json(f"/contract/kline/{symbol}", params=params)
        if error:
            errors.append(error)
            if attempt == 0:
                out = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
                meta = {
                    "symbol": symbol,
                    "interval": interval,
                    "requested_start": start,
                    "requested_end": end,
                    "candles": 0,
                    "partial_history": True,
                    "errors": errors,
                }
                return out, meta
            break

        chunk = _kline_frame(raw)
        if chunk.empty:
            break

        all_frames.append(chunk)
        earliest_ts = int(chunk["timestamp"].min())

        if earliest_ts <= start:
            break

        window_end = earliest_ts - 1
        if attempt < MAX_CHUNK_ATTEMPTS - 1:
            time.sleep(0.3)

    if all_frames:
        out = pd.concat(all_frames, ignore_index=True)
        out = out.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        out = out[out["timestamp"] >= start].reset_index(drop=True)
    else:
        out = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    interval_seconds = INTERVAL_SECONDS.get(interval, 15 * 60)
    requested_candles = max(1, int((end - start) / interval_seconds))
    meta = {
        "symbol": symbol,
        "interval": interval,
        "requested_start": start,
        "requested_end": end,
        "candles": int(len(out)),
        "partial_history": len(out) < int(requested_candles * 0.8),
        "errors": errors,
    }
    if meta["partial_history"]:
        print(f"[edge_lab:mexc] partial history {symbol}: candles={len(out)}", file=sys.stderr)
    return out, meta
