# Matrix Trader 7.0 — AI Handoff Document

> Paste this entire file at the start of any AI session (ChatGPT, Gemini,
> etc.) when continuing work on this project. Read every word before
> writing a single line of code. This file is manually maintained to reflect current state, not planned state.
> Update it at the end of every session before deploying.

Last updated: 2026-04-24
Last commit: b8abdb2 feat: capture exit_price in evaluate_outcome() — close price of triggering candle
app.py: 1775 lines
index.html: 3770 lines

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
├── CLAUDE.md              ← Claude Code orientation; phase status defers to HANDOFF.md
├── HANDOFF.md             ← this file; manually maintained; update every session
├── README.md              ← minimal placeholder; write properly in P4
├── .gitignore             ← covers .env, __pycache__, data/
├── .env                   ← ANTHROPIC_API_KEY, MATRIX_PORT (not committed; never touch)
├── requirements.txt       ← all deps installed; add packages here if needed
├── app.py                 ← entire Flask backend, 1694 lines; keep flat, one file
├── backtest.py            ← standalone script; do NOT import from app.py
├── templates/
│   └── index.html         ← entire frontend: HTML + CSS + JS, 3604 lines; one file
├── static/                ← directory exists; no style.css — all CSS is inline in index.html
├── docs/
│   ├── design-brief.md    ← original design doc; read-only reference
│   └── project-status.md  ← human-readable status; may be stale; HANDOFF.md is authoritative
├── .claude/
│   ├── settings.local.json ← Claude Code project permissions
│   └── commands/
│       ├── handoff.md      ← /handoff skill: regenerates HANDOFF.md from codebase
│       └── handoff_command.md ← legacy handoff command
├── data/                  ← gitignored; auto-created at runtime; never commit
│   ├── signals.db         ← SQLite signal history; auto-created by init_db()
│   └── backtest_results.json ← written by backtest.py
└── lib/                   ← pure utility functions only; no Flask, no API calls
    ├── indicators.py      ← 261 lines; complete; wired into app.py scoring
    ├── laddering.py       ← 120 lines; complete; wired into enrich_signal()
    ├── mexc_stream.py     ← 263 lines; complete; WebSocket client — not used by SSE route (P3e used poll loop)
    └── ai_client.py       ← AI provider fallback chain; call_ai() is the only public fn
```

**Touch policy:**
- `app.py` and `index.html`: always read the relevant sections before editing
- `lib/` files: pure functions only; no imports from app.py; no Flask
- `data/`: never touch directly; managed by `init_db()` and runtime writes
- `docs/`: read-only reference; never edit
- `.env`: never read, never write, never commit
- `static/`: no CSS file exists here; do not create one — CSS lives inline in index.html

**Tree update cadence:** update when a file/folder is added, deleted, or changes role (e.g. mexc_stream.py gets wired). Update line counts on >10% change. Do not update for bug fixes or content-only changes.

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
| `/api/signal/detail/<int:signal_id>` | GET | Returns full trade detail + short Claude AI coach review for a closed signal |
| `/api/outcomes/check` | POST | Evaluates all open (untagged) signals against Min15 klines; auto-tags hits |
| `/api/prices` | GET | Batch price fetch for multiple symbols — used by open positions panel |
| `/api/stream/prices` | GET | SSE stream: price updates every 3s for `?symbols=` (comma-sep); replaces 30s polling when History tab is active |
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

**DB-only columns** (not in the enrich_signal dict — set by the outcome system, never returned by the scan):

| Column | Type | Set by |
|---|---|---|
| `result` | TEXT \| NULL | `PATCH /api/signal/result` (manual) or `evaluate_outcome()` (auto) |
| `result_note` | TEXT \| NULL | `evaluate_outcome()` — describes which level was hit |
| `result_at` | TEXT \| NULL | UTC ISO timestamp of the candle where the auto-evaluated outcome occurred; manual tags use write time |
| `exit_price` | REAL \| NULL | `evaluate_outcome()` only — decisive TP/SL level for the outcome; NULL for manual tags and EXPIRED/SKIPPED |
| `entry_at` | TEXT \| NULL | `evaluate_outcome()` only — UTC ISO timestamp of the candle where entry1 was first touched |
| `signal_json` | TEXT \| NULL | Full enriched signal snapshot at scan time for later research/backtesting |
| `data_quality` | TEXT \| NULL | `current` for post-fix rows; `legacy_pre_fill_check` for pre-fix rows |
| `evaluation_version` | TEXT \| NULL | `entry_fill_v2` for corrected auto-evaluation; `pre_entry_fill_v1` for legacy outcomes |

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
  priceStream:        null,   // EventSource handle for SSE price stream (/api/stream/prices)
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

**Shared detail panel** (`#detail-panel`, `<aside>`): populated by `renderDetail(sig)` from signals or market tab. Slides in from right on desktop; slides up from bottom on mobile. Contains `#panel-resize-handle` (drag-to-resize, hidden on mobile) and `#panel-body` (all innerHTML writes target this, not the aside itself).

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
- History table: 10 columns on desktop, 5 columns on mobile (JS-conditional rendering in `renderHistoryTable()`). Clicking a closed-signal row calls `showClosedDetail(id)` to open the detail panel with trade summary + Claude coach review.
- Outcome buttons: WIN / LOSS / PAR / EXP / SKP — PATCH `/api/signal/result`, full reload after. Buttons call `event.stopPropagation()` to prevent row-click firing.
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

