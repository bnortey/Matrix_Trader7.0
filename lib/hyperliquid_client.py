"""
Hyperliquid public API client.
All functions are pure — no Flask, no app.py imports.
All functions return empty on any error and log to stderr with [hl_client] prefix.
Base URL: https://api.hyperliquid.xyz
All requests: POST /info with JSON body
No auth needed for market data or account reads.
"""
import sys
import time
import threading
import requests

HL_BASE = "https://api.hyperliquid.xyz"
_TIMEOUT = 10
_VALID_INTERVALS = {"1m","3m","5m","15m","30m","1h","2h","4h","8h","12h","1d"}

# Module-level caches — shared across threads, protected by _cache_lock
_cache_lock = threading.Lock()

_META_TTL   = 30          # seconds — ticker prices update every few seconds; 30s is fresh enough
_KLINE_TTL  = 300         # seconds — 5-minute kline cache; safe for Stage 2 enrichment

_meta_cache: dict  = {}   # {"ts": float, "data": (universe, asset_ctxs)}
_kline_cache: dict = {}   # {(coin, interval): {"ts": float, "data": list}}

# 429 retry config
_MAX_RETRIES   = 3
_RETRY_DELAYS  = [0.5, 1.5, 3.0]  # seconds between attempts


def _post(payload: dict):
    """POST /info with payload. Retries up to _MAX_RETRIES times on 429. Returns parsed JSON or None."""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.post(f"{HL_BASE}/info", json=payload, timeout=_TIMEOUT)
            if resp.status_code == 429:
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[attempt]
                    print(f"[hl_client] 429 rate-limit ({payload.get('type')}), retry {attempt+1} in {delay}s", file=sys.stderr)
                    time.sleep(delay)
                    continue
                print(f"[hl_client] 429 rate-limit ({payload.get('type')}), all retries exhausted", file=sys.stderr)
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError:
            return None
        except Exception as e:
            print(f"[hl_client] POST /info error ({payload.get('type')}): {e}", file=sys.stderr)
            return None
    return None


def fetch_hl_meta_and_ctxs() -> tuple:
    """
    POST /info {"type": "metaAndAssetCtxs"}
    Returns (universe, asset_ctxs) — two parallel lists. Cached for _META_TTL seconds.
    universe[i] = {name, szDecimals, maxLeverage, ...}
    asset_ctxs[i] = {markPx, prevDayPx, dayNtlVlm, openInterest, funding, oraclePx, ...}
    Returns ([], []) on any error — never raises.
    """
    now = time.time()
    with _cache_lock:
        cached = _meta_cache.get("data")
        if cached and (now - _meta_cache.get("ts", 0)) < _META_TTL:
            return cached
    try:
        data = _post({"type": "metaAndAssetCtxs"})
        if not data or not isinstance(data, list) or len(data) < 2:
            return [], []
        universe   = data[0].get("universe", [])
        asset_ctxs = data[1]
        if not isinstance(universe, list) or not isinstance(asset_ctxs, list):
            return [], []
        result = (universe, asset_ctxs)
        with _cache_lock:
            _meta_cache["ts"]   = time.time()
            _meta_cache["data"] = result
        return result
    except Exception as e:
        print(f"[hl_client] fetch_hl_meta_and_ctxs error: {e}", file=sys.stderr)
        return [], []


def fetch_hl_klines(coin: str, interval: str = "1h", lookback_hours: int = 120) -> list:
    """
    POST /info {"type": "candleSnapshot", ...}
    Cached per (coin, interval) for _KLINE_TTL seconds — lookback_hours is ignored on cache hit.
    Returns list of OHLCV dicts {t, o, h, l, c, v}. Returns [] on any error — never raises.
    """
    if interval not in _VALID_INTERVALS:
        print(f"[hl_client] fetch_hl_klines: invalid interval '{interval}'", file=sys.stderr)
        return []

    cache_key = (coin, interval)
    now = time.time()
    with _cache_lock:
        entry = _kline_cache.get(cache_key)
        if entry and (now - entry["ts"]) < _KLINE_TTL:
            return entry["data"]
    try:
        end_ms   = int(time.time() * 1000)
        start_ms = end_ms - lookback_hours * 3_600_000
        data = _post({
            "type": "candleSnapshot",
            "req": {
                "coin":      coin,
                "interval":  interval,
                "startTime": start_ms,
                "endTime":   end_ms,
            },
        })
        if not data or not isinstance(data, list):
            return []
        with _cache_lock:
            _kline_cache[cache_key] = {"ts": time.time(), "data": data}
        return data
    except Exception as e:
        print(f"[hl_client] fetch_hl_klines({coin},{interval}) error: {e}", file=sys.stderr)
        return []


