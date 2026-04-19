# Matrix Trader 7.0 — AI Handoff Document

> Paste this entire file at the start of any AI session (ChatGPT, Gemini,
> etc.) when continuing work on this project. Read every word before
> writing a single line of code. This file was auto-generated from the
> actual codebase — it reflects current state, not planned state.

Last updated: 2026-04-19
Last commit: b0ee1cc P2a: strategy registry — Balanced/Funding Arb/Momentum/Mean Reversion with pill UI
app.py: 673 lines
index.html: 1903 lines

---

## What This Project Is

Matrix Trader 7.0 is a local web application for high-leverage crypto trading on MEXC perpetual swap markets. A Python Flask backend serves a single-file dark-theme dashboard. The user scans 800+ MEXC perp tickers, receives ranked LONG/SHORT signals with entry/TP/SL ladders, and executes trades manually. It is not an auto-trading bot, not a price forecasting engine, and not a SaaS product. The AI layer (Claude API) is reserved and not yet called.

---

## Why These Rules Exist (MT2–MT6 Failures)

| MT6 Mistake | MT7 Rule |
|---|---|
| Matrix chat bot as delivery mechanism | Web app only |
| ARIMA price forecasting | No forecasting. Signals only. |
| Two competing TUI implementations | One interface: the web dashboard |
| Coinglass API key committed in plaintext | All keys in `.env`, never committed |
| 17 planning markdown files instead of code | Ship before you plan |
| God class `EnhancedTradingBot` (900+ lines) | `app.py` stays flat until Phase 2 is complete |
| Multi-exchange as primary venues | MEXC is primary. Others are context. |

---

## Hard Rules

1. **Ship before you plan.** Running code before the next feature.
2. **One file, one job.** `app.py` stays flat. `lib/` files are pure functions.
3. **No features that don't serve the trader.** If it doesn't help make a better trade decision, it doesn't ship.
4. **The mobile test is non-negotiable.** Every UI change must work on iPhone Safari.
5. **No committed secrets.** `.env` only. `.env` is in `.gitignore` from day one.
6. **Error handling is a feature.** Every API call is wrapped in try/except. App never crashes.
7. **Signal quality over quantity.** 20 high-conviction signals beats 200 weak ones.
8. **The tool is for trading, not for looking at.** Aesthetics serve the signal, not the other way around.
9. **S and M state objects are completely isolated.** Never share state between signals and market tabs.
10. **No JS frameworks.** Vanilla JS only.
11. **No glassmorphism, gradients, or drop shadows.** Dark flat UI only.
12. **Read the actual files before writing a single line.** Do not assume state from memory or prior sessions.

---

## File Structure

```
Matrix_Trader_7.0/
├── CLAUDE.md              ← source of truth for Claude Code sessions
├── HANDOFF.md             ← this file (auto-generated)
├── README.md              ← planned for P4
├── .gitignore
├── .env                   ← ANTHROPIC_API_KEY, MEXC_API_KEY (not committed)
├── requirements.txt
├── app.py                 ← entire Flask backend, 673 lines
├── templates/
│   └── index.html         ← entire frontend (CSS + HTML + JS), 1903 lines
├── static/
│   └── style.css          ← minimal overrides (most CSS is inline in index.html)
└── lib/                   ← pure utility functions, no Flask, no API calls
    ├── indicators.py      ← vwap, ema, rsi, atr, atr_pct, volatility_regime
    ├── laddering.py       ← generate_ladders(price, atr, tiers, direction)
    └── mexc_stream.py     ← WebSocket client (built, not yet wired to UI)
```

---

## Tech Stack

From `requirements.txt` — all installed:

```
flask>=3.0.0
requests>=2.31.0
pandas>=2.0.0
numpy>=1.24.0
websocket-client>=1.6.0
python-dotenv>=1.0.0
anthropic>=0.39.0
```

Run with: `python3 app.py`
Default port: **8080** (auto-increments if busy — avoids macOS AirPlay on 5000)

---

## MEXC API Reference

All public, no auth required:

```
Base URL: https://contract.mexc.com/api/v1

GET /contract/ticker                    — all perp tickers (800+ pairs)
GET /contract/detail                    — contract specs (leverage, fees)
GET /contract/kline/{symbol}            — OHLCV data
GET /contract/depth/{symbol}            — orderbook
GET /contract/funding_rate/{symbol}     — current funding rate

Intervals: Min1, Min5, Min15, Min30, Min60, Hour4, Hour8, Day1
```

Response wrapper: `{ "success": true, "data": [...] }`

---

## Flask Routes