> Source of truth for phase status. The phase table in CLAUDE.md defers to this file.

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
| P3d+ | exit_price capture in evaluate_outcome() — close price of triggering candle | ✅ Done |
| P3d++ | Closed signal row click → detail panel (trade summary + Claude coach review) | ✅ Done |
| P3e | SSE live price refresh for open positions (History tab) | ✅ Done |
| Strategy Lab | First-class strategy keys, explainers, custom strategy configs, per-strategy tracking | 🔲 Planned |
| P4 | README, GitHub publish, 5 external beta testers | 🔲 Planned |

---

## Current Task List

Next in priority order:

1. **P3e — WebSocket live price refresh**: Wire `lib/mexc_stream.py` to the frontend for real-time price updates on open positions and watched pairs. The library exists and is complete but is not connected to any Flask route or UI. Approach: add a Server-Sent Events (SSE) endpoint in `app.py` that streams price updates; frontend subscribes on History tab entry and unsubscribes on tab leave. This replaces/augments the current 30s polling via `GET /api/prices`.

2. **Strategy Lab foundation — make strategies first-class**: Current strategies are hardcoded scoring profiles, not user-editable strategy modules. Before adding custom strategies, add stable `strategy_key` alongside the existing human `strategy` name everywhere signals are created, logged, filtered, refreshed, and analyzed. Add `GET /api/strategies` so the frontend renders strategy pills and history filters from backend metadata instead of duplicating hardcoded names in `index.html`. Then add a compact strategy explainer panel showing weights, filters, min conviction, leverage cap, intended market regime, and how the score is assembled.

3. **Strategy Lab custom configs — user-created strategies**: After `strategy_key` exists end-to-end, allow users to duplicate a prebuilt strategy, edit weights/filters/min conviction/leverage cap, run scans under that custom strategy, and track outcomes separately. Store custom strategy configs in a local persistent format (SQLite table or local JSON file) rather than app state. Keep execution manual; this is for signal research and paper-tracking, not auto-trading.

4. **P4 — Public release**: Write external-facing `README.md` with full setup instructions (currently minimal). Push to GitHub. Recruit 5 external beta testers.

---

## Strategy System Direction

This conversation is now focused on managing, analyzing, and developing the strategies Matrix Trader uses to generate signals. The desired end state: the dashboard should let users choose specific strategies, understand how each one works, track the signals and outcomes generated by each strategy, and add their own strategies to test alongside Matrix Trader's built-ins.

Current reality:
- Strategies live in `STRATEGIES` in `app.py` and are **scoring profiles**. They share the same scanner pipeline (`score_ticker()` → `enrich_signal()` → `generate_report()` → `log_signals()`).
- Built-ins are Balanced, Funding Arb, Momentum Breakout, and Mean Reversion.
- Strategy selection already works for scans via `GET /api/scan?strategy=<key>`.
- History already logs the human strategy name and can filter by strategy/result/symbol.
- Open positions and closed signals already track outcomes and can be reviewed by AI.
- `backtest.py` already iterates all strategies in `STRATEGIES`, but it mirrors only part of live enrichment and should remain a standalone live-data research script.

