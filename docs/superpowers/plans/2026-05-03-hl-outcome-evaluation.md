# HL Outcome Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the outcome evaluator exchange-aware so Hyperliquid signals are auto-evaluated using HL klines instead of staying open indefinitely.

**Architecture:** A single new helper `_fetch_klines_for_signal(sig, interval, limit, start_ts)` centralises all exchange routing. It returns a DataFrame with columns `[timestamp, open, high, low, close, volume]`. Both `evaluate_outcome()` and `compute_trade_journey()` replace their direct `fetch_mexc()` kline calls with this helper; the evaluation logic (candle scanning loops) is not touched. `api_outcomes_check()` already uses `SELECT *` so `exchange` is already in every sig dict.

**Tech Stack:** Python 3.11, pandas (already a dep), `fetch_hl_klines` from `lib/hyperliquid_client.py` (already imported).

---

## File Map

| Action | Path | Change |
|---|---|---|
| **Modify** | `app.py` | Add `_fetch_klines_for_signal()` before `evaluate_outcome()`; replace kline fetch in `evaluate_outcome()` (~line 2808); replace kline fetch in `compute_trade_journey()` (~line 2512); confirm `exchange` column in `api_outcomes_check()` SELECT |
| **Modify** | `HANDOFF.md` | Mark HL outcome evaluation limitation resolved; add two "What NOT To Do" rules; update line count |

---

## Task 1: Add _fetch_klines_for_signal() helper

This is the only new function. It must be placed **immediately before** `evaluate_outcome()` (currently at line 2768).

**Files:**
- Modify: `app.py` (~line 2766)

- [ ] **Step 1: Read the insertion point**

Run:
```bash
grep -n "^def evaluate_outcome\|^def expire_stale" /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/app.py
```
Expected output (approximate — confirm actual line numbers):
```
2735: def expire_stale_signals() ...
2768: def evaluate_outcome(...) ...
```
The new function goes between these two — immediately before `def evaluate_outcome`.

- [ ] **Step 2: Insert the helper**

Find the blank line immediately before `def evaluate_outcome(` and insert the following function before it:

```python
def _fetch_klines_for_signal(
    sig: dict,
    interval: str = "Min15",
    limit: int = 300,
    start_ts: int | None = None,
) -> "pd.DataFrame":
    """
    Fetch klines for a signal using the correct exchange client.
    Routes to Hyperliquid or MEXC based on sig.get('exchange').
    Returns a DataFrame with columns: [timestamp, open, high, low, close, volume].
    Returns empty DataFrame on any error — never raises.
    Callers must check df.empty before using.
    """
    exchange = (sig.get("exchange") or "MEXC").upper()
    symbol   = sig.get("symbol", "")

    if exchange == "HYPERLIQUID":
        coin = symbol.replace("_USDT", "").replace("_USDC", "")
        interval_map = {
            "Min1":  "1m",  "Min5":  "5m",  "Min15": "15m",
            "Min30": "30m", "Min60": "1h",  "Hour4": "4h",
            "Hour8": "8h",  "Day1":  "1d",
        }
        hl_interval = interval_map.get(interval, "15m")
        hours_per_candle = {
            "1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
            "1h": 1, "4h": 4, "8h": 8, "1d": 24,
        }
        lookback = int(limit * hours_per_candle.get(hl_interval, 0.25) * 1.2)
        try:
            raw = fetch_hl_klines(coin, hl_interval, lookback_hours=max(lookback, 24))
            if not raw:
                return pd.DataFrame()
            df = pd.DataFrame(raw)
            df = df.rename(columns={
                "t": "timestamp", "o": "open", "h": "high",
                "l": "low",       "c": "close", "v": "volume",
            })
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.sort_values("timestamp").tail(limit).reset_index(drop=True)
            print(f"[hl_klines] {symbol} fetched {len(df)} {hl_interval} candles", file=sys.stderr)
            return df
        except Exception as e:
            print(f"[hl_klines] {symbol} error: {e}", file=sys.stderr)
            return pd.DataFrame()

    else:
        # MEXC path — mirrors existing fetch_mexc kline call
        try:
            params: dict = {"interval": interval, "limit": limit}
            if start_ts is not None:
                params["start"] = start_ts
            raw = fetch_mexc(f"/contract/kline/{symbol}", params=params)
            if not raw or not isinstance(raw, dict):
                return pd.DataFrame()
            df = pd.DataFrame({
                "timestamp": raw.get("time",  []),
                "open":      raw.get("open",  []),
                "high":      raw.get("high",  []),
                "low":       raw.get("low",   []),
                "close":     raw.get("close", []),
                "volume":    raw.get("vol",   []),
            })
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.sort_values("timestamp").reset_index(drop=True)
            print(f"[mexc_klines] {symbol} fetched {len(df)} {interval} candles", file=sys.stderr)
            return df
        except Exception as e:
            print(f"[mexc_klines] {symbol} error: {e}", file=sys.stderr)
            return pd.DataFrame()
```

