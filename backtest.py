#!/usr/bin/env python3
"""
Standalone backtest for Matrix Trader 7.0 strategies.

Usage:  python3 backtest.py
Output: formatted terminal report + data/backtest_results.json

Does NOT modify app.py or index.html.
Does NOT call enrich_signal() — all indicators computed directly from lib/.
"""

import bisect
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from lib.indicators import (
    rsi as calc_rsi,
    atr as calc_atr,
    atr_pct as calc_atr_pct,
    volatility_regime,
)
from lib.laddering import generate_ladders
from app import score_ticker, STRATEGIES

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MEXC_BASE  = "https://contract.mexc.com/api/v1"
SYMBOLS    = [
    # Majors
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT", "DOGE_USDT",
    # Alts
    "PEPE_USDT", "WIF_USDT", "BONK_USDT", "INJ_USDT",
    "TIA_USDT", "SEI_USDT", "RENDER_USDT", "JUP_USDT",
]
WINDOW     = 100   # history candles fed to indicators per window
STEP       = 4     # slide step (candles)
FORWARD    = 24    # forward candles to evaluate trade outcome
SLIPPAGE   = 0.001 # 0.1% entry price slippage
KLINES_MAX = 2000  # max candles to request per symbol


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_klines(symbol: str) -> pd.DataFrame | None:
    """
    Fetch up to KLINES_MAX 1h candles from MEXC for the given symbol.
    Returns a DataFrame with OHLCV columns, or None on failure.
    """
    end_ts   = int(time.time())
    start_ts = end_ts - KLINES_MAX * 3600

    try:
        resp = requests.get(
            f"{MEXC_BASE}/contract/kline/{symbol}",
            params={"interval": "Min60", "start": start_ts, "end": end_ts},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            return None
        data = body.get("data")
        if not data or not isinstance(data, dict):
            return None

        df = pd.DataFrame({
            "open":   data.get("open",  []),
            "high":   data.get("high",  []),
            "low":    data.get("low",   []),
            "close":  data.get("close", []),
            "volume": data.get("vol",   []),
        }).astype(float)

        if len(df) < 150:
            return None  # too little history — skip silently

        # Reconstruct hourly timestamps: align last candle to the most recent
        # completed hour boundary, then step back 3600s per candle.
        # Funding settle times are also round-hour aligned, so nearest-match works.
        last_ts = (end_ts // 3600) * 3600
        n = len(df)
        df["timestamp"] = [last_ts - (n - 1 - i) * 3600 for i in range(n)]

        return df

    except Exception as e:
        print(f"  [!] kline fetch error [{symbol}]: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Funding rate history
# ---------------------------------------------------------------------------

def fetch_funding_history(symbol: str, max_records: int = 400) -> tuple[dict[int, float], list[int]]:
    """
    Fetch up to max_records funding settlements from MEXC history endpoint.

    At 8h cadence, 400 records ≈ 133 days — covers the full ~83-day kline window.
    Returns (lookup, sorted_keys) where lookup maps settle_time_seconds → fundingRate
    and sorted_keys is pre-sorted for bisect lookups.
    """
    lookup: dict[int, float] = {}
    per_page = 100
    pages = -(-max_records // per_page)  # ceiling division

    for page in range(1, pages + 1):
        try:
            resp = requests.get(
                f"{MEXC_BASE}/contract/funding_rate/history",
                params={"symbol": symbol, "page_num": page, "page_size": per_page},
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
            if not body.get("success"):
                break
            result_list = (body.get("data") or {}).get("resultList") or []
            if not result_list:
                break
            for item in result_list:
                settle_s = int(item["settleTime"]) // 1000
                lookup[settle_s] = float(item["fundingRate"])
            if len(result_list) < per_page:
                break
        except Exception as e:
            print(f"  [!] funding history error [{symbol}] page {page}: {e}", file=sys.stderr)
            break

    sorted_keys = sorted(lookup)
    return lookup, sorted_keys


def lookup_funding(funding_map: dict[int, float], sorted_keys: list[int], ts: int) -> float:
    """
    Return the most recent funding rate at or before timestamp ts.
    Uses binary search — O(log n) per call.
    Returns 0.0 when no historical data is available.
    """
    if not sorted_keys:
        return 0.0
    idx = bisect.bisect_right(sorted_keys, ts) - 1
    return funding_map[sorted_keys[idx]] if idx >= 0 else 0.0


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def simulate_trade(
    df_forward: pd.DataFrame,
    entries: list[float],
    exits: list[float],
    stop_loss: float,
    direction: str,
) -> str:
    """
    Walk FORWARD candles candle-by-candle. Return "win", "loss", or "timeout".

    Entry assumed filled at entries[0] ± slippage (already applied by caller).
    Win condition: price touches exits[2] (TP3 — farthest take-profit).
    Loss condition: price touches stop_loss.
    First event in time wins. If neither fires: timeout.
    """
    tp_target = exits[2]
    sl_level  = stop_loss

    for _, row in df_forward.iterrows():
        high = float(row["high"])
        low  = float(row["low"])

        if direction == "LONG":
            if low  <= sl_level:  return "loss"
            if high >= tp_target: return "win"
        else:
            if high >= sl_level:  return "loss"
            if low  <= tp_target: return "win"

    return "timeout"


# ---------------------------------------------------------------------------
# Per-symbol × strategy backtest
# ---------------------------------------------------------------------------

def run_symbol_strategy(
    symbol: str,
    strat_key: str,
    strat: dict,
    df: pd.DataFrame,
    funding_map: dict[int, float],
    funding_keys: list[int],
) -> dict:
    """
    Slide a WINDOW-candle window across df with STEP stride.
    For each window, build a synthetic ticker, score it with stage-1,
    run stage-2 indicators, and simulate a forward trade.

    funding_map / funding_keys: pre-fetched funding history for nearest-match
    lookup by candle timestamp.

    Returns: {"win": int, "loss": int, "timeout": int, "skipped": int, "signals": int}
    """
    _empty_band = lambda: {"win": 0, "loss": 0, "timeout": 0}
    counts = {
        "win": 0, "loss": 0, "timeout": 0, "skipped": 0, "signals": 0,
        "bands": {"55_64": _empty_band(), "65_74": _empty_band(), "75p": _empty_band()},
    }
    min_conv = strat["min_conviction"]
    filters  = strat.get("filters", {})
    n_total  = len(df)

    i = 0
    while i + WINDOW + FORWARD <= n_total:
        window  = df.iloc[i : i + WINDOW]
        forward = df.iloc[i + WINDOW : i + WINDOW + FORWARD]
        i += STEP

        current_close = float(window["close"].iloc[-1])
        if current_close <= 0:
            continue

        # --- 24h price change (24 × 1h candles back) ---
        lookback = min(24, len(window) - 1)
        close_24h_ago = float(window["close"].iloc[-(lookback + 1)])
        rise_fall_rate = (
            (current_close - close_24h_ago) / close_24h_ago
            if close_24h_ago > 0 else 0.0
        )

        # --- 24h volume (sum of last 24 candle volumes × price ≈ USD volume) ---
        vol_24_base = window["volume"].iloc[-24:].sum() if len(window) >= 24 else window["volume"].sum()
        volume_usd  = vol_24_base * current_close

        # --- Funding rate: nearest settlement at or before this candle's time ---
        candle_ts   = int(window["timestamp"].iloc[-1])
        funding_rate = lookup_funding(funding_map, funding_keys, candle_ts)

        # --- Synthetic ticker dict matching score_ticker's expected fields ---
        ticker = {
            "symbol":       symbol,
            "lastPrice":    current_close,
            "fairPrice":    current_close,
            "riseFallRate": rise_fall_rate,  # decimal; score_ticker multiplies ×100
            "fundingRate":  funding_rate,    # real historical rate
            "volume24":     volume_usd,
            "holdVol":      0.0,
        }

        # --- Stage 1 ---
        base = score_ticker(ticker, strategy=strat)
        if base is None or base["conviction_base"] < min_conv:
            counts["skipped"] += 1
            continue

        direction = base["direction"]

        # --- Strategy stage-1 filter: min_24h_change_pct (already in score_ticker) ---
        # score_ticker returns None if the filter fails, handled above.

        # --- Stage 2: indicators directly from lib ---
        rsi_series = calc_rsi(window, period=14)
        rsi_clean  = rsi_series.dropna()
        rsi_val    = float(rsi_clean.iloc[-1]) if not rsi_clean.empty else 50.0

        atr_series = calc_atr(window, period=14)
        atr_clean  = atr_series.dropna()
        atr_val    = float(atr_clean.iloc[-1]) if not atr_clean.empty else current_close * 0.01
        if atr_val <= 0:
            atr_val = current_close * 0.01

        atr_pct_val = calc_atr_pct(window, current_close, period=14)
        vol_regime  = volatility_regime(atr_pct_val)

        # --- Strategy stage-2 RSI filter (mean_reversion) ---
        if "rsi_long_max" in filters and direction == "LONG":
            if rsi_val >= filters["rsi_long_max"]:
                counts["skipped"] += 1
                continue
        if "rsi_short_min" in filters and direction == "SHORT":
            if rsi_val <= filters["rsi_short_min"]:
                counts["skipped"] += 1
                continue

        # --- Conviction adjustments (mirrors enrich_signal exactly) ---
        conviction = base["conviction_base"]
        if direction == "LONG":
            if rsi_val < 40:  conviction += 10
            elif rsi_val > 70: conviction -= 10
        else:
            if rsi_val > 60:  conviction += 10
            elif rsi_val < 30: conviction -= 10

        if vol_regime == "extreme":
            conviction = int(conviction * 0.85)

        conviction = max(0, min(100, conviction))
        if conviction < min_conv:
            counts["skipped"] += 1
            continue

        # --- Generate ladders ---
        entries, exits, stop_loss = generate_ladders(
            current_price=current_close,
            atr_value=atr_val,
            tiers=3,
            direction=direction,
        )

        # Apply slippage to entry 1
        entry_fill = entries[0] * (1 + SLIPPAGE) if direction == "LONG" else entries[0] * (1 - SLIPPAGE)
        entries[0] = entry_fill  # only used for reference — simulate_trade uses exits/sl directly

        counts["signals"] += 1
        outcome = simulate_trade(forward, entries, exits, stop_loss, direction)
        counts[outcome] += 1

        band = "55_64" if conviction < 65 else ("65_74" if conviction < 75 else "75p")
        counts["bands"][band][outcome] += 1

    return counts


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def pct_str(n: int, d: int) -> str:
    return f"{n / d * 100:5.1f}%" if d > 0 else "  n/a "


def print_report(all_results: dict, strategy_totals: dict) -> None:
    print(f"\n{'='*72}")
    print("  Matrix Trader 7.0 — Backtest Results")
    print(f"  Symbols: {', '.join(SYMBOLS)}")
    print(f"  Window: {WINDOW}  |  Step: {STEP}  |  Forward: {FORWARD} candles  |  Slippage: {SLIPPAGE*100:.1f}%")
    print(f"{'='*72}")

    header = f"{'Symbol':<13} {'Strategy':<22} {'Signals':>7}  {'Win':>7}  {'Loss':>7}  {'T/O':>7}"
    print(f"\n{header}")
    print("─" * 72)

    for symbol in SYMBOLS:
        if symbol not in all_results:
            print(f"{symbol:<13} (no data)")
            continue
        for strat_key, strat in STRATEGIES.items():
            r = all_results[symbol].get(strat_key, {})
            n = r.get("signals", 0)
            line = (
                f"{symbol:<13} {strat['name']:<22} {n:>7}  "
                f"{pct_str(r.get('win', 0), n)}  "
                f"{pct_str(r.get('loss', 0), n)}  "
                f"{pct_str(r.get('timeout', 0), n)}"
            )
            print(line)
        print()

    print("─" * 72)
    print(f"{'TOTAL (all symbols)':<36} {'Signals':>7}  {'Win':>7}  {'Loss':>7}  {'T/O':>7}")
    print("─" * 72)
    band_defs = [("55–64", "55_64"), ("65–74", "65_74"), ("75+  ", "75p")]
    for strat_key, strat in STRATEGIES.items():
        t = strategy_totals[strat_key]
        n = t["signals"]
        print(
            f"{strat['name']:<36} {n:>7}  "
            f"{pct_str(t['win'], n)}  "
            f"{pct_str(t['loss'], n)}  "
            f"{pct_str(t['timeout'], n)}"
        )
        print("  Conviction bands:")
        for label, key in band_defs:
            b  = t["bands"][key]
            bn = b["win"] + b["loss"] + b["timeout"]
            print(f"    {label}:  {pct_str(b['win'], bn)} win  ({bn} signals)")
        print()
    print("─" * 72)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_backtest() -> None:
    all_results: dict = {}
    strategy_totals: dict = {
        k: {
            "win": 0, "loss": 0, "timeout": 0, "signals": 0,
            "bands": {
                "55_64": {"win": 0, "loss": 0, "timeout": 0},
                "65_74": {"win": 0, "loss": 0, "timeout": 0},
                "75p":   {"win": 0, "loss": 0, "timeout": 0},
            },
        }
        for k in STRATEGIES
    }

    for symbol in SYMBOLS:
        print(f"Fetching klines:  {symbol} ...", end=" ", flush=True)
        df = fetch_klines(symbol)
        if df is None:
            print("SKIPPED")
            continue
        print(f"{len(df)} candles")

        print(f"Fetching funding: {symbol} ...", end=" ", flush=True)
        funding_map, funding_keys = fetch_funding_history(symbol)
        print(f"{len(funding_keys)} settlements")

        all_results[symbol] = {}
        for strat_key, strat in STRATEGIES.items():
            result = run_symbol_strategy(symbol, strat_key, strat, df, funding_map, funding_keys)
            all_results[symbol][strat_key] = result
            for k in ("win", "loss", "timeout", "signals"):
                strategy_totals[strat_key][k] += result[k]
            for band in ("55_64", "65_74", "75p"):
                for k in ("win", "loss", "timeout"):
                    strategy_totals[strat_key]["bands"][band][k] += result["bands"][band][k]

    print_report(all_results, strategy_totals)

    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "config": {
            "symbols":      SYMBOLS,
            "window":       WINDOW,
            "step":         STEP,
            "forward":      FORWARD,
            "slippage_pct": round(SLIPPAGE * 100, 3),
            "klines_max":   KLINES_MAX,
        },
        "by_symbol":   all_results,
        "by_strategy": strategy_totals,
    }

    out_path = Path(__file__).parent / "data" / "backtest_results.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nSaved → {out_path}")


if __name__ == "__main__":
    run_backtest()
