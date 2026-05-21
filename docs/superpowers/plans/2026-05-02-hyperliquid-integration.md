# Hyperliquid Exchange Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Hyperliquid as a second exchange source — working scan that produces signals in the same format as MEXC, plus read-only account integration, no proxy or VPN needed.

**Architecture:** A new `lib/hyperliquid_client.py` provides fail-closed API functions and a normalizer that maps HL ticker format to MEXC-compatible field names so `score_ticker()` can consume them unchanged. `enrich_signal()` in `app.py` gains a single exchange-dispatch branch: when `base.get('exchange') == 'HYPERLIQUID'`, it routes kline/depth fetches to the HL client instead of MEXC endpoints. Two new routes (`/api/hl/scan`, `/api/hl/account`) expose HL data. The frontend adds an exchange selector above the strategy bar and routes `scanSignals()` accordingly.

**Tech Stack:** Python 3.11 / Flask, `requests` (already installed), SQLite3 (signals persist with `exchange=HYPERLIQUID`), vanilla JS — no new dependencies.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| **Create** | `lib/hyperliquid_client.py` | HL API calls + ticker normalizer |
| **Modify** | `app.py` | Import HL client, patch `score_ticker` exchange field, patch `enrich_signal` kline routing, add 2 routes, add `HL_WALLET_ADDRESS` env read |
| **Modify** | `templates/index.html` | S.exchange state, exchange selector pills, scan routing, HL badge on cards, HL connection status |
| **Modify** | `.env.example` | Document `HL_WALLET_ADDRESS` |
| **Modify** | `HANDOFF.md` | Session summary, new routes, new env var |

---

## Task 1: Create lib/hyperliquid_client.py

**Files:**
- Create: `lib/hyperliquid_client.py`

- [ ] **Step 1: Write the file**

```python
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
import requests

HL_BASE = "https://api.hyperliquid.xyz"
_TIMEOUT = 10


def _post(payload: dict):
    """POST /info with payload. Returns parsed JSON or None on any error."""
    try:
        resp = requests.post(f"{HL_BASE}/info", json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[hl_client] POST /info error ({payload.get('type')}): {e}", file=sys.stderr)
        return None


def fetch_hl_meta_and_ctxs() -> tuple:
    """
    POST /info {"type": "metaAndAssetCtxs"}
    Returns (universe, asset_ctxs) — two parallel lists.
    universe[i] = {name, szDecimals, maxLeverage, ...}
    asset_ctxs[i] = {markPx, prevDayPx, dayNtlVlm, openInterest, funding, oraclePx, ...}
    Returns ([], []) on any error — never raises.
    """
    try:
        data = _post({"type": "metaAndAssetCtxs"})
        if not data or not isinstance(data, list) or len(data) < 2:
            return [], []
        universe = data[0].get("universe", [])
        asset_ctxs = data[1]
        if not isinstance(universe, list) or not isinstance(asset_ctxs, list):
            return [], []
        return universe, asset_ctxs
    except Exception as e:
        print(f"[hl_client] fetch_hl_meta_and_ctxs error: {e}", file=sys.stderr)
        return [], []


def fetch_hl_klines(coin: str, interval: str = "1h", lookback_hours: int = 120) -> list:
    """
    POST /info {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                "startTime": ms, "endTime": ms}}
    interval options: "1m","3m","5m","15m","30m","1h","2h","4h","8h","12h","1d"
    Returns list of OHLCV dicts. Returns [] on any error — never raises.
    Each dict: {t, o, h, l, c, v} — timestamp ms, open, high, low, close, volume
    """
    try:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - lookback_hours * 3_600_000
        data = _post({
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        })
        if not data or not isinstance(data, list):
            return []
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
    results = []
    for i, meta in enumerate(universe):
        try:
            if i >= len(asset_ctxs):
                break
            ctx = asset_ctxs[i]
            name = meta.get("name", "")
            if not name:
                continue

            mark_px = float(ctx.get("markPx") or 0)
            prev_day_px = float(ctx.get("prevDayPx") or 0)
            day_vol = float(ctx.get("dayNtlVlm") or 0)
            oi_base = float(ctx.get("openInterest") or 0)
            funding = float(ctx.get("funding") or 0)
            oracle_px = float(ctx.get("oraclePx") or mark_px)
            max_lev = int(meta.get("maxLeverage") or 50)

            if mark_px <= 0 or day_vol <= 0:
                continue

            rise_fall = (mark_px - prev_day_px) / prev_day_px if prev_day_px > 0 else 0.0
            oi_usdc = oi_base * mark_px

            results.append({
                "symbol":       f"{name}_USDT",
                "lastPrice":    mark_px,
                "fairPrice":    oracle_px,
                "riseFallRate": rise_fall,       # decimal — score_ticker multiplies by 100
                "fundingRate":  funding,          # already decimal
                "vol24h":       day_vol,          # score_ticker falls back to vol24h
                "holdVol":      oi_usdc,          # open interest in USDC
                "exchange":     "HYPERLIQUID",
                "maxLeverage":  max_lev,
            })
        except Exception as e:
            print(f"[hl_client] normalize error at index {i}: {e}", file=sys.stderr)
            continue

    return results
```