- [ ] **Step 3: Verify compile**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 -m py_compile app.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 4: Verify import**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 -c "import app; print(hasattr(app, '_fetch_klines_for_signal')); print('OK')"
```
Expected: `True` then `OK`

- [ ] **Step 5: Verify MEXC path returns DataFrame**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 -c "
import app, pandas as pd
sig = {'symbol': 'BTC_USDT', 'exchange': 'MEXC'}
df = app._fetch_klines_for_signal(sig, interval='Min15', limit=10)
print('type:', type(df).__name__)
print('empty:', df.empty)
if not df.empty:
    print('cols:', list(df.columns))
    assert 'timestamp' in df.columns
    assert 'high' in df.columns
    assert 'low' in df.columns
    assert 'close' in df.columns
    print('PASS: MEXC DataFrame shape', df.shape)
"
```
Expected: `PASS: MEXC DataFrame shape (N, 6)` where N > 0

- [ ] **Step 6: Verify HL path returns DataFrame**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 -c "
import app, pandas as pd
sig = {'symbol': 'BTC_USDC', 'exchange': 'HYPERLIQUID'}
df = app._fetch_klines_for_signal(sig, interval='Min15', limit=20)
print('type:', type(df).__name__)
if not df.empty:
    print('cols:', list(df.columns))
    assert 'timestamp' in df.columns
    assert 'high' in df.columns
    assert 'low' in df.columns
    assert 'close' in df.columns
    assert len(df) <= 20
    print('PASS: HL DataFrame shape', df.shape)
else:
    print('empty (API may be unreachable) — check stderr for [hl_klines]')
"
```
Expected: `PASS: HL DataFrame shape (N, 6)` or `empty (API may be unreachable)` with `[hl_klines]` in stderr

- [ ] **Step 7: Verify NULL exchange defaults to MEXC**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 -c "
import app
sig = {'symbol': 'ETH_USDT', 'exchange': None}
df = app._fetch_klines_for_signal(sig, interval='Min15', limit=5)
print('exchange=None -> MEXC path, empty:', df.empty)
sig2 = {'symbol': 'ETH_USDT'}
df2 = app._fetch_klines_for_signal(sig2, interval='Min15', limit=5)
print('exchange missing -> MEXC path, empty:', df2.empty)
print('PASS')
"
```
Expected: both print `False` (non-empty) if MEXC is reachable, `True` if not — either way, no exception raised. `PASS` printed.