| Route | Method | Description |
|---|---|---|
| `/` | GET | Renders `templates/index.html` |
| `/api/scan` | GET | Two-stage scan of all MEXC perps. Params: `threshold` (int, default 55), `strategy` (str, default "balanced"). Returns enriched signals. |
| `/api/market` | GET | Returns all 800+ tickers scored at stage 1 (no klines). Used by market browser tab. |
| `/api/signal/<symbol>` | GET | Enriches a single symbol on demand. Used by market tab click-to-enrich. |

---

## Signal Data Shape

Full dict returned by `enrich_signal()` in `app.py`:

```python
{
    "symbol":               str,        # "BTC_USDT"
    "exchange":             str,        # "MEXC"
    "direction":            str,        # "LONG" | "SHORT"
    "conviction":           int,        # 0–100 (stage 2 enriched)
    "price":                float,
    "entries":              [float, float, float],
    "exits":                [float, float, float],
    "stop_loss":            float,
    "change_24h_pct":       float,
    "change_4h_pct":        float,
    "change_1h_pct":        float,
    "funding_rate":         float,
    "open_interest":        float,
    "next_funding_minutes": int | None,
    "volume_24h":           float,
    "atr_pct":              float,
    "volatility":           str,        # "low" | "medium" | "high" | "extreme"
    "rsi_1h":               float,
    "trend_score":          int,        # -100 to +100
    "tags":                 [str],
    "basis_pct":            None,       # reserved
    "ai_report":            None,       # reserved for P2c
    "strategy":             str,        # e.g. "Funding Arb"
    "leverage_cap":         int,        # e.g. 10
}
```

`score_ticker()` (stage 1) returns a subset: `symbol`, `exchange`, `direction`, `conviction_base`, `price`, `change_24h_pct`, `funding_rate`, `volume_24h`, `open_interest`, `tags`.

---

## Strategy Registry

Four presets in `STRATEGIES` dict in `app.py`. Each has `weights`, `filters`, `leverage_cap`, `min_conviction`.

| Key | Name | leverage_cap | min_conviction | Stage-1 filter | Stage-2 filter |
|---|---|---|---|---|---|
| `balanced` | Balanced | 20 | 55 | none | none |
| `funding_arb` | Funding Arb | 10 | 60 | `\|funding\| > 0.0003` | none |
| `momentum_breakout` | Momentum Breakout | 25 | 55 | `\|24h Δ\| > 3%` | none |
| `mean_reversion` | Mean Reversion | 15 | 65 | none | RSI < 35 (LONG) or RSI > 65 (SHORT) |

Scoring weights (strong / weak tier):

| Strategy | Momentum | Funding | Basis | Vol mult |
|---|---|---|---|---|
| Balanced | 30 / 15 | 25 / 10 | 15 | 1.1× |
| Funding Arb | 10 / 5 | 50 / 20 | 20 | 1.0× |
| Momentum Breakout | 50 / 25 | 10 / 4 | 5 | 1.2× |
| Mean Reversion | 5 / 2 | 30 / 12 | 30 | 1.0× |

`run_scan()` enforces `effective_threshold = max(threshold, strat["min_conviction"])`.

---

## JavaScript State Objects

Exact code from `index.html`:

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
};

const M = {
  phase:        'idle',   // idle | loading | ready | error
  pairs:        [],       // all pairs from /api/market
  filtered:     [],       // after search + dir filter + sort
  dir:          'all',
  sort:         'conviction_base',
  search:       '',
  page:         0,
  pageSize:     50,
  renderedCount: 0,
  sortDir:      'desc',
};

let currentTab = 'signals';
let currentTVSymbol   = null;
let currentTVInterval = '60';
```

`S` and `M` are completely isolated. Never read S from market code or M from signals code.

---

## Dashboard Structure

Three tabs, three sections:

| Tab button ID | Section ID | Description |
|---|---|---|
| `tab-signals` | `signals-section` | Strategy bar, stat bar, filter bar, signal rows, detail panel |
| `tab-market` | `market-section` | 800+ pair browser with search, sort, heat coloring, click-to-enrich |
| `tab-tools` | `tools-section` | Risk Calculator + Compound Planner |

**Signals section sub-elements:**
- `#strategy-bar` — always visible pill buttons: Balanced / Funding Arb / Momentum / Mean Rev
- `#stat-bar` — shown after scan: Scanned / Signals / Longs / Shorts
- `#filter-bar` — shown after scan: All/Long/Short + sort dropdown (11 options)
- `#list-col` — contains `#idle-cta`, `#scan-status` (progress bar + skeletons), `#sig-rows`, `#empty-state`, `#error-state`
- `#detail-panel` (260px, right side) — trade plan ladder, compact TradingView chart (160px, 1h), context grid (10 items incl. Strategy + Max Leverage), tags, AI placeholder

