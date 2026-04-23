# Matrix Trader 7.0 — AI Handoff Document

> Paste this entire file at the start of any AI session (ChatGPT, Gemini,
> etc.) when continuing work on this project. Read every word before
> writing a single line of code. This file was auto-generated from the
> actual codebase — it reflects current state, not planned state.

Last updated: 2026-04-23
Last commit: 8037615 fix: fmtAge naming collision causing corrupted scan timestamp; fix idle-cta height clipping text
app.py: 1576 lines
index.html: 3464 lines

---

## What This Project Is

Matrix Trader 7.0 is a local web application for high-leverage crypto trading on MEXC perpetual swap markets. A Python Flask backend serves a single-file dark-theme dashboard. The user scans 800+ MEXC perp tickers, receives ranked LONG/SHORT signals with entry/TP/SL ladders derived from ATR, views a 4-section AI trade brief, and executes trades manually. Signal history is auto-logged to SQLite on every scan. It is not an auto-trading bot, not a price forecasting engine, and not a SaaS product.

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
13. **No databases for application state.** SQLite is acceptable for signal history logging and outcome tracking only.

---

## File Structure

```
Matrix_Trader_7.0/
├── CLAUDE.md              ← source of truth for Claude Code sessions
├── HANDOFF.md             ← this file (auto-generated)
├── README.md              ← exists, minimal
├── .gitignore             ← includes .env, __pycache__, data/
├── .env                   ← ANTHROPIC_API_KEY, MEXC_API_KEY (not committed)
├── requirements.txt
├── app.py                 ← entire Flask backend, 1576 lines
├── backtest.py            ← standalone backtest script, 4 strategies × 14 symbols
├── templates/
│   └── index.html         ← entire frontend (CSS + HTML + JS), 3464 lines
├── static/
│   └── style.css          ← minimal overrides (most CSS is inline in index.html)
├── docs/
│   ├── design-brief.md    ← original design document
│   └── project-status.md  ← human-readable status overview
├── data/                  ← gitignored; created at runtime
│   ├── signals.db         ← SQLite signal history (auto-created by init_db())
│   └── backtest_results.json ← written by backtest.py
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

SQLite3 is stdlib — no pip install needed. Used for `data/signals.db`.

---

## MEXC API Reference

All public, no auth needed:

```
Base URL: https://contract.mexc.com/api/v1

GET /contract/ticker                    — all perp tickers (800+ pairs)
GET /contract/detail                    — contract specs (leverage, fees)
GET /contract/kline/{symbol}            — OHLCV data
GET /contract/depth/{symbol}            — orderbook
GET /contract/funding_rate/{symbol}     — current funding rate
GET /contract/funding_rate/history      — funding history (?symbol=X&page_num=N&page_size=N)