Important gaps before user-created strategies:
- The backend returns/stores only human display names like `"Balanced"` in signal rows. It does not persist a stable `strategy_key` like `"balanced"`.
- The frontend hardcodes strategy buttons, metadata, history filter options, and leverage fallback maps. Adding a new strategy currently requires editing multiple UI constants.
- `GET /api/signal/<symbol>` enriches with Balanced regardless of the original logged strategy. Open-position live detail refresh can therefore merge Balanced context/leverage into a non-Balanced signal.
- There is no in-app strategy explainer that shows weights, gates, scoring rules, and historical performance together.
- There is no safe user strategy schema, validation layer, persistence, or editor.

Recommended implementation order:
1. Add `strategy_key` to enriched signal dicts, SQLite logging, history serialization, closed detail responses, and analysis payloads. Keep `strategy` as the display name for backwards compatibility.
2. Add `GET /api/strategies` returning backend-owned metadata: key, name, description, risk level, weights, filters, leverage cap, min conviction, and short explainer text.
3. Update `index.html` to render scan pills, history filter options, open-position strategy filters, and leverage fallback from `/api/strategies` instead of hardcoded `STRAT_META` / `STRATEGY_LEVERAGE`.
4. Update `GET /api/signal/<symbol>` to accept a `strategy` query param and use that strategy for live context refreshes.
5. Add the strategy explainer UI before custom strategy editing. Users need to understand the built-ins before cloning/tuning them.
6. Add custom strategy persistence only after the key/metadata/explainer path is stable.

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
- Do not add new strategies by editing only one place. Until `GET /api/strategies` exists, strategy metadata is duplicated across `app.py` (`STRATEGIES`) and `index.html` (`STRAT_META`, strategy buttons, history filter options, `STRATEGY_LEVERAGE`). Keep them in sync or, preferably, complete the Strategy Lab foundation first.
- Do not refresh a logged signal's live detail with Balanced by accident. `GET /api/signal/<symbol>` currently defaults to Balanced; when adding strategy-aware refresh, pass the original `strategy_key` and preserve the original paper-trade entries/exits/stop.
- Do not set `exit_price` to 0 when unknown — use NULL so it can be distinguished from a genuine zero-price edge case. Only `evaluate_outcome()` writes `exit_price`; manual tags via `PATCH /api/signal/result` leave it NULL.
- Do not use `display: none` on `:nth-child()` td/th cells to hide columns in a `table-layout: fixed` JS-generated table — the column slot still exists in the layout algorithm and the table overflows. Use conditional JS rendering instead (skip rendering hidden cells in the template literal).
- Do not write innerHTML directly to `$('detail-panel')` — write to `$('panel-body')` only. The aside contains a sibling `#panel-resize-handle` that must not be wiped.
- Do not add a fifth innerHTML write to the detail panel without targeting `$('panel-body')`. All four existing write sites (renderDetail, enrichMarketPair ×3, showClosedDetail) already target panel-body.
- Do not mark a task complete without adding its verification items to the checklist at the bottom of this file. If the task added a new localStorage key, new route, new UI element, or new persistent behavior — it gets a checklist item.
- Do not add `onclick="showClosedDetail(...)"` to open-positions rows — that handler is only for closed (tagged) signal rows in the history table. Open positions use `showPositionDetail()`.
- Do not call any AI provider directly from routes — always use `call_ai()` from `lib/ai_client.py`. Adding a new provider means adding one entry to `PROVIDERS` and one `_call_*()` function in `lib/ai_client.py` only.
- Do not import `anthropic` at the top of `app.py` — it has been removed. The lazy import inside `_call_claude()` in `lib/ai_client.py` handles it.

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

Every task prompt assumes CLAUDE.md and HANDOFF.md have been read first.

Task prompts should NOT repeat what HANDOFF.md already covers:
- Project constraints and hard rules
- Color system and CSS variables
- Mobile behavior (desktop slides right, mobile slides up)
- State isolation rules (S, M, H never cross)
- Deploy sequence
- What NOT To Do list