- [ ] **Step 8: Commit**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && git add app.py && git commit -m "feat: add _fetch_klines_for_signal() — exchange-aware kline helper for MEXC and Hyperliquid"
```

---

## Task 2: Update evaluate_outcome() to use the helper

`evaluate_outcome()` currently fetches klines at one point (currently line ~2808) and extracts four arrays. Replace that block with a call to `_fetch_klines_for_signal()` plus DataFrame-to-array extraction. The candle scanning loop (`for c in candles:`) must not be touched.

**Files:**
- Modify: `app.py` (~line 2808–2821)

- [ ] **Step 1: Read the current block** (confirm line numbers match)

Run:
```bash
grep -n "fetch_mexc.*contract/kline\|raw_times\|raw_highs\|raw_lows\|raw_closes" /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/app.py | head -20
```
Expected: shows lines inside `evaluate_outcome()` for klines fetch and array extraction, and also lines in `compute_trade_journey()`. Note the line numbers for evaluate_outcome's block.

- [ ] **Step 2: Replace the kline fetch block in evaluate_outcome()**

**FIND** (inside `evaluate_outcome()`, after the `start_ts` computation):
```python
    klines = fetch_mexc(f"/contract/kline/{symbol}", params={
        "interval": "Min15",
        "start":    start_ts,
        "limit":    300,   # 300 × 15min = ~75 hours of coverage
    })
    if not klines or not isinstance(klines, dict):
        return None

    raw_times  = klines.get("time",  [])
    raw_highs  = klines.get("high",  [])
    raw_lows   = klines.get("low",   [])
    raw_closes = klines.get("close", [])
    if not raw_times or not raw_highs or not raw_lows or not raw_closes:
        return None
```

**REPLACE WITH:**
```python
    kline_df = _fetch_klines_for_signal(sig, interval="Min15", limit=300, start_ts=start_ts)
    if kline_df.empty:
        return None

    raw_times  = kline_df["timestamp"].tolist()
    raw_highs  = kline_df["high"].tolist()
    raw_lows   = kline_df["low"].tolist()
    raw_closes = kline_df["close"].tolist()
    if not raw_times or not raw_highs or not raw_lows or not raw_closes:
        return None
```

- [ ] **Step 3: Verify compile**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 -m py_compile app.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 4: Verify evaluate_outcome() still references the helper**

```bash
grep -n "_fetch_klines_for_signal\|fetch_mexc.*kline" /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/app.py | grep -A2 -B2 "evaluate_outcome\|2[89][0-9][0-9]:"
```
Confirm `_fetch_klines_for_signal` appears inside evaluate_outcome's body (roughly lines 2808–2815 area) and no `fetch_mexc.*kline` appears there.

- [ ] **Step 5: Verify no crash on outcomes check**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 app.py &
sleep 3
curl -s -X POST http://localhost:8080/api/outcomes/check | python3 -m json.tool
pkill -f "python3 app.py"
```
Expected: `{"success": true, "evaluated": N, "tagged": N, "skipped": N, "results": [...]}` — no crash

- [ ] **Step 6: Commit**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && git add app.py && git commit -m "feat: evaluate_outcome() uses _fetch_klines_for_signal() for exchange-aware kline routing"
```

---

## Task 3: Update compute_trade_journey() to use the helper

`compute_trade_journey()` has its own `fetch_mexc()` kline call (currently line ~2512). Same pattern: replace with `_fetch_klines_for_signal()`, extract arrays, leave candle loop intact.

**Files:**
- Modify: `app.py` (~line 2512–2536)

- [ ] **Step 1: Read the current block**

```bash
grep -n "fetch_mexc.*contract/kline" /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/app.py
```
Note the line that is inside `compute_trade_journey` (there should be one around line 2512).

- [ ] **Step 2: Replace the kline fetch block in compute_trade_journey()**

**FIND** (inside `compute_trade_journey()`, after `limit = min(300, ...)`):
```python
    klines = fetch_mexc(f"/contract/kline/{symbol}", params={
        "interval": "Min15",
        "start": start_ts,
        "limit": limit,
    })
    if not klines or not isinstance(klines, dict):
        return {"available": False, "reason": "kline history unavailable"}

    raw_times = klines.get("time", [])
    raw_highs = klines.get("high", [])
    raw_lows = klines.get("low", [])
    raw_closes = klines.get("close", [])
    candles: list[dict] = []
    for i, t in enumerate(raw_times):
```

**REPLACE WITH:**
```python
    kline_df = _fetch_klines_for_signal(sig, interval="Min15", limit=limit, start_ts=start_ts)
    if kline_df.empty:
        return {"available": False, "reason": "kline history unavailable"}

    raw_times = kline_df["timestamp"].tolist()
    raw_highs = kline_df["high"].tolist()
    raw_lows = kline_df["low"].tolist()
    raw_closes = kline_df["close"].tolist()
    candles: list[dict] = []
    for i, t in enumerate(raw_times):