**Market section sub-elements:**
- `#mkt-idle`, `#mkt-loading`, `#mkt-error`, `#mkt-ready`
- `#mkt-controls` — search input + sort select
- `#mkt-table` — 8-column grid: Symbol / Price / Dir / Score / 24h% / Funding / Volume / OI
- `#mkt-rows` — infinite scroll (50-row batches via DocumentFragment)

**Tools section sub-elements:**
- Risk Calculator: dollar risk → position size, contracts, margin, liquidation price, R:R
- Compound Planner: expected value over 90 days, milestone table, canvas chart, worst streak

---

## TradingView Integration

```javascript
function toTVSymbol(mexcSymbol, exchange) {
  const map = {
    'MEXC':        s => 'MEXC:'        + s.replace('_', '')         + '.P',
    'BINANCE':     s => 'BINANCE:'     + s.replace('_USDT', 'USDT') + '.PERP',
    'BYBIT':       s => 'BYBIT:'       + s.replace('_', '')         + '.P',
    'HYPERLIQUID': s => 'HYPERLIQUID:' + s.split('_')[0]            + 'USDT.P',
  };
  return (map[exchange] || map['MEXC'])(mexcSymbol);
}
```

- Compact preview: 160px height, 1h fixed, EMA + VWAP only, no toolbar
- Full chart modal: 80vh / 700px max, 15m/1h/4h/1D toggle, RSI + EMA + VWAP, side toolbar
- 5-second iframe fallback for unknown symbols
- `currentTVSymbol` tracks active symbol to prevent stale loads

---

## Color System

```css
:root {
  --bg:     #0b0d12;
  --bg2:    #0e1016;
  --bg3:    #0a0b0f;
  --border: rgba(255,255,255,0.06);
  --text:   #e8e8f0;
  --text2:  rgba(255,255,255,0.45);
  --text3:  rgba(255,255,255,0.25);
  --green:  #00e676;
  --red:    #ff5252;
  --amber:  #ffab40;
  --blue:   #448aff;
  --mono:   'SF Mono', Menlo, 'Courier New', monospace;
}
```

---

## Phase Status

| Phase | What | Status |
|---|---|---|
| P0 | Flask app running, MEXC ticker scan, basic scoring, web dashboard | ✅ Done |
| P1 | Indicators integrated, entry/TP/SL on signals, risk calc, compound planner | ✅ Done |
| P2a | Strategy registry (Balanced/Funding Arb/Momentum/Mean Rev), pill UI | ✅ Done |
| P2b | Signal card "Why" line, freshness indicator, invalidation condition | 🔄 Next |
| P2c | Template-based AI signal report in detail panel | Planned |
| P2d | CoinGlass free tier integration (OI, long/short ratio) | Planned |
| P2e | Error hardening, volatility/volume filters, localStorage preferences | Planned |
| P3 | Signal history logging, trade log, outcome tracking, WebSocket live prices | Planned |
| P4 | README, GitHub publish, 5 external beta testers | Planned |

---

## Current Task List (Priority Order)

1. **P2b — Signal card redesign** (next)
   - Add a "Why this signal" plain-English line on each signal row, generated from a Python string template using signal dict fields (e.g. "RSI oversold at 28 with negative funding — short squeeze setup")
   - Add signal freshness indicator on each card showing age since scan: fresh (<5 min) / aging (5–15 min) / stale (>15 min) — driven by `S.scanTime`
   - Add invalidation condition to the detail panel derived from `stop_loss` and `atr_pct` (e.g. "Invalidated if price closes above 42,150 — that's 1.4× ATR from entry")

2. **P2c — Template-based AI signal report**
   - Python string template using signal dict fields, renders in detail panel `ai_report` placeholder
   - No Anthropic API call — completely free
   - Covers: thesis, entry timing, invalidation, risk note
   - Optional: user-supplied API key for narrative upgrade

3. **P2d — CoinGlass integration**
   - Free API key → `COINGLASS_API_KEY` in `.env`
   - Aggregated OI and long/short ratio
   - Add to context grid in detail panel

4. **P2e — Error hardening + filter improvements**
   - Retry logic for MEXC rate limits
   - Filter by volatility regime
   - Filter by minimum volume threshold
   - Save filter preferences to localStorage

---

## What NOT To Do