Task prompts should ONLY cover:
1. What to build — specific and concrete
2. Task-specific ambiguity to check before writing (the "stop and describe" trigger)
3. Implementation order — only if sequence matters
4. Self-verification specific to this feature — code-checkable, not browser-checkable
5. HANDOFF.md update instruction

Template:
```
Task: [one sentence]
Requirements:

[specific requirements not already covered by hard rules]

Before writing:

[what to look for in the actual files that HANDOFF.md might not capture]
If [specific ambiguity found], STOP and describe — do not guess

Implementation order: [only if order matters]
Self-verify:

[each item checkable by reading the output, not opening a browser]
No new files, no routes, no backend changes [delete whichever don't apply]

Before deploying:

Add verification checklist items to HANDOFF.md for any new feature
Add a session note summarizing what was built, decided, and what to watch for
If a file or folder was added or removed, update the File Structure tree in HANDOFF.md
Deploy after self-verify and HANDOFF.md update are both complete
```

---

## Verification Checklist

> Run this checklist before reporting any task complete. Add new items here whenever
> a task introduces a new localStorage key, new route, new UI element, or new persistent
> behavior. This checklist is part of the definition of done — not an afterthought.

- [ ] `python3 -c "import app; print('OK')"` exits clean
- [ ] `GET /` returns 200
- [ ] `GET /api/scan` returns JSON with `success: true`
- [ ] `GET /api/signals/history` returns 200
- [ ] `POST /api/outcomes/check` returns 200
- [ ] `GET /api/prices?symbols=BTC_USDT` returns 200
- [ ] `GET /api/signal/detail/<id>` returns 200 for a valid signal_id from the DB
- [ ] Signal cards show entry/TP/SL in the detail panel
- [ ] Strategy pills work and update `#strat-lbl`
- [ ] History tab loads on click and shows table (or empty state)
- [ ] Open positions panel shows untagged signals with live prices
- [ ] Outcome buttons update `result` and reload the table
- [ ] Clicking a closed signal row opens the detail panel with trade summary + Coach Review section
- [ ] Coach Review shows Claude analysis text or "Analysis unavailable" gracefully
- [ ] Outcome buttons do NOT trigger row click (stopPropagation)
- [ ] Equity sparkline renders and hover tooltip shows date/balance/change
- [ ] Mobile: UI fits on 375px screen without horizontal scroll — history table included (5-column mobile layout)
- [ ] No `console.error` in browser on page load
- [ ] No Python traceback on server start
- [ ] localStorage key `mt7_filters` persists filter state across reloads
- [ ] localStorage key `mt7_guide_seen` hides the first-run guide on return visits
- [ ] localStorage key `mt7_panel_width` persists detail panel width; restored on reload
- [ ] `GET /api/stream/prices?symbols=BTC_USDT` returns `Content-Type: text/event-stream` and streams `data: {...}` events every ~3s
- [ ] History tab entry subscribes SSE and `H.priceStream` is set; leaving the tab closes the stream and sets it to null
- [ ] `H.posRefreshTimer` is null while SSE is active; restores on SSE error/close

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

> **Session notes policy:** Keep the last three sessions verbatim. After that, extract
> any new gotchas into "What NOT To Do", any new DOM structure into "Dashboard Structure",
> and delete the raw note. Knowledge graduates into the permanent sections — it does not
> accumulate here indefinitely.

### 2026-04-24 — Session summary (P3e — SSE live price refresh)
Built: `GET /api/stream/prices` SSE endpoint in `app.py` — polls `/contract/ticker` every 3s, filters to `?symbols=` param, yields one `data: {"symbol": ..., "price": ...}\n\n` SSE event per matching symbol. Uses `stream_with_context(generate())` with `GeneratorExit` guard for clean disconnects. Added `Response, stream_with_context` to Flask imports. In `index.html`: added `H.priceStream` to the H state object, added `subscribePositionStream()` (creates EventSource, first message clears `posRefreshTimer`, onerror falls back to `startPosTimers()`) and `unsubscribePositionStream()` (closes EventSource, nulls handle). Modified `startPosTimers()` to skip `posRefreshTimer` when `H.priceStream` is set. Added `subscribePositionStream()` call at end of `fetchAndRenderPositions()`. Modified `switchTab()` to call `unsubscribePositionStream()` on history tab leave.
Decided: `lib/mexc_stream.py` was not used — it's a full WebSocket client (263 lines, background threads, reconnect logic) which is overkill for SSE. The SSE endpoint reuses the existing `fetch_mexc("/contract/ticker")` poll, keeping the implementation flat and consistent with the rest of `app.py`. The 30s polling fallback (posRefreshTimer) is preserved and automatically re-activates on SSE failure.
Watch out for: `subscribePositionStream()` is called at the end of every `fetchAndRenderPositions()` run — including the 30s fallback run when SSE is broken. This is intentional: it retries SSE after each fallback cycle. If the SSE server is broken, EventSource fires onerror immediately → `unsubscribePositionStream()` + `startPosTimers()` → 30s posRefreshTimer loop continues. No tight retry loop — at worst one retry attempt every 30s. `posCounterTimer` (1s "Xs ago" counter) always starts regardless of SSE state.