```

- [ ] **Step 3: Verify compile**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 -m py_compile app.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 4: Verify no direct kline fetch remains in either function**

```bash
grep -n "fetch_mexc.*contract/kline" /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/app.py
```
Expected: remaining lines are only inside `enrich_signal()` and any helper functions — NOT inside `evaluate_outcome()` or `compute_trade_journey()`. Both those functions should show zero matches.

- [ ] **Step 5: Verify trade journey still works (smoke test)**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 -c "
import sqlite3, app
DB = 'data/signals.db'
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
# Find a closed MEXC signal to test journey
row = con.execute(
    \"SELECT * FROM signals WHERE result IS NOT NULL AND exchange='MEXC' ORDER BY id DESC LIMIT 1\"
).fetchone()
con.close()
if row:
    sig = dict(row)
    j = app.compute_trade_journey(sig)
    print('Journey available:', j.get('available'))
    print('PASS: compute_trade_journey() runs without error')
else:
    print('No closed MEXC signal in DB — skipping journey test')
    print('PASS: no crash')
"
```
Expected: `Journey available: True` (or False if no history) and `PASS`.

- [ ] **Step 6: Commit**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && git add app.py && git commit -m "feat: compute_trade_journey() uses _fetch_klines_for_signal() for exchange-aware kline routing"
```

---

## Task 4: Confirm exchange column in api_outcomes_check() SELECT

`api_outcomes_check()` uses `SELECT *` so `exchange` is already included. This task confirms it and adds a belt-and-suspenders default for safety.

**Files:**
- Modify: `app.py` (~line 2983–2985)

- [ ] **Step 1: Confirm SELECT * includes exchange**

```bash
grep -n "SELECT \* FROM signals WHERE result IS NULL\|exchange" /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/app.py | grep -A1 -B1 "SELECT \*"
```
Expected: shows `SELECT * FROM signals WHERE result IS NULL`. `SELECT *` includes all columns including `exchange`. ✅ No change needed to the SELECT itself.

- [ ] **Step 2: Verify exchange field flows into _fetch_klines_for_signal()**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 -c "
import sqlite3, app
DB = 'data/signals.db'
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
rows = con.execute('SELECT * FROM signals WHERE result IS NULL LIMIT 3').fetchall()
con.close()
for r in rows:
    sig = dict(r)
    print('signal id:', sig.get('id'), 'exchange:', repr(sig.get('exchange')), 'symbol:', sig.get('symbol'))
print('PASS: exchange field present in sig dict')
"
```
Expected: prints each open signal's id, exchange (e.g., `'MEXC'` or `'HYPERLIQUID'` or `None`), symbol. Confirms `exchange` key is accessible.

- [ ] **Step 3: Commit note only — no code change needed**

If SELECT * confirmed working, no commit needed. If the query needed updating, commit here. Document with a comment only:

```bash
# No code change — SELECT * already includes exchange. Confirmed by test above.
```

---

## Task 5: Run full verification checklist

- [ ] **Step 1: Compile both files**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 -m py_compile app.py lib/hyperliquid_client.py && echo "compile OK"
```
Expected: `compile OK`

- [ ] **Step 2: Import clean**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 -c "import app; print('import OK')"
```
Expected: `import OK`

- [ ] **Step 3: Outcomes check returns 200**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 app.py &
sleep 3
curl -s -X POST http://localhost:8080/api/outcomes/check | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['success'], d; print('PASS:', d['evaluated'], 'evaluated,', d['tagged'], 'tagged')"
pkill -f "python3 app.py"
```
Expected: `PASS: N evaluated, N tagged`

- [ ] **Step 4: History route still works**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 app.py &
sleep 3
curl -s "http://localhost:8080/api/signals/history?limit=5" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('success') or 'signals' in d, d; print('PASS: history OK')"
pkill -f "python3 app.py"
```
Expected: `PASS: history OK`