- [ ] **Step 2: Verify compile**

```bash
python3 -m py_compile lib/hyperliquid_client.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 3: Verify live fetch — pair count**

```bash
python3 -c "
from lib.hyperliquid_client import fetch_hl_meta_and_ctxs
u, c = fetch_hl_meta_and_ctxs()
print(len(u), 'pairs')
assert len(u) > 100, f'Expected >100, got {len(u)}'
print('PASS')
"
```
Expected: `NNN pairs` (where NNN > 100) then `PASS`

- [ ] **Step 4: Verify normalize output shape**

```bash
python3 -c "
from lib.hyperliquid_client import fetch_hl_meta_and_ctxs, normalize_hl_tickers
u, c = fetch_hl_meta_and_ctxs()
t = normalize_hl_tickers(u, c)
assert len(t) > 0, 'No tickers'
required = {'symbol','lastPrice','fairPrice','riseFallRate','fundingRate','vol24h','holdVol','exchange','maxLeverage'}
missing = required - set(t[0].keys())
assert not missing, f'Missing fields: {missing}'
assert t[0]['exchange'] == 'HYPERLIQUID'
print(t[0])
print('PASS')
"
```
Expected: dict with all required fields printed, then `PASS`

- [ ] **Step 5: Verify klines**

```bash
python3 -c "
from lib.hyperliquid_client import fetch_hl_klines
k = fetch_hl_klines('BTC', '1h', 120)
assert len(k) > 100, f'Expected >100 candles, got {len(k)}'
assert 't' in k[0] and 'h' in k[0] and 'l' in k[0]
print(len(k), 'candles, first:', k[0])
print('PASS')
"
```
Expected: `NNN candles, first: {...}` then `PASS`

- [ ] **Step 6: Verify account graceful failure**

```bash
python3 -c "
from lib.hyperliquid_client import fetch_hl_account
result = fetch_hl_account('0x0000000000000000000000000000000000000000')
print('result:', result)
print('PASS — returned without raising')
"
```
Expected: prints a dict (possibly empty or with zero-balance data) without error

- [ ] **Step 7: Verify no Flask imports**

```bash
grep -n "flask\|from app" lib/hyperliquid_client.py && echo "FAIL: Flask import found" || echo "PASS: no Flask imports"
```
Expected: `PASS: no Flask imports`

- [ ] **Step 8: Commit**

```bash
git add lib/hyperliquid_client.py
git commit -m "feat: add lib/hyperliquid_client.py — HL API client and ticker normalizer"
```

---

## Task 2: Patch score_ticker() to propagate exchange field

`score_ticker()` hardcodes `"exchange": "MEXC"` at line 1138 of `app.py`. Changing this to read from the ticker allows HL tickers (which have `exchange: "HYPERLIQUID"`) to flow correctly into `enrich_signal()`.

**Files:**
- Modify: `app.py` at line 1138

- [ ] **Step 1: Read the target line**

Open `app.py`, find the `result = {` dict inside `score_ticker()` (around line 1136–1148). Confirm it reads:
```python
        result = {
            "symbol": symbol,
            "exchange": "MEXC",
```

- [ ] **Step 2: Patch the exchange field**

Change line 1138:
```python
            "exchange": "MEXC",
```
to:
```python
            "exchange": ticker.get("exchange", "MEXC"),
```

- [ ] **Step 3: Verify compile**

```bash
python3 -m py_compile app.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 4: Verify MEXC behavior unchanged**

```bash
python3 -c "
import app
t = {'symbol':'BTC_USDT','lastPrice':100,'fairPrice':100,'riseFallRate':0.05,'fundingRate':-0.001,'volume24':50_000_000,'holdVol':100}
result = app.score_ticker(t)
assert result['exchange'] == 'MEXC', f'Expected MEXC, got {result[\"exchange\"]}'
print('PASS: MEXC ticker still gets exchange=MEXC')
"
```
Expected: `PASS: MEXC ticker still gets exchange=MEXC`

- [ ] **Step 5: Verify HL ticker gets HYPERLIQUID**

```bash
python3 -c "
import app
t = {'symbol':'BTC_USDT','lastPrice':100,'fairPrice':100,'riseFallRate':0.05,'fundingRate':-0.001,'vol24h':50_000_000,'holdVol':100,'exchange':'HYPERLIQUID'}
result = app.score_ticker(t)
assert result['exchange'] == 'HYPERLIQUID', f'Expected HYPERLIQUID, got {result[\"exchange\"]}'
print('PASS: HL ticker gets exchange=HYPERLIQUID')
"
```
Expected: `PASS: HL ticker gets exchange=HYPERLIQUID`

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "fix: score_ticker() propagates exchange field from ticker instead of hardcoding MEXC"
```

---

## Task 3: Add HL imports and env var to app.py

**Files:**
- Modify: `app.py` (top of file — imports and env reads)

- [ ] **Step 1: Read the current imports block** (lines 1–35 of app.py)

Confirm current lib imports end with `from lib.coinglass_client import (...)`.

- [ ] **Step 2: Add HL import after coinglass import**

After the `from lib.coinglass_client import (...)` block (around line 35), add:

```python
from lib.hyperliquid_client import (
    fetch_hl_meta_and_ctxs,
    fetch_hl_klines,
    fetch_hl_orderbook,
    fetch_hl_account,
    normalize_hl_tickers,
)
```

- [ ] **Step 3: Add HL_WALLET_ADDRESS env var**

Find where `PORT = int(os.getenv("MATRIX_PORT", "8080"))` is defined (around line 50). Add after it:

```python
HL_WALLET_ADDRESS = os.getenv("HL_WALLET_ADDRESS", "")
```

- [ ] **Step 4: Verify compile**

```bash
python3 -m py_compile app.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 5: Verify import resolves**

```bash
python3 -c "import app; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat: import hyperliquid_client into app.py, add HL_WALLET_ADDRESS env var"
```

---

## Task 4: Patch enrich_signal() for HL kline routing

`enrich_signal()` currently makes 5 MEXC API calls for each signal. When the signal's exchange is `HYPERLIQUID`, these must route to HL equivalents instead. The MEXC path must not change at all.

**Files:**
- Modify: `app.py` (inside `enrich_signal()`, approximately lines 1483–1735)

There are 6 specific patches, each isolated. Apply them in order.

### Patch 4a — 1h kline fetch and DataFrame builder

**Find** this block (around lines 1484–1498):
```python
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
```

**Replace** with:
```python
    try:
        # --- Klines ---
        exchange = base.get("exchange", "MEXC")
        if exchange == "HYPERLIQUID":
            coin = symbol.replace("_USDT", "")
            hl_1h = fetch_hl_klines(coin, "1h", 120)
            if not hl_1h:
                return None
            kline_data = {
                "open":  [float(k["o"]) for k in hl_1h],
                "high":  [float(k["h"]) for k in hl_1h],
                "low":   [float(k["l"]) for k in hl_1h],
                "close": [float(k["c"]) for k in hl_1h],
                "vol":   [float(k["v"]) for k in hl_1h],
            }
        else:
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
```

### Patch 4b — 4h kline depth check

**Find** this block (around lines 1507–1515):
```python
        # --- Kline depth gate ---
        # Fetch 4h candles just to measure history depth; count only, not used for indicators.
        kline4h_data = fetch_mexc(f"/contract/kline/{symbol}", params={"interval": "Hour4", "limit": 50})
        n4h = 0
        if kline4h_data and isinstance(kline4h_data, dict):
            n4h = len(kline4h_data.get("close", []))
```

**Replace** with:
```python
        # --- Kline depth gate ---
        # Fetch 4h candles just to measure history depth; count only, not used for indicators.
        if exchange == "HYPERLIQUID":
            hl_4h = fetch_hl_klines(coin, "4h", 480)
            n4h = len(hl_4h)
        else:
            kline4h_data = fetch_mexc(f"/contract/kline/{symbol}", params={"interval": "Hour4", "limit": 50})
            n4h = 0
            if kline4h_data and isinstance(kline4h_data, dict):
                n4h = len(kline4h_data.get("close", []))
```

### Patch 4c — funding rate fetch

**Find** this block (around lines 1634–1646):
```python
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
```

**Replace** with:
```python
        # --- Next funding settlement ---
        next_funding_minutes = None
        if exchange != "HYPERLIQUID":
            # MEXC endpoint returns nextSettleTime as Unix millisecond timestamp.
            try:
                fr_data = fetch_mexc(f"/contract/funding_rate/{symbol}")
                if fr_data and isinstance(fr_data, dict):
                    next_settle_ms = fr_data.get("nextSettleTime")
                    if next_settle_ms:
                        minutes_left = (int(next_settle_ms) / 1000 - time.time()) / 60
                        next_funding_minutes = max(0, int(round(minutes_left)))
            except Exception:
                next_funding_minutes = None
```

### Patch 4d — orderbook depth fetch

**Find** this block (around lines 1648–1658):
```python
        # --- Orderbook imbalance ---
        imbalance = 0.5  # neutral default if depth fetch fails
        depth_data = fetch_mexc(f"/contract/depth/{symbol}")
        if depth_data and isinstance(depth_data, dict):
            asks = depth_data.get("asks", [])[:10]
            bids = depth_data.get("bids", [])[:10]
```

**Replace** with:
```python
        # --- Orderbook imbalance ---
        imbalance = 0.5  # neutral default if depth fetch fails
        if exchange == "HYPERLIQUID":
            depth_data = fetch_hl_orderbook(coin)
        else:
            depth_data = fetch_mexc(f"/contract/depth/{symbol}")
        if depth_data and isinstance(depth_data, dict):
            asks = depth_data.get("asks", [])[:10]
            bids = depth_data.get("bids", [])[:10]
```

### Patch 4e — daily trend klines

**Find** this block (around lines 1724–1735):
```python
        try:
            daily_klines = fetch_mexc(
                f"/contract/kline/{symbol}",
                params={"interval": "Day1", "limit": 30},
            )
            if daily_klines:
                dt = daily_trend_direction(daily_klines)
                daily_trend = dt
                if dt != "NEUTRAL":
                    daily_trend_aligned = (direction == dt)
        except Exception:
            pass
```

**Replace** with:
```python
        try:
            if exchange == "HYPERLIQUID":
                hl_daily = fetch_hl_klines(coin, "1d", 720)
                # Convert to list-of-entry format that daily_trend_direction accepts:
                # [timestamp, open, close, high, low, vol] (index 3=high, 4=low)
                daily_klines = [[k["t"], k["o"], k["c"], k["h"], k["l"], k["v"]] for k in hl_daily] if hl_daily else None
            else:
                daily_klines = fetch_mexc(
                    f"/contract/kline/{symbol}",
                    params={"interval": "Day1", "limit": 30},
                )
            if daily_klines:
                dt = daily_trend_direction(daily_klines)
                daily_trend = dt
                if dt != "NEUTRAL":
                    daily_trend_aligned = (direction == dt)
        except Exception:
            pass
```

### Patch 4f — hardcoded "MEXC" in sig dict (lines 1583 and 1742)

There are two places where the candidate/signal dict hardcodes `"exchange": "MEXC"`.

**Find line ~1583** (inside the vol-gate shadow/block block, the `candidate = {` dict):
```python
                "exchange": "MEXC",
```
**Replace** with:
```python
                "exchange": exchange,
```

**Find line ~1742** (inside the final `sig = {` dict):
```python
            "exchange": "MEXC",
```
**Replace** with:
```python
            "exchange": exchange,
```

- [ ] **Step 1: Apply Patch 4a** (1h kline routing) as described above.

- [ ] **Step 2: Apply Patch 4b** (4h kline depth check) as described above.

- [ ] **Step 3: Apply Patch 4c** (funding rate fetch guard) as described above.

- [ ] **Step 4: Apply Patch 4d** (orderbook depth routing) as described above.

- [ ] **Step 5: Apply Patch 4e** (daily kline routing) as described above.

- [ ] **Step 6: Apply Patch 4f** (both hardcoded "MEXC" in dicts) as described above.

- [ ] **Step 7: Verify compile**

```bash
python3 -m py_compile app.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 8: Verify MEXC scan still works (smoke test)**

```bash
python3 -c "import app; print('import OK')"
```
Expected: `import OK`

- [ ] **Step 9: Commit**

```bash
git add app.py
git commit -m "feat: enrich_signal() routes klines/depth/daily to Hyperliquid client when exchange=HYPERLIQUID"
```

---

## Task 5: Add /api/hl/scan and /api/hl/account routes

**Files:**
- Modify: `app.py` — add two routes after the existing `/api/scan/all` route (~line 2052)

- [ ] **Step 1: Read the area** around lines 2050–2060 in `app.py` to confirm the end of `api_scan_all` and start of `api_market`.

- [ ] **Step 2: Add both routes** between `api_scan_all` and `api_market`:

```python
@app.route("/api/hl/scan")
def api_hl_scan():
    """
    Scans Hyperliquid perp markets using the same scoring engine as MEXC.
    Query params: strategy (default: balanced), threshold (default: CONVICTION_THRESHOLD)
    Returns same shape as /api/scan — {success, signals, total_pairs, scan_time, exchange}
    """
    try:
        t0 = time.time()
        strategy_key = request.args.get("strategy", "balanced")
        threshold = request.args.get("threshold", CONVICTION_THRESHOLD, type=int)

        registry = get_strategy_registry()
        strat = registry.get(strategy_key, registry["balanced"])
        strategy_key = strat["key"]
        effective_threshold = max(threshold, strat["min_conviction"])

        universe, asset_ctxs = fetch_hl_meta_and_ctxs()
        if not universe:
            return jsonify({"success": False, "error": "Hyperliquid ticker feed unavailable"}), 502

        tickers = normalize_hl_tickers(universe, asset_ctxs)
        total_pairs = len(tickers)

        coinglass_snapshot = get_coin_market_snapshot()
        sym_perf_cache = _load_symbol_performance_cache()
        sym_overrides = _get_symbol_overrides()

        # Stage 1 — score all HL tickers
        base_signals: list[dict] = []
        for t in tickers:
            scored = score_ticker(t, strategy=strat, coinglass_snapshot=coinglass_snapshot,
                                  sym_perf_cache=sym_perf_cache, sym_overrides=sym_overrides)
            if scored and scored["conviction_base"] >= 20:
                base_signals.append(scored)

        base_signals.sort(key=lambda s: s["conviction_base"], reverse=True)
        top = base_signals[:ENRICH_TOP_N]

        # Stage 2 — concurrent enrichment
        filter_stats = {
            "lock": threading.Lock(),
            "long_vol_refuse": 0,
            "long_vol_shadow": 0,
            "short_vol_refuse": 0,
            "short_vol_shadow": 0,
        }
        enrich = partial(enrich_signal, strategy=strat, filter_stats=filter_stats)
        signals: list[dict] = []
        with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as executor:
            for sig in executor.map(enrich, top):
                if sig and sig["conviction"] >= effective_threshold:
                    signals.append(sig)

        signals.sort(key=lambda s: s["conviction"], reverse=True)
        log_signals(signals)

        scan_time = round(time.time() - t0, 2)
        return jsonify({
            "success":     True,
            "signals":     signals,
            "count":       len(signals),
            "total_pairs": total_pairs,
            "strategy":    strategy_key,
            "scan_time":   scan_time,
            "exchange":    "HYPERLIQUID",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/hl/account")
def api_hl_account():
    """
    Returns Hyperliquid account state for the configured wallet address.
    No signing required — reads by wallet address only.
    Returns {connected: false, reason: "..."} when HL_WALLET_ADDRESS not set.
    """
    if not HL_WALLET_ADDRESS:
        return jsonify({"success": True, "connected": False,
                        "reason": "HL_WALLET_ADDRESS not configured"})
    data = fetch_hl_account(HL_WALLET_ADDRESS)
    if not data:
        return jsonify({"success": True, "connected": False,
                        "reason": "No data returned from Hyperliquid"})
    return jsonify({"success": True, "connected": True, "data": data})
```

- [ ] **Step 3: Verify compile**

```bash
python3 -m py_compile app.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 4: Start the app and test /api/hl/account (no wallet configured)**

```bash
# In one terminal:
python3 app.py &
sleep 3
curl -s http://localhost:8080/api/hl/account | python3 -m json.tool
```
Expected: `{"connected": false, "reason": "HL_WALLET_ADDRESS not configured", "success": true}`

Kill the background server after testing: `pkill -f "python3 app.py"`

- [ ] **Step 5: Test /api/hl/scan returns 200 with HYPERLIQUID exchange**

```bash
python3 app.py &
sleep 3
curl -s "http://localhost:8080/api/hl/scan?strategy=balanced" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['success'], d.get('error')
assert d['exchange'] == 'HYPERLIQUID'
assert isinstance(d['signals'], list)
print(f'PASS: {len(d[\"signals\"])} signals from {d[\"total_pairs\"]} pairs in {d[\"scan_time\"]}s')
"
pkill -f "python3 app.py"
```
Expected: `PASS: N signals from NNN pairs in N.Ns`

- [ ] **Step 6: Verify MEXC scan unaffected**

```bash
python3 app.py &
sleep 3
curl -s -X POST http://localhost:8080/api/scan/all | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['success'], d.get('error')
sigs = d['results'].get('balanced', {}).get('signals', [])
mexc_sigs = [s for s in sigs if s.get('exchange') == 'MEXC']
print(f'PASS: {len(mexc_sigs)} MEXC signals unchanged')
"
pkill -f "python3 app.py"
```
Expected: `PASS: N MEXC signals unchanged`

- [ ] **Step 7: Verify HL signals appear in history**

```bash
python3 -c "
import sqlite3, os
db = 'data/signals.db'
if os.path.exists(db):
    conn = sqlite3.connect(db)
    rows = conn.execute('SELECT COUNT(*) FROM signals WHERE exchange=?', ('HYPERLIQUID',)).fetchone()
    print(f'PASS: {rows[0]} HYPERLIQUID signals in DB')
    conn.close()
else:
    print('DB not found — run a scan first')
"
```
Expected: `PASS: N HYPERLIQUID signals in DB` (N > 0 after a successful scan)

- [ ] **Step 8: Commit**

```bash
git add app.py
git commit -m "feat: add /api/hl/scan and /api/hl/account routes"
```

---

## Task 6: Update .env.example

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Read .env.example** to find the current content and the right insertion point.

- [ ] **Step 2: Add HL_WALLET_ADDRESS** after the MEXC keys section:

```bash
HL_WALLET_ADDRESS=    # your Hyperliquid wallet address (0x...) for account integration
```

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: add HL_WALLET_ADDRESS to .env.example"
```

---

## Task 7: Frontend — S.exchange state + exchange selector pills

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add `exchange` field to S state object** (around line 1474–1488)

**Find:**
```javascript
const S = {
  phase:    'idle',
  signals:  [],
  filtered: [],
  selected: -1,
  dir:      'all',
  sort:     'conviction',
  strategy: 'balanced',
  totalPairs: 0,
  scanTime:   null,
  timerId:    null,
  countdownId: null,
  volFilter:  'any',    // any | low | medium | high_extreme
  minVolume:  0,        // minimum 24h volume in USD (0 = no filter)
};
```

**Replace** with:
```javascript
const S = {
  phase:    'idle',
  signals:  [],
  filtered: [],
  selected: -1,
  dir:      'all',
  sort:     'conviction',
  strategy: 'balanced',
  exchange: 'mexc',     // 'mexc' | 'hyperliquid'
  totalPairs: 0,
  scanTime:   null,
  timerId:    null,
  countdownId: null,
  volFilter:  'any',    // any | low | medium | high_extreme
  minVolume:  0,        // minimum 24h volume in USD (0 = no filter)
};
```

- [ ] **Step 2: Add CSS for exchange pills** — find the `#strategy-bar` CSS block (around line 106) and add the exchange bar styles after it:

**Find:**
```css
    #strategy-bar {
```
There will be several lines for the strategy bar. Add the following CSS somewhere near the strategy-bar block (after it ends):

```css
    #exchange-bar {
      display: flex;
      gap: 6px;
      padding: 8px 16px 0;
      flex-wrap: wrap;
    }
    .exch-btn {
      padding: 4px 12px;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--text2);
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      cursor: pointer;
      border-radius: 3px;
    }
    .exch-btn.active {
      background: var(--blue);
      border-color: var(--blue);
      color: #fff;
    }
    .exch-btn:hover:not(.active) { border-color: var(--text2); color: var(--text1); }
    .hl-badge {
      display: inline-block;
      font-size: 9px;
      font-weight: 700;
      padding: 1px 4px;
      border-radius: 2px;
      background: var(--blue);
      color: #fff;
      vertical-align: middle;
      margin-left: 4px;
      letter-spacing: 0.3px;
    }
```

- [ ] **Step 3: Add exchange selector pills to HTML** — in the signals section, add the exchange bar above the strategy bar:

**Find:**
```html
      <!-- Strategy selector — always visible, governs next scan; populated by renderStrategyButtons() -->
      <div id="strategy-bar">
```

**Replace** with:
```html
      <!-- Exchange selector -->
      <div id="exchange-bar">
        <button class="exch-btn active" data-exch="mexc" onclick="setExchange('mexc')">MEXC</button>
        <button class="exch-btn" data-exch="hyperliquid" onclick="setExchange('hyperliquid')">Hyperliquid</button>
      </div>

      <!-- Strategy selector — always visible, governs next scan; populated by renderStrategyButtons() -->
      <div id="strategy-bar">
```

- [ ] **Step 4: Add `setExchange()` function** — add it near `setStrategy()` (around line 2377):

**Find** the `function setStrategy(key` function and add `setExchange` before or after it:

```javascript
function setExchange(key) {
  S.exchange = key;
  S.signals  = [];
  S.filtered = [];
  H.scanResults = {};
  localStorage.setItem('mt7_exchange', key);

  document.querySelectorAll('.exch-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.exch === key);
  });

  if (S.phase !== 'idle') {
    setPhase('idle');
  }
}
```

- [ ] **Step 5: Restore exchange from localStorage on init** — find where filters are restored from localStorage (look for `mt7_strategy` or `localStorage.getItem` in the JS). Add exchange restore nearby:

**Find** the first `localStorage.getItem('mt7_strategy')` call and add after it (or just before where `loadStrategies()` is called on page load):

```javascript
(function restoreExchange() {
  const saved = localStorage.getItem('mt7_exchange');
  if (saved === 'hyperliquid') setExchange('hyperliquid');
})();
```

- [ ] **Step 6: Verify compile**

```bash
python3 -m py_compile app.py && echo "app OK"
```
Expected: `app OK`

- [ ] **Step 7: Commit**

```bash
git add templates/index.html
git commit -m "feat: add S.exchange state and exchange selector pills above strategy bar"
```

---

## Task 8: Frontend — update scanSignals() for exchange routing

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Replace scanSignals()** (currently at line 2179–2220)

**Find** the entire `async function scanSignals()` body. **Replace** with:

```javascript
async function scanSignals() {
  setPhase('scanning');
  startProgress();
  if (S.timerId)     clearInterval(S.timerId);
  if (S.countdownId) clearInterval(S.countdownId);

  try {
    let data;

    if (S.exchange === 'hyperliquid') {
      const resp = await fetch(`/api/hl/scan?strategy=${encodeURIComponent(S.strategy)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
      if (!data.success) throw new Error(data.error || 'Unknown error');

      stopProgress();
      H.scanResults  = { [S.strategy]: { signals: data.signals, total_pairs: data.total_pairs } };
      H.lastScanTime = new Date().toISOString();
      S.totalPairs   = data.total_pairs || 0;
      S.scanTime     = new Date();
      S.signals      = data.signals || [];

    } else {
      const resp = await fetch('/api/scan/all', { method: 'POST' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = await resp.json();
      if (!data.success) throw new Error(data.error || 'Unknown error');

      stopProgress();
      H.scanResults  = data.results || {};
      H.lastScanTime = new Date().toISOString();
      S.totalPairs   = data.total_pairs || 800;
      S.scanTime     = new Date();

      const res = H.scanResults[S.strategy];
      S.signals = (res && res.signals) ? res.signals : [];
    }

    if (!S.signals.length) {
      $('empty-msg').textContent =
        `${S.totalPairs} pairs scanned. No signals met the conviction threshold for ${S.strategy}.`;
      updateStatBar();
      setPhase('empty');
      return;
    }

    setPhase('results');
    updateStatBar();
    filterAndSort(true);
    startTimestamp();

  } catch (e) {
    console.error('Scan error:', e);
    stopProgress();
    const msg = e.message || 'Unknown error';
    $('error-msg').textContent = `Scan failed: ${msg}`;
    setPhase('error');
  }
}
```

- [ ] **Step 2: Verify app still compiles**

```bash
python3 -m py_compile app.py && echo "OK"
```

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: scanSignals() routes to /api/hl/scan for hyperliquid exchange, /api/scan/all for mexc"
```

---

## Task 9: Frontend — HL badge on signal cards + HL connection status

**Files:**
- Modify: `templates/index.html`

### Part A: HL badge on signal cards

- [ ] **Step 1: Add HL badge to rowHTML()** — find the symbol line inside `rowHTML()` (around line 1862):

**Find:**
```javascript
      <span class="row-symbol">${esc(base)}<span style="color:var(--text3);font-size:10px">${esc(suffix)}</span></span>
```

**Replace** with:
```javascript
      <span class="row-symbol">${esc(base)}<span style="color:var(--text3);font-size:10px">${esc(suffix)}</span>${sig.exchange === 'HYPERLIQUID' ? '<span class="hl-badge">HL</span>' : ''}</span>
```

### Part B: HL connection in Bot Readiness panel

- [ ] **Step 2: Add HL connection status line to renderReadiness()** — find the end of the `renderReadiness()` function (the `el.innerHTML = ...` block, around line 3570–3574):

**Find:**
```javascript
  el.innerHTML = `<div class="br-panel">
    <div class="br-title">Bot Readiness</div>
    ${rows}
    <div class="br-footer">Execution mode: DISABLED &nbsp;·&nbsp; P11 requires review of readiness data before any live orders</div>
  </div>`;
```

**Replace** with:
```javascript
  el.innerHTML = `<div class="br-panel">
    <div class="br-title">Bot Readiness</div>
    ${rows}
    <div class="br-footer">Execution mode: DISABLED &nbsp;·&nbsp; P11 requires review of readiness data before any live orders</div>
  </div>
  <div id="hl-connection-status" style="margin-top:8px;font-size:11px;color:var(--text3)">Hyperliquid: checking...</div>`;
  fetchHlConnectionStatus();
```

- [ ] **Step 3: Add fetchHlConnectionStatus() function** — add after or near `renderReadiness()`:

```javascript
async function fetchHlConnectionStatus() {
  const el = $('hl-connection-status');
  if (!el) return;
  try {
    const resp = await fetch('/api/hl/account');
    if (!resp.ok) { el.textContent = 'Hyperliquid: error'; return; }
    const d = await resp.json();
    if (d.connected) {
      el.textContent = 'Hyperliquid: connected';
      el.style.color = 'var(--green)';
    } else {
      el.textContent = `Hyperliquid: not connected — ${d.reason || 'unknown'}`;
      el.style.color = 'var(--text3)';
    }
  } catch(e) {
    el.textContent = 'Hyperliquid: connection check failed';
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat: HL badge on signal cards, Hyperliquid connection status in Bot Readiness panel"
```

---

## Task 10: Update HANDOFF.md

**Files:**
- Modify: `HANDOFF.md`

- [ ] **Step 1: Add to Environment Variables section** — add `HL_WALLET_ADDRESS` entry:

```
HL_WALLET_ADDRESS  — optional — Hyperliquid wallet address (0x...) for read-only account status
```

- [ ] **Step 2: Add to Flask Routes table** — add two rows:

```
| `/api/hl/scan` | GET | Scans Hyperliquid perp markets; same scoring engine as MEXC; query: strategy, threshold; returns signals with exchange=HYPERLIQUID |
| `/api/hl/account` | GET | Returns Hyperliquid account state for HL_WALLET_ADDRESS; no signing needed; returns connected: false when unconfigured |
```

- [ ] **Step 3: Add to File Structure section** — add `lib/hyperliquid_client.py`:

```
├── lib/hyperliquid_client.py  ← Hyperliquid API client: fetch_hl_meta_and_ctxs, fetch_hl_klines, fetch_hl_orderbook, fetch_hl_account, normalize_hl_tickers
```

- [ ] **Step 4: Add to "What NOT To Do" section**:

```
- Do not call MEXC kline endpoints for Hyperliquid signals — check exchange field
  in enrich_signal() and route to fetch_hl_klines() for HYPERLIQUID signals.
- Do not mix HL_WALLET_ADDRESS with MEXC_API_KEY — these are separate auth systems
  for separate exchanges.
```

- [ ] **Step 5: Update app.py and index.html line counts** — run:

```bash
wc -l app.py templates/index.html
```
Update HANDOFF.md header with new counts.

- [ ] **Step 6: Add session summary** for May 2, 2026 session covering this feature.

- [ ] **Step 7: Commit**

```bash
git add HANDOFF.md
git commit -m "docs: update HANDOFF.md — Hyperliquid integration session May 2"
```

---

## Final Verification Checklist

Run these after all tasks are complete:

```bash
# 1. Both files compile
python3 -m py_compile app.py lib/hyperliquid_client.py && echo "compile OK"

# 2. Import clean
python3 -c "import app; print('import OK')"

# 3. No Flask in HL client
grep -n "flask\|from app" lib/hyperliquid_client.py && echo "FAIL" || echo "no Flask imports OK"

# 4. HL fetch works
python3 -c "
from lib.hyperliquid_client import fetch_hl_meta_and_ctxs, normalize_hl_tickers
u,c = fetch_hl_meta_and_ctxs()
t = normalize_hl_tickers(u,c)
print(len(t), 'pairs normalized')
assert len(t) > 100
print('HL fetch OK')
"

# 5. S.exchange in index.html
grep -c "S\.exchange" templates/index.html && echo "S.exchange references found"

# 6. exchange-bar div exists
grep -c "exchange-bar" templates/index.html && echo "exchange-bar found"

# 7. HL badge in rowHTML
grep "hl-badge" templates/index.html && echo "hl-badge found"
```

All 7 checks must pass before the session is considered complete.