### 2026-04-24 — Session summary (strategy system analysis / Strategy Lab direction)
Analyzed: Current strategies are scoring profiles, not separate engines. `STRATEGIES` in `app.py` controls weights, filters, min conviction, and leverage caps; all strategies share `score_ticker()`, `enrich_signal()`, `generate_report()`, and `log_signals()`. UI strategy selection already works for `/api/scan?strategy=<key>`, and history/outcome tracking already groups by human strategy name.
Decided: The next strategy work should make strategies first-class before adding user-created strategies. Add stable `strategy_key` end-to-end, add `GET /api/strategies`, render strategy UI from backend metadata, fix `/api/signal/<symbol>` to refresh with the original strategy, then add a strategy explainer panel. Only after that should custom strategy cloning/editing/persistence be built.
Deferred: No code changes to app behavior in this session. Custom strategy editor, persistence schema, and backtest integration remain planned work.
Watch out for: Backend currently stores display names like `"Balanced"` in `signals.strategy`, while frontend scan state uses keys like `"balanced"`. This name/key split is the central risk for custom strategies. Also, open-position live refresh calls `/api/signal/<symbol>` without a strategy param today, so it can silently use Balanced context for a non-Balanced logged signal.

### 2026-04-23 — Session summary (mobile history table, v2 — JS fix)
Built: History table truly fits 375px with zero horizontal scroll. Previous CSS-only fix was replaced — `display: none` on `:nth-child()` cells is unreliable with `table-layout: fixed` on JS-generated tables; hidden column slots still exist in the layout algorithm. Fix: `renderHistoryTable()` now checks `window.innerWidth < 768` (`isMobile`) and conditionally skips rendering Strategy/Conv/Why/R/Balance cells entirely. CSS changed from `display: none` rules to clean 5-column widths (Time=52px, Symbol=60px, Dir=44px, Result=auto, $P&L=70px). Time format shortened on mobile to "Apr 23" (no hours/minutes).
Decided: JS-conditional rendering is the correct approach when CSS alone can't be relied on for fixed-layout tables. Columns not in the DOM = no layout slot, no overflow.
Deferred: nothing.
Watch out for: `isMobile` is evaluated once at render time. If user rotates device or resizes browser without refreshing, the table won't rerender. This is acceptable — `loadHistory()` is called on every tab switch so re-entry fixes it. Do not cache `isMobile` as a module-level constant — it must be evaluated fresh inside `renderHistoryTable()` each call.

### 2026-04-23 — Session summary (exit_price capture)
Built: `exit_price REAL DEFAULT NULL` column added to signals table via `ALTER TABLE` migration in `init_db()` (wrapped in try/except OperationalError for idempotency). Later accuracy pass changed `evaluate_outcome()` to return `(result, note, exit_price, result_at, entry_at)` and write both `exit_price` and `entry_at`.
Decided: exit_price = decisive TP/SL level for the outcome, not the candle close. NULL for EXPIRED, SKIPPED, and any manually tagged result.
Deferred: displaying exit_price in the history tab UI.
Watch out for: `init_db()` is only called under `__main__` — calling it via `import app` in a script requires `app.init_db()` explicitly. The ALTER TABLE is idempotent; running twice is safe. `evaluate_outcome()` return type is now `tuple[str, str, float | None, str | None, str | None] | None` — any future caller that unpacks as `result, note = outcome` will raise a ValueError.