Intervals: Min1, Min5, Min15, Min30, Min60, Hour4, Hour8, Day1
```

Response wrapper: `{ "success": true, "data": [...] }`

Sentiment APIs (no key needed):
- OKX L/S: `https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=1H`
- OKX OI:  `https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP`
- Binance and Bybit return 451/403 on US IPs — handled gracefully with None fallback.

---

## Flask Routes

| Route | Method | Description |
|---|---|---|
| `/` | GET | Serves index.html dashboard |
| `/api/scan` | GET | Full scan: scores 800+ tickers, enriches top 30, logs to DB, returns signals array |
| `/api/market` | GET | Returns all scored tickers for market browser (no enrichment) |
| `/api/signal/<symbol>` | GET | Fully enriches a single symbol on demand |
| `/api/signal/result` | PATCH | Tags a logged signal with WIN/LOSS/PARTIAL/EXPIRED/SKIPPED |
| `/api/signals/history` | GET | Returns logged signal history; filters: strategy, result, symbol, limit |
| `/api/outcomes/check` | POST | Evaluates all open (untagged) signals against Min15 klines; auto-tags hits |
| `/api/prices` | GET | Batch price fetch for multiple symbols — used by open positions panel |
| `/api/analysis` | POST | AI strategy review: sends last 200 tagged outcomes to Claude API |

Query params for `/api/scan`: `threshold` (int, default 55), `strategy` (string, default "balanced").
Query params for `/api/signals/history`: `limit` (int, default 100), `strategy`, `result`, `symbol`.
Query params for `/api/prices`: `symbols` (comma-separated string of symbol names).

Background thread `_outcome_loop` runs `api_outcomes_check()` every 15 minutes automatically (daemon thread, starts on import, 1-minute startup delay).

---

## Signal Data Shape

Full dict returned by `enrich_signal()` and sent to the frontend:

```python
{
  # Identity
  "symbol":               str,   # e.g. "BTC_USDT"
  "exchange":             str,   # always "MEXC"
  "direction":            str,   # "LONG" or "SHORT"
  "strategy":             str,   # human name e.g. "Balanced"
  "leverage_cap":         int,   # from strategy registry

  # Conviction
  "conviction":           int,   # 0–100 after stage-2 adjustments

  # Price & ladders
  "price":                float,
  "entries":              list[float],  # [e1, e2, e3] nearest→farthest from price
  "exits":                list[float],  # [tp1, tp2, tp3]
  "stop_loss":            float,

  # Momentum
  "change_24h_pct":       float,
  "change_4h_pct":        float,
  "change_1h_pct":        float,

  # Funding / OI
  "funding_rate":         float,   # decimal (e.g. -0.0001)
  "open_interest":        float,
  "next_funding_minutes": int | None,

  # Volume
  "volume_24h":           float,

  # Technicals
  "atr_pct":              float,   # ATR as % of price
  "volatility":           str,     # "low" | "medium" | "high" | "extreme"
  "rsi_1h":               float,
  "trend_score":          int,     # -100 to +100, EMA20/50 alignment + price position bonus
  "basis_pct":            None,    # reserved

  # Daily trend
  "daily_trend":          str | None,   # "LONG" | "SHORT" | "NEUTRAL" | None
  "daily_trend_aligned":  bool | None,  # True if signal direction matches daily trend

  # Tags
  "tags":                 list[str],  # e.g. ["short_squeeze", "high_volume"]

  # Generated text
  "signal_why":           str,     # one-line plain-English reason
  "ai_report":            str,     # JSON string: [{label, text}, ...] 4 sections

  # Market sentiment (from Binance/Bybit/OKX public APIs)
  "binance_ls_long_pct":  float | None,
  "binance_oi":           float | None,
  "bybit_oi":             float | None,
  "okx_ls_long_pct":      float | None,
  "okx_oi":               float | None,
  "sentiment_tracked":    bool,   # False for MEXC-only altcoins, True for major pairs
}
```

`ai_report` is a JSON string: `[{"label": "Setup", "text": "..."}, {"label": "Structure", ...}, ...]`
Parse with `JSON.parse()` in the frontend. Four sections: Setup, Structure, Invalidation, Risk.

---

## JavaScript State Objects

Copied verbatim from index.html:

```js
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

const H = {
  loading:         false,   // guards concurrent loadHistory() calls
  openPositions:   [],      // untagged signals (result === null)
  priceCache:      {},      // symbol → {price, ts} from /api/prices
  posRefreshTimer: null,    // setInterval handle — 30s price refresh
  posCounterTimer: null,    // setInterval handle — per-second "Xs ago" counter
  posLastRefresh:  null,    // Date.now() of last successful price fetch
  posFetching:        false,  // guards concurrent fetchAndRenderPositions() calls
  selectedPositionId: null,   // id of position whose detail panel is currently open
  posSort:            'age',  // active sort column for open positions table
  posSortDir:         'asc',  // 'asc' | 'desc'
  posStratFilter:     '',     // '' = all strategies
};

let lastHistorySigs = [];

const STRATEGY_LEVERAGE = { balanced: 20, funding_arb: 10, momentum_breakout: 25, mean_reversion: 15 };
```

`S` and `M` are completely isolated. Never cross-reference them.
`H` manages history tab state — open positions list, price cache, timer handles, sort/filter state.

---

## Dashboard Structure

Four tabs, one shared detail panel:

| Tab button | Section div | Loaded by |
|---|---|---|
| `#tab-signals` | `#signals-section` | `scanSignals()` on button click |
| `#tab-market` | `#market-section` | `loadMarket()` on tab switch |
| `#tab-tools` | `#tools-section` | Static, rendered at init |
| `#tab-history` | `#history-section` | `loadHistory()` on tab switch (auto) |

**History tab sub-sections:**
- `#open-positions-section` — live open positions (untagged signals) with P&L tracking, 30s price refresh
- `#closed-signals-section` — tagged signals with outcome buttons, equity curve, strategy review

**Shared detail panel** (`#detail-panel`, `<aside>`): populated by `renderDetail(sig)` from signals or market tab. Slides in from right on desktop; slides up from bottom on mobile.

