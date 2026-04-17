import os
import sys
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import requests
import pandas as pd
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

from lib.indicators import (
    rsi as calc_rsi,
    ema as calc_ema,
    atr as calc_atr,
    atr_pct as calc_atr_pct,
    volatility_regime,
)
from lib.laddering import generate_ladders

load_dotenv()

app = Flask(__name__)

MEXC_BASE = "https://contract.mexc.com/api/v1"
PORT = int(os.getenv("MATRIX_PORT", "8080"))
CONVICTION_THRESHOLD = 55   # signals below this are filtered from results
KLINE_INTERVAL = "Min60"    # 1h candles — 100 candles default = ~4 days, plenty for 14-period indicators
ENRICH_TOP_N = 30           # enrich only the top N base signals to limit API calls
ENRICH_WORKERS = 10         # concurrent threads for stage-2 enrichment


# ---------------------------------------------------------------------------
# MEXC API helper
# ---------------------------------------------------------------------------

def fetch_mexc(path: str, params: dict | None = None) -> dict | list | None:
    """
    GET a MEXC contract API endpoint and unwrap the response envelope.

    All MEXC responses wrap their payload in {"success": true, "data": ...}.
    This helper returns the value of "data" directly, or None on any failure
    (network error, timeout, non-200, success=false). Callers treat None as
    "skip this ticker/symbol".
    """
    try:
        resp = requests.get(f"{MEXC_BASE}{path}", params=params, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            return None
        return body.get("data")
    except Exception as e:
        print(f"MEXC fetch error [{path}]: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Stage 1: lightweight ticker scoring
# ---------------------------------------------------------------------------

def score_ticker(ticker: dict) -> dict | None:
    """
    Score a single ticker using only the fields available in the /contract/ticker
    response. No additional API calls. Returns a base signal dict or None if
    the ticker is unusable (zero price, missing symbol, parse error).

    Scoring inputs:
      - riseFallRate: 24h price change (high weight, 0–30 pts)
      - fundingRate:  negative = shorts paying longs, bullish (high weight, 0–25 pts)
      - price vs fairPrice: basis spread (medium weight, 0–15 pts)
      - volume24:     high volume multiplies conviction by 1.1 (low weight)

    Direction is whichever side accumulates more points. Ties go to LONG.
    """
    try:
        symbol = ticker.get("symbol", "")
        price = float(ticker.get("lastPrice") or 0)
        fair_price = float(ticker.get("fairPrice") or price)
        change_pct = float(ticker.get("riseFallRate") or 0) * 100  # decimal → percent
        funding = float(ticker.get("fundingRate") or 0)
        volume = float(
            ticker.get("volume24") or ticker.get("vol24h") or ticker.get("amount24") or 0
        )
        open_interest = float(ticker.get("holdVol") or 0)

        if price <= 0 or not symbol:
            return None

        long_score = 0
        short_score = 0
        tags: list[str] = []

        # 24h momentum — high weight (0–30 pts)
        if change_pct > 5:
            long_score += 30
            tags.append("strong_momentum")
        elif change_pct > 2:
            long_score += 15
            tags.append("momentum")
        elif change_pct < -5:
            short_score += 30
            tags.append("strong_dump")
        elif change_pct < -2:
            short_score += 15
            tags.append("dump")

        # Funding rate — high weight (0–25 pts)
        # Negative funding = shorts paying longs = short-squeeze setup
        if funding < -0.001:
            long_score += 25
            tags.append("short_squeeze")
        elif funding < 0:
            long_score += 10
        elif funding > 0.001:
            short_score += 25
            tags.append("long_squeeze")
        elif funding > 0:
            short_score += 10

        # Basis spread — medium weight (0–15 pts)
        # Premium (price > fair): longs paying more, bearish lean
        # Discount (price < fair): shorts paying more, bullish lean
        if fair_price > 0:
            basis_pct = (price - fair_price) / fair_price * 100
            if basis_pct > 0.1:
                short_score += 15
                tags.append("premium")
            elif basis_pct < -0.1:
                long_score += 15
                tags.append("discount")

        # Volume — low weight, applied as a multiplier not additive points.
        # A 1.1× multiplier on a 30-pt signal adds 3 pts; on a 5-pt signal adds 0.5.
        # This rewards high-conviction signals in liquid markets without inflating
        # weak signals just because the coin has large absolute volume.
        vol_mult = 1.1 if volume > 10_000_000 else 1.0
        if volume > 10_000_000:
            tags.append("high_volume")

        if long_score >= short_score:
            direction = "LONG"
            conviction_base = min(int(long_score * vol_mult), 100)
        else:
            direction = "SHORT"
            conviction_base = min(int(short_score * vol_mult), 100)

        return {
            "symbol": symbol,
            "direction": direction,
            "conviction_base": conviction_base,
            "price": price,
            "change_24h_pct": round(change_pct, 4),
            "funding_rate": funding,
            "volume_24h": volume,
            "open_interest": open_interest,
            "tags": tags,
        }
    except Exception as e:
        print(f"score_ticker error [{ticker.get('symbol', '?')}]: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Stage 2: enrich with klines, depth, indicators, and ladders
# ---------------------------------------------------------------------------

def _parse_depth_volume(levels: list) -> float:
    """Sum quantity from depth levels regardless of whether each level is a
    [price, qty] list or a {"price": ..., "quantity": ...} dict."""
    total = 0.0
    for level in levels:
        try:
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                total += float(level[1])
            elif isinstance(level, dict):
                total += float(level.get("quantity") or level.get("qty") or 0)
        except (ValueError, TypeError):
            continue
    return total


def enrich_signal(base: dict) -> dict | None:
    """
    Stage 2: fetch klines and depth for one symbol, run RSI/ATR indicators,
    compute orderbook imbalance, adjust conviction, and generate entry/TP/SL
    ladders. Returns the full signal dict or None if data is insufficient.

    Conviction adjustments (on top of conviction_base from stage 1):
      +10 if RSI aligns with direction (oversold for LONG, overbought for SHORT)
      -10 if RSI contradicts direction
      +10 if orderbook imbalance aligns with direction
      ×0.85 in extreme volatility regime (5%+ ATR) — risky, reduce confidence

    Falls back gracefully at every step:
      - If klines are missing or too short: return None (skip the signal)
      - If depth is unavailable: use 0.5 (neutral) imbalance
      - If ATR is zero: fall back to 1% of price so generate_ladders never errors
    """
    symbol = base["symbol"]
    price = base["price"]
    direction = base["direction"]
    tags = list(base["tags"])

    try:
        # --- Klines ---
        # No start/end: MEXC returns the latest ~100 candles by default.
        # 100 × Hour1 = ~4 days, enough for 14-period ATR and RSI with headroom.
        kline_data = fetch_mexc(f"/contract/kline/{symbol}", params={"interval": KLINE_INTERVAL})

        if not kline_data or not isinstance(kline_data, dict):
            return None

        df = pd.DataFrame({
            "open":   kline_data.get("open", []),
            "high":   kline_data.get("high", []),
            "low":    kline_data.get("low", []),
            "close":  kline_data.get("close", []),
            "volume": kline_data.get("vol", []),
        }).astype(float)

        # Need at least period+2 rows for reliable ATR/RSI (14 + 2 = 16)
        if len(df) < 16:
            return None

        # --- Momentum: 1h and 4h price change ---
        # change_1h_pct: open→close on the most recent completed candle.
        # Using open rather than close[-2] because MEXC sometimes delivers the
        # current live candle as the last row — open/close of that row reflects
        # the in-progress move, which is still useful directional signal.
        try:
            last_open = float(df["open"].iloc[-1])
            last_close = float(df["close"].iloc[-1])
            change_1h_pct = round((last_close - last_open) / last_open * 100, 4) if last_open > 0 else 0.0
        except Exception:
            change_1h_pct = 0.0

        # change_4h_pct: close now vs close 4 candles ago (= 4 hours at Min60).
        # iloc[-5] is the close 4 periods back: [-1]=now, [-2]=1h, [-3]=2h, [-4]=3h, [-5]=4h.
        # We already require len(df) >= 16 so iloc[-5] is always safe.
        try:
            close_4h_ago = float(df["close"].iloc[-5])
            change_4h_pct = round((last_close - close_4h_ago) / close_4h_ago * 100, 4) if close_4h_ago > 0 else 0.0
        except Exception:
            change_4h_pct = 0.0

        # --- Indicators ---
        rsi_series = calc_rsi(df, period=14)
        rsi_clean = rsi_series.dropna()
        last_rsi = float(rsi_clean.iloc[-1]) if not rsi_clean.empty else 50.0

        atr_series = calc_atr(df, period=14)
        atr_clean = atr_series.dropna()
        atr_val = float(atr_clean.iloc[-1]) if not atr_clean.empty else 0.0
        if atr_val <= 0:
            atr_val = price * 0.01  # 1% of price as safe fallback

        atr_pct_val = calc_atr_pct(df, price, period=14)
        vol_regime = volatility_regime(atr_pct_val)

        # --- Trend score: EMA20/EMA50 alignment ---
        # EWM never produces NaN with adjust=False, but dropna() guards against
        # a column of NaNs from a degenerate input series.
        try:
            ema20_last = float(calc_ema(df, period=20).dropna().iloc[-1])
            ema50_last = float(calc_ema(df, period=50).dropna().iloc[-1])
            # Amplify the EMA gap 10× so a 1% gap → 10 pts, 10% gap → 100 pts (full range).
            raw = (ema20_last - ema50_last) / ema50_last * 100 * 10
            raw = max(-100.0, min(100.0, raw))
            # Price position bonus: +20 if price above both EMAs (bullish structure),
            # -20 if below both (bearish). No bonus if price is between the two.
            if price > ema20_last and price > ema50_last:
                raw += 20
            elif price < ema20_last and price < ema50_last:
                raw -= 20
            trend_score = int(max(-100, min(100, raw)))
        except Exception:
            trend_score = 0

        # --- Next funding settlement ---
        # One extra API call per enriched symbol (top 30 only). The endpoint
        # returns nextSettleTime as a Unix millisecond timestamp.
        next_funding_minutes = None
        try:
            fr_data = fetch_mexc(f"/contract/funding_rate/{symbol}")
            if fr_data and isinstance(fr_data, dict):
                next_settle_ms = fr_data.get("nextSettleTime")
                if next_settle_ms:
                    minutes_left = (int(next_settle_ms) / 1000 - time.time()) / 60
                    next_funding_minutes = max(0, int(round(minutes_left)))
        except Exception:
            next_funding_minutes = None

        # --- Orderbook imbalance ---
        imbalance = 0.5  # neutral default if depth fetch fails
        depth_data = fetch_mexc(f"/contract/depth/{symbol}")
        if depth_data and isinstance(depth_data, dict):
            asks = depth_data.get("asks", [])[:10]
            bids = depth_data.get("bids", [])[:10]
            ask_vol = _parse_depth_volume(asks)
            bid_vol = _parse_depth_volume(bids)
            total = bid_vol + ask_vol
            if total > 0:
                imbalance = bid_vol / total

        # --- Conviction adjustments ---
        conviction = base["conviction_base"]

        if direction == "LONG":
            if last_rsi < 40:
                conviction += 10
                tags.append("oversold")
            elif last_rsi > 70:
                conviction -= 10
                tags.append("overbought_risk")
            if imbalance > 0.6:
                conviction += 10
                tags.append("bid_heavy")
        else:  # SHORT
            if last_rsi > 60:
                conviction += 10
                tags.append("overbought")
            elif last_rsi < 30:
                conviction -= 10
                tags.append("oversold_risk")
            if imbalance < 0.4:
                conviction += 10
                tags.append("ask_heavy")

        # Extreme volatility: high ATR% means the signal is real but the trade
        # is more dangerous — discount conviction so it ranks below calmer setups.
        if vol_regime == "extreme":
            conviction = int(conviction * 0.85)
            tags.append("extreme_vol")

        conviction = max(0, min(100, conviction))

        # --- Ladders ---
        entries, exits, stop_loss = generate_ladders(
            current_price=price,
            atr_value=atr_val,
            tiers=3,
            direction=direction,
        )

        return {
            "symbol": symbol,
            "direction": direction,
            "conviction": conviction,
            "price": price,
            "entries": [round(e, 8) for e in entries],
            "exits": [round(e, 8) for e in exits],
            "stop_loss": round(stop_loss, 8),
            # Momentum
            "change_24h_pct": base["change_24h_pct"],
            "change_4h_pct": change_4h_pct,
            "change_1h_pct": change_1h_pct,
            # Funding / open interest
            "funding_rate": base["funding_rate"],
            "open_interest": base["open_interest"],
            "next_funding_minutes": next_funding_minutes,
            # Volume
            "volume_24h": base["volume_24h"],
            # Technical
            "atr_pct": round(atr_pct_val, 4),
            "volatility": vol_regime,
            "rsi_1h": round(last_rsi, 2),
            "trend_score": trend_score,
            "tags": list(dict.fromkeys(tags)),  # deduplicate, preserve order
            "basis_pct": None,   # reserved for P1.5
            "ai_report": None,   # reserved for P2
        }
    except Exception as e:
        print(f"enrich_signal error [{symbol}]: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_scan(threshold: int = CONVICTION_THRESHOLD) -> tuple[list[dict], int]:
    """
    Two-stage scan across all MEXC perpetual tickers.

    Stage 1: Fetch all 800+ tickers, score each with score_ticker(), discard
             anything with conviction_base < 20, take the top ENRICH_TOP_N.
    Stage 2: Enrich the top N concurrently — fetches klines + depth per symbol,
             runs indicators, builds ladders.

    Returns (signals, total_pairs) where total_pairs is the raw ticker count.
    Signals have conviction >= threshold, sorted descending.
    """
    tickers = fetch_mexc("/contract/ticker")
    if not tickers or not isinstance(tickers, list):
        return [], 0

    total_pairs = len(tickers)

    # Stage 1
    base_signals: list[dict] = []
    for t in tickers:
        scored = score_ticker(t)
        if scored and scored["conviction_base"] >= 20:
            base_signals.append(scored)

    base_signals.sort(key=lambda s: s["conviction_base"], reverse=True)
    top = base_signals[:ENRICH_TOP_N]

    # Stage 2 — concurrent enrichment
    signals: list[dict] = []
    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as executor:
        for sig in executor.map(enrich_signal, top):
            if sig and sig["conviction"] >= threshold:
                signals.append(sig)

    signals.sort(key=lambda s: s["conviction"], reverse=True)
    return signals, total_pairs


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan")
def api_scan():
    try:
        threshold = request.args.get("threshold", CONVICTION_THRESHOLD, type=int)
        signals, total_pairs = run_scan(threshold=threshold)
        return jsonify({"success": True, "signals": signals, "count": len(signals), "total_pairs": total_pairs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/signal/<symbol>")
def api_signal(symbol: str):
    """
    Return a fully-enriched signal for a single symbol. Used by the signal
    detail modal to refresh or display a specific ticker on demand.
    Fetches all tickers and finds the matching one — MEXC's public ticker
    endpoint doesn't support single-symbol filtering reliably.
    """
    try:
        tickers = fetch_mexc("/contract/ticker")
        if not tickers:
            return jsonify({"success": False, "error": "MEXC unavailable"}), 502

        ticker = next(
            (t for t in tickers if t.get("symbol") == symbol.upper()), None
        )
        if not ticker:
            return jsonify({"success": False, "error": f"Symbol {symbol!r} not found"}), 404

        base = score_ticker(ticker)
        if not base:
            return jsonify({"success": False, "error": "Unable to score ticker"}), 422

        signal = enrich_signal(base)
        if not signal:
            return jsonify({"success": False, "error": "Insufficient kline data"}), 422

        return jsonify({"success": True, "signal": signal})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def find_free_port(preferred: int, max_tries: int = 5) -> int:
    """
    Return the first available port starting at preferred, trying up to
    max_tries consecutive ports. Binds to 0.0.0.0 to match Flask's binding
    behavior — a port that's free on loopback only could still fail when Flask
    binds to all interfaces. Returns preferred if none are free (Flask will
    then surface its own error).
    """
    for port in range(preferred, preferred + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("0.0.0.0", port))
            return port
        except OSError:
            continue
    return preferred


if __name__ == "__main__":
    try:
        # SOCK_DGRAM connect trick: doesn't send packets, just resolves the
        # outbound interface so we get the real LAN IP rather than 127.0.0.1.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lan_ip = "127.0.0.1"

    actual_port = find_free_port(PORT)
    if actual_port != PORT:
        print(f"Note: port {PORT} was busy, using {actual_port} instead.")

    print("=" * 50)
    print("  Matrix Trader 7.0")
    print(f"  Local  → http://localhost:{actual_port}")
    print(f"  iPhone → http://{lan_ip}:{actual_port}")
    print("=" * 50)

    app.run(host="0.0.0.0", port=actual_port, debug=False)