- [ ] **Step 5: No direct fetch_mexc kline calls remain in evaluate_outcome or compute_trade_journey**

```bash
python3 << 'EOF'
import ast, sys

with open('/Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/app.py') as f:
    src = f.read()

tree = ast.parse(src)
lines = src.splitlines()

forbidden_fns = {'evaluate_outcome', 'compute_trade_journey'}
violations = []

for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name in forbidden_fns:
        fn_lines = set(range(node.lineno, node.end_lineno + 1))
        for i, line in enumerate(lines[node.lineno-1:node.end_lineno], node.lineno):
            if 'fetch_mexc' in line and 'kline' in line:
                violations.append(f"  {node.name}() line {i}: {line.strip()}")

if violations:
    print("FAIL: direct fetch_mexc kline calls found:")
    for v in violations:
        print(v)
    sys.exit(1)
else:
    print("PASS: no direct fetch_mexc kline calls in evaluate_outcome or compute_trade_journey")
EOF
```
Expected: `PASS: no direct fetch_mexc kline calls in evaluate_outcome or compute_trade_journey`

- [ ] **Step 6: exchange column present in SELECT query**

```bash
grep -n "SELECT \* FROM signals WHERE result IS NULL" /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/app.py
```
Expected: line found (SELECT * includes exchange)

- [ ] **Step 7: stderr shows correct prefix during outcomes evaluation**

Start the app, trigger outcomes check, and inspect stderr:
```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && python3 app.py 2>stderr.log &
sleep 3
curl -s -X POST http://localhost:8080/api/outcomes/check > /dev/null
sleep 2
pkill -f "python3 app.py"
grep "\[hl_klines\]\|\[mexc_klines\]" stderr.log | head -10 || echo "(no kline log lines — no open signals to evaluate)"
rm -f stderr.log
```
Expected: either shows `[mexc_klines] BTC_USDT fetched N Min15 candles` and/or `[hl_klines] BTC_USDC fetched N 15m candles` depending on what's in the DB, OR `(no kline log lines — no open signals to evaluate)` if DB is empty.

---

## Task 6: Update HANDOFF.md

**Files:**
- Modify: `HANDOFF.md`

- [ ] **Step 1: Remove or update the HL outcome evaluation limitation note**

Find the section in HANDOFF.md that says:
```
One known limitation (out of scope per spec): the outcome evaluator background thread uses MEXC klines...
```
(This was added in the May 2 session summary.) Replace or update it to say the limitation is resolved.

- [ ] **Step 2: Add two "What NOT To Do" rules**

Find the "What NOT To Do" section and add:
```
- Do not fetch klines directly inside evaluate_outcome() or compute_trade_journey() —
  always use _fetch_klines_for_signal(sig, interval, limit) so exchange routing is
  handled in one place.
- Do not assume sig.exchange is always set — _fetch_klines_for_signal() defaults to
  MEXC when exchange is None or empty string.
```

- [ ] **Step 3: Update app.py line count in header**

Run:
```bash
wc -l /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0/app.py
```
Update `app.py: XXXX lines` in HANDOFF.md header.

- [ ] **Step 4: Add session summary for May 3, 2026**

Add to HANDOFF.md session summaries section:
```
### May 3, 2026 — HL outcome evaluation

- Added `_fetch_klines_for_signal(sig, interval, limit, start_ts)` in app.py —
  exchange-aware kline helper that routes to fetch_hl_klines() for HYPERLIQUID
  signals and fetch_mexc() for MEXC/unknown signals.
- Patched evaluate_outcome() and compute_trade_journey() to use the helper.
  Evaluation logic (candle scanning loops) unchanged.
- Confirmed api_outcomes_check() SELECT * already includes exchange column.
- Hyperliquid signals will now be auto-evaluated by the 15-minute background loop.
```

- [ ] **Step 5: Commit**

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0 && git add HANDOFF.md && git commit -m "docs: update HANDOFF.md — HL outcome evaluation resolved, May 3 session summary"
```