**Strategy bar** (inside `#signals-section`): four pills (Balanced / Funding Arb / Momentum / Mean Rev).

**Filter bar** (inside `#signals-section`): direction toggle, volatility filter select, min-volume input. State persisted to localStorage key `mt7_filters`.

**Open positions panel** (`#open-positions-section`):
- `#open-positions-header` — account size input, live performance banner (signals, win rate, P&L, open, best trade, streak)
- `#open-positions-body` — sortable table (age, symbol, direction, entry, current price, P&L, leverage)
- Click a position row → live P&L status bar, progress bar to TP3, TP markers, 30s auto-refresh detail
- Positions auto-sync with server-side outcome checker on every `loadHistory()` call

**Closed signals panel** (`#closed-signals-section`):
- Summary stats bar: Logged · Tagged · Win Rate · W/L/P counts · Avg Conviction (wins vs losses)
- Equity curve: sparkline chart of account growth over time, with hover tooltip (crosshair, date, balance, change)
- Outcome buttons: WIN / LOSS / PAR / EXP / SKP — PATCH `/api/signal/result`, full reload after
- Three filter selects: Strategy · Direction (client-side) · Result
- Strategy Review button → `requestAnalysis()` → POST `/api/analysis`

---

## TradingView Integration

```js
function toTVSymbol(mexcSymbol, exchange) {
  exchange = exchange || 'MEXC';
  const map = {
    'MEXC':        s => 'MEXC:'        + s.replace('_', '')         + '.P',
    'BINANCE':     s => 'BINANCE:'     + s.replace('_USDT', 'USDT') + '.PERP',
    'BYBIT':       s => 'BYBIT:'       + s.replace('_', '')         + '.P',
    'HYPERLIQUID': s => 'HYPERLIQUID:' + s.split('_')[0]            + 'USDT.P',
  };
  return (map[exchange] || map['MEXC'])(mexcSymbol);
}
```

Chart uses TradingView Advanced Chart widget. Config: `autosize: true`, `theme: 'dark'`, `style: '1'` (candles), `interval: '60'` (default 1h). Timeframe toggle buttons (15m / 1h / 4h) re-render the chart.

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

No glassmorphism, no gradients, no drop shadows. Flat dark UI only.

---

## Phase Status

| Phase | What | Status |
|---|---|---|
| P0 | Flask app, MEXC scan, basic scoring, web dashboard | ✅ Done |
| P1 | Indicators, entry/TP/SL, market browser, charts, risk calc, compound planner | ✅ Done |
| P2a | Strategy registry (Balanced/Funding Arb/Momentum/Mean Rev) | ✅ Done |
| P2b | Why-line, freshness dot, invalidation condition | ✅ Done |
| P2c | Template-based AI signal report (4-section trade brief) | ✅ Done |
| P2d | Market sentiment (OKX live, Binance/Bybit graceful fallback) | ✅ Done |
| P2e | Retry logic, specific error messages, volatility/volume filters, localStorage | ✅ Done |
| UX  | First-run guide, strategy tooltips, tag hover tooltips | ✅ Done |
| Backtest | backtest.py — 14 symbols × 4 strategies, real funding rate history | ✅ Done |
| P3a | SQLite signal history — auto-log on scan, PATCH outcome, GET history | ✅ Done |
| P3b | History tab UI — summary bar, outcome buttons, filters, win rate | ✅ Done |
| P3c | AI strategy review — POST /api/analysis, Claude API, History tab button | ✅ Done |
| P3d | Open positions panel — live P&L, 30s refresh, auto-tagging, equity curve | ✅ Done |
| P3e | WebSocket live price refresh for watched pairs | 🔲 Next |
| P4 | README, GitHub publish, 5 external beta testers | 🔲 Planned |

---

## Current Task List

Next in priority order:

1. **P3e — WebSocket live price refresh**: Wire `lib/mexc_stream.py` to the frontend for real-time price updates on open positions and watched pairs. The library exists and is complete but is not connected to any Flask route or UI. Approach: add a Server-Sent Events (SSE) endpoint in `app.py` that streams price updates; frontend subscribes on History tab entry and unsubscribes on tab leave. This replaces/augments the current 30s polling via `GET /api/prices`.

2. **P4 — Public release**: Write external-facing `README.md` with full setup instructions (currently minimal). Push to GitHub. Recruit 5 external beta testers.

---

## What NOT To Do