- Do not add ARIMA, price prediction, or any forecasting logic
- Do not create new files without a specific reason (no new routes file, no new config file)
- Do not use React, Vue, or any JS framework — vanilla JS only
- Do not add glassmorphism, gradients, or drop shadows to the UI
- Do not commit `.env` or any API keys
- Do not split `app.py` into multiple files — it stays flat until P2 is fully done
- Do not call the Anthropic API — it is reserved and the key is in `.env` but not yet wired
- Do not auto-trigger a new scan when the user switches strategy — let them click Scan
- Do not share state between `S` (signals) and `M` (market) objects
- Do not use `conviction_base` for display — always use `conviction` (the enriched value)
- Do not modify `lib/` files with Flask routes or API calls — they are pure functions only

---

## How To Run

```bash
pip install -r requirements.txt
python3 app.py
```

- Mac: `http://localhost:8080`
- iPhone (same WiFi): `http://192.168.x.x:8080`
- Port auto-increments to 8081, 8082, etc. if 8080 is busy
- Set `MATRIX_PORT` env var to override default

---

## Task Framing Template

When asking any AI to make a change, use this format:

```
Read CLAUDE.md and HANDOFF.md before doing anything.
We are working on [task name].
[Describe exactly what to build — be specific about file names, function names, data fields.]
Read the actual files before writing anything.
Explain every decision.
```

---

## Verification Checklist

Before marking any task complete:

- [ ] `python3 app.py` starts without errors
- [ ] `/api/scan` returns valid JSON with signals array
- [ ] Signal rows render in browser
- [ ] Detail panel opens on signal click
- [ ] Mobile layout works (test at 390px width or on actual iPhone)
- [ ] No console errors in browser devtools
- [ ] No secrets committed (check `git diff --staged`)
- [ ] `lib/` files contain no Flask routes or imports
- [ ] `app.py` still has a single flat structure (no classes, no blueprints)
- [ ] All four strategy pills render and switch correctly
- [ ] `/api/scan?strategy=funding_arb` returns signals with `leverage_cap: 10`
- [ ] Strategy label updates in `#strat-lbl` when pill is clicked
- [ ] New feature tested on both desktop and mobile layout

---

## Returning to Claude Code

Open a new chat in this project and paste the exact prompt from the
Current Task List section above. Claude Code has access to CLAUDE.md
automatically — HANDOFF.md supplements it with richer context.

---

## Session Notes

### 2026-04-19 — P1 verified complete, P2a built

Built:
- Verified P1 complete against actual code: `lib/indicators.py` (RSI, EMA, VWAP, ATR, atr_pct, volatility_regime), `lib/laddering.py` (generate_ladders with LONG/SHORT), `lib/mexc_stream.py` (built, not wired), full `enrich_signal()` pipeline, Risk Calculator, Compound Planner
- P2a Strategy Registry: `STRATEGIES` dict with 4 presets, `score_ticker()` and `enrich_signal()` accept `strategy` param, stage-1 filters (funding abs, 24h change pct), stage-2 RSI filter (mean reversion only), `run_scan()` with `strategy_key` param and `effective_threshold`, `/api/scan?strategy=X` route, `#strategy-bar` with 4 pill buttons always visible, `setStrategy()` JS, `S.strategy` state, `STRAT_META` display lookup, `leverage_cap` and `strategy` name in context grid

Decided:
- Strategy bar is always visible (not gated on scan phase) so users select before scanning
- Changing strategy does NOT auto-trigger rescan — user clicks Scan to apply
- `/api/market` and `/api/signal/<symbol>` use Balanced defaults (strategy-agnostic)
- `effective_threshold = max(threshold, strat["min_conviction"])` enforces each strategy's floor
- `functools.partial` used to thread strategy through `ThreadPoolExecutor.map()`
- Stage-1 filters return None before scoring to avoid wasting enrichment quota on ineligible tickers
- Mean reversion RSI filter applied in stage 2 after klines are fetched (RSI requires OHLCV)

Deferred:
- `lib/mexc_stream.py` wiring to UI (P3)
- `basis_pct` computation (requires spot price comparison)
- `ai_report` generation (P2c)

Watch out for:
- `conviction_base` (stage 1) vs `conviction` (stage 2 enriched) — display always uses `conviction`
- Mean reversion filter is STRICT: signals are discarded if RSI doesn't meet threshold, not penalized
- `STRAT_META` in JS is display-only — authoritative strategy data lives in `STRATEGIES` in `app.py`
- `#strategy-bar` uses `.strat-btn`; `#filter-bar` uses `.dir-btn` — do not mix selectors