def fetch_hl_orderbook(coin: str) -> dict:
    """
    POST /info {"type": "l2Book", "coin": coin}
    Returns {bids: [[px, sz], ...], asks: [[px, sz], ...]}
    Returns {} on any error — never raises.
    """
    try:
        data = _post({"type": "l2Book", "coin": coin})
        if not data or not isinstance(data, dict):
            return {}
        levels = data.get("levels", [])
        if not isinstance(levels, list) or len(levels) < 2:
            return {}
        bids = [[float(e["px"]), float(e["sz"])] for e in levels[0] if "px" in e and "sz" in e]
        asks = [[float(e["px"]), float(e["sz"])] for e in levels[1] if "px" in e and "sz" in e]
        return {"bids": bids, "asks": asks}
    except Exception as e:
        print(f"[hl_client] fetch_hl_orderbook({coin}) error: {e}", file=sys.stderr)
        return {}


def fetch_hl_account(wallet_address: str) -> dict:
    """
    POST /info {"type": "clearinghouseState", "user": wallet_address}
    Returns account summary dict or {} on any error — never raises.
    wallet_address: 42-char hex Ethereum address e.g. "0x..."
    No signing required — public read by wallet address.
    """
    try:
        if not wallet_address or not wallet_address.startswith("0x") or len(wallet_address) != 42:
            print(f"[hl_client] fetch_hl_account: invalid wallet address", file=sys.stderr)
            return {}
        data = _post({"type": "clearinghouseState", "user": wallet_address})
        if not data or not isinstance(data, dict):
            return {}
        return data
    except Exception as e:
        print(f"[hl_client] fetch_hl_account error: {e}", file=sys.stderr)
        return {}


def normalize_hl_tickers(universe: list, asset_ctxs: list) -> list:
    """
    Converts Hyperliquid metaAndAssetCtxs response into MT7 ticker format.
    Produces MEXC-compatible field names so score_ticker() consumes them unchanged.
    Filters out pairs with zero volume, zero price, or missing data.
    """
    try:
        results = []
        for i, meta in enumerate(universe):
            try:
                if i >= len(asset_ctxs):
                    break
                ctx = asset_ctxs[i]
                name = meta.get("name", "")
                if not name:
                    continue

                mark_px    = float(ctx.get("markPx")     or 0)
                prev_day_px = float(ctx.get("prevDayPx") or 0)
                day_vol    = float(ctx.get("dayNtlVlm")  or 0)
                oi_base    = float(ctx.get("openInterest") or 0)
                funding    = float(ctx.get("funding")    or 0)
                oracle_px  = float(ctx.get("oraclePx")  or mark_px)
                max_lev    = int(meta.get("maxLeverage") or 50)

                if mark_px <= 0 or day_vol <= 0:
                    continue

                rise_fall = (mark_px - prev_day_px) / prev_day_px if prev_day_px > 0 else 0.0
                oi_usdc   = oi_base * mark_px

                results.append({
                    "symbol":      f"{name}_USDC",
                    "lastPrice":   mark_px,
                    "fairPrice":   oracle_px,
                    "riseFallRate": rise_fall,   # decimal — score_ticker multiplies by 100
                    "fundingRate": funding,       # already decimal
                    "vol24h":      day_vol,       # score_ticker falls back to vol24h
                    "holdVol":     oi_usdc,       # open interest in USDC
                    "exchange":    "HYPERLIQUID",
                    "maxLeverage": max_lev,
                })
            except Exception as e:
                print(f"[hl_client] normalize error at index {i}: {e}", file=sys.stderr)
                continue
        return results
    except Exception as e:
        print(f"[hl_client] normalize_hl_tickers error: {e}", file=sys.stderr)
        return []