- Do not call `enrich_signal()` from `backtest.py` — it makes live API calls (kline, depth, funding, sentiment). Use `lib/` functions directly.
- Do not import from `app.py` in a way that triggers Flask server startup — `if __name__ == "__main__":` guard exists; all module-level code runs on import (including `load_dotenv()`).
- Do not add new columns to the `signals` SQLite table without a migration — existing `data/signals.db` files on user machines won't have the column and will error. Add `ALTER TABLE` logic to `init_db()` if schema changes.
- Do not use `datetime.now()` — always use `datetime.utcnow()` for consistency with existing logged timestamps. All stored timestamps are UTC ISO strings without Z suffix.
- Do not use `con.row_factory = sqlite3.Row` in write paths — only needed for SELECT + `dict(r)` serialization.
- Do not add JS frameworks. No React, Vue, jQuery, Alpine, or similar.
- Do not add glassmorphism, gradients, or drop shadows to the UI.
- Do not commit `.env`, `data/`, or `__pycache__/` — all in `.gitignore`.
- Do not modify `S` state from market tab code or `M` state from signals tab code.
- Do not run the full backtest during CI or import-time — it makes live MEXC API calls and takes several minutes.
- Do not filter direction server-side in `/api/signals/history` — direction is filtered client-side in `loadHistory()`. If you add server-side direction filtering, remove the client-side filter to avoid double-filtering.
- Do not use `title` attributes on mobile-only UI elements — native browser tooltips don't appear on touch devices.
- Do not rename `fmtAge` — a naming collision between the history tab helper and a stale global caused corrupted scan timestamps (fixed in commit 8037615). The function name is intentional.
- Do not touch the equity sparkline's mousemove/crosshair logic without testing hover on mobile — canvas mouse events behave differently on touch.
- Do not change strategy filter option values in the history tab — they must match the DB strings exactly: "Balanced", "Funding Arb", "Momentum Breakout". A mismatch causes silent empty results.

---

## How To Run

```bash
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY
python3 app.py
```

Opens at `http://localhost:8080` (auto-increments if busy).
Opens at `http://<LAN_IP>:8080` on iPhone (same WiFi).
Port configurable via `MATRIX_PORT` env var.

Run backtest separately (makes live MEXC API calls, takes ~5 min):
```bash
python3 backtest.py
# Results saved to data/backtest_results.json
```

---

## Task Framing Template

When asking any AI to make a change, frame it exactly like this:

```
Read CLAUDE.md and HANDOFF.md before touching anything.

[Task description]

Constraints:
- No backend changes / No frontend changes (whichever applies)
- Do not change [specific things to preserve]

After writing: [verification steps]
Explain every decision.
```

---

## Verification Checklist

Before reporting any task complete:

- [ ] `python3 -c "import app; print('OK')"` exits clean
- [ ] `GET /` returns 200
- [ ] `GET /api/scan` returns JSON with `success: true`
- [ ] `GET /api/signals/history` returns 200
- [ ] `POST /api/outcomes/check` returns 200
- [ ] `GET /api/prices?symbols=BTC_USDT` returns 200
- [ ] Signal cards show entry/TP/SL in the detail panel
- [ ] Strategy pills work and update `#strat-lbl`
- [ ] History tab loads on click and shows table (or empty state)
- [ ] Open positions panel shows untagged signals with live prices
- [ ] Outcome buttons update `result` and reload the table
- [ ] Equity sparkline renders and hover tooltip shows date/balance/change
- [ ] Mobile: UI fits on 375px screen without horizontal scroll (except history table which scrolls intentionally)
- [ ] No `console.error` in browser on page load
- [ ] No Python traceback on server start
- [ ] localStorage key `mt7_filters` persists filter state across reloads
- [ ] localStorage key `mt7_guide_seen` hides the first-run guide on return visits

---

## Returning to Claude

Start every Claude Code session with:

```
Read CLAUDE.md and HANDOFF.md before touching anything.
[Your task here]
```

Claude Code reads the actual files. It does not need the full HANDOFF.md pasted into the prompt — just the instruction to read it. For other AIs (ChatGPT, Gemini), paste this entire file.

---

## Session Notes

### 2026-04-19 — Session summary
Built: P2a strategy registry (Balanced/Funding Arb/Momentum/Mean Reversion), pill UI, STRAT_META object, setStrategy() wiring.
Decided: Strategy profiles live in app.py as a dict; weights/filters/caps all in one place.
Deferred: Strategy-specific UI differentiation beyond the label.
Watch out for: STRAT_META in index.html must stay in sync with STRATEGIES dict in app.py — same keys, same names.