### 2026-04-24 — Session summary (paper trading accuracy pass)
Built: `evaluate_outcome()` now treats logged signals as pending ladder trades instead of immediately filled positions. It waits for `entry1` to be touched before a stop or TP can count, parses naive stored UTC timestamps as UTC instead of local machine time, records the candle timestamp for `result_at`, and writes `entry_at`. `exit_price` now stores the decisive TP/SL level instead of the triggering candle close. Added idempotent migrations for `entry_at` and `signal_json`; `log_signals()` stores the full enriched signal dict as JSON so future strategy analysis has access to the complete scan-time context.
Decided: Existing historical rows are labelled with `data_quality='legacy_pre_fill_check'` and `evaluation_version='pre_entry_fill_v1'`. They may contain pre-fix outcomes and NULL `exit_price`/`entry_at`; re-evaluate/backfill deliberately before using them for serious research. Future logged rows default to `data_quality='current'`, and corrected auto-evaluated outcomes use `evaluation_version='entry_fill_v2'`.
Watch out for: `evaluate_outcome()` return type is now `tuple[str, str, float | None, str | None, str | None] | None`.

### 2026-04-23 — Session summary (AI provider fallback chain)
Built: `lib/ai_client.py` — provider fallback chain with `call_ai(system, user, max_tokens)` as the single public function. Provider order: Claude → Gemini → DeepSeek → Groq. Each `_call_*()` function lazy-imports its SDK (so missing packages only fail at call time, not module import). `app.py` top-level `import anthropic` removed; `from lib.ai_client import call_ai` added. Both `api_signal_detail()` and `api_analysis()` now call `call_ai()`. `api_analysis()` no longer gates on `ANTHROPIC_API_KEY` specifically — `call_ai()` tries all configured providers. `requirements.txt` updated with `google-generativeai>=0.8.0`, `openai>=1.0.0`, `groq>=0.9.0`.
Decided: Lazy imports per provider (not at module level) so importing `lib/ai_client` never fails even if SDK packages are missing. `_DISPATCH` dict maps provider names to functions to keep `call_ai()` clean. `api_analysis` max_tokens=2048 (was 2000 — rounded up to match task spec).
Deferred: nothing.
Watch out for: `call_ai()` returns `None` if all providers fail or none have keys — callers must handle `None` gracefully (detail route sets `ai_analysis=None`, analysis route returns 400). Do not add provider logic directly to routes; always add to `lib/ai_client.py`. The `anthropic` package is still in `requirements.txt` — it is needed by `_call_claude()` at runtime.

### 2026-04-23 — Session summary (closed signal detail panel)
Built: `GET /api/signal/detail/<int:signal_id>` — returns full trade summary (entry, exit_price, stop, TPs, result, pnl_pct, duration_minutes, signal_why, tags, volatility) plus a short Claude AI coach review (`max_tokens=512`, system="You are a trading coach..."). Coach review gracefully returns null on failure. `showClosedDetail(id)` function in index.html — opens `#detail-panel` using the same desktop/mobile split as `enrichMarketPair` (desktop: remove `hidden`; mobile: add `open` + overlay + overflow hidden). Renders `.d-inner` with `.d-close` button, `.d-sym-row`, `.d-badges`, `.ctx-grid` (8 cells: Strategy, Conviction, Entry, Exit, Stop, TP1, P&L, Duration), signal_why italic, result_note, and Coach Review section. Closed signal `<tr>` rows now have `onclick="showClosedDetail(${s.id})"` and `cursor:pointer`. Outcome buttons call `event.stopPropagation()` to prevent row-click firing.
Decided: Route uses `<int:signal_id>` (integer) so Flask resolves `/api/signal/detail/123` before `/api/signal/<symbol>` — no routing conflict. Coach review uses `max_tokens=512` (short, fast). All innerHTML for the panel targets `$('panel-body')`, not `$('detail-panel')`.
Deferred: nothing.
Watch out for: The `showClosedDetail` panel opener uses `$('overlay')` not `$('panel-overlay')` — the overlay element ID is `overlay`. Existing `closePanel()` function handles cleanup (removes `open` class, clears overflow, clears `$('overlay').classList`). Do NOT add `showClosedDetail` to open-positions rows — those use `showPositionDetail()`.