### 2026-04-20 — Session summary
Built: P2c AI trade brief (generate_report() — 4-section JSON, §1 strategy-aware, §3 ATR formula parity with JS). P2d market sentiment via OKX (live), Binance/Bybit (graceful 451/403 fallback). P2e error hardening (fetch_mexc() 3-attempt retry, specific error messages on scan fail, volatility/volume filters, localStorage persistence for filters). UX additions: first-run guide overlay, strategy pill tooltips (native title attr), tag hover tooltips (TAG_TIPS lookup). P3a SQLite signal history (init_db, log_signals, PATCH /api/signal/result, GET /api/signals/history). P3b History tab UI (summary bar with win rate stats, outcome button group WIN/LOSS/PAR/EXP/SKP, three filter selects). backtest.py standalone script (14 symbols × 4 strategies, sliding 100-candle window, real MEXC funding history, conviction band breakdown).
Decided: Binance/Bybit geo-blocked on US IPs — fail gracefully with None, never retry. OKX uses ccy=BTC param (not instId), oiUsd field (already USD). Duplicate guard in log_signals uses 30-min window on symbol+strategy+direction. History direction filter is client-side (backend doesn't have it). Full reload after tagOutcome() so summary stats stay in sync.
Deferred: P3c AI strategy review, P3d paper trading, P3e WebSocket live refresh.
Watch out for: log_signals() must never raise — swallow all exceptions. init_db() creates data/ dir; data/ is gitignored. logged_at timestamps are UTC without Z suffix — append 'Z' in JS before passing to new Date(). OKX L/S response is [[timestamp_ms, ratio_string], ...] — access data[0][1]. The `sentiment_tracked` boolean distinguishes geo-blocked major pairs (True, all None) from untracked altcoins (False, never fetched).

### 2026-04-21 — Session summary
Built: P3b History tab (this session completed the UI — summary bar, outcome buttons, filter selects, win-rate stats). CLAUDE.md updated to reflect P3 current phase. VPS deployment to Hetzner at 62.238.15.113:8080.
Decided: History tab auto-loads on switchTab('history') — no manual refresh button needed. H state object is minimal (loading flag only) — history data is not cached in JS state, always fetched fresh. Outcome button labels abbreviated (WIN/LOSS/PAR/EXP/SKP) to fit 375px iPhone screens; full word in title attr.
Deferred: P3c AI strategy review (next task), P3d paper trading, P3e WebSocket.
Watch out for: The history table intentionally scrolls horizontally on mobile — this is correct, not a bug. Do not remove overflow-x: auto from #hist-table-wrap. Direction filter in loadHistory() is client-side — if you move it server-side, remove the client-side filter or signals will be double-filtered.

### 2026-04-23 — Session summary
Built: P3c AI strategy review (POST /api/analysis — Claude API call, last 200 tagged outcomes, min 10 required, strategy/direction breakdown, tag and symbol analysis). P3d open positions panel — full live tracking: 30s price refresh, per-position P&L, leverage-aware notional sizing, liquidation price, dollar P&L from equity curve, equity sparkline with hover tooltip (crosshair, date, balance, change), live performance banner (signals, win rate, P&L, open positions, best trade, streak), sortable columns, strategy filter, autonomous outcome checker background thread (every 15 min), evaluate_outcome() against Min15 klines (stop/TP detection with partial credit for TP1 hit before stop), daily trend direction tag.
Decided: Dollar P&L derived from equity curve so it correlates exactly with the Balance column. Equity curve uses account size input in positions header as the starting balance. Background outcome thread sleeps 1 min on startup then runs every 15 min. evaluate_outcome() uses pessimistic ambiguity rule — if TP1 was hit but stop was later hit, result is PARTIAL. 0.1% slippage applied to entry prices.
Deferred: P3e WebSocket live price refresh (lib/mexc_stream.py exists but not wired to UI).
Watch out for: fmtAge naming collision — a local function in history tab clashed with a stale global, corrupting scan timestamp display (fixed in 8037615). Equity sparkline canvas mousemove handler uses clientX/clientY offset calculations — do not simplify without testing hover accuracy. Strategy filter option values must match DB strings exactly (Balanced, Funding Arb, Momentum Breakout) — mismatch causes silent empty results.
