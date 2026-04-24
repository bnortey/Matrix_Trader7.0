# Matrix Trader 7.0 — AI Handoff Document

> Paste this entire file at the start of any AI session (ChatGPT, Gemini,
> etc.) when continuing work on this project. Read every word before
> writing a single line of code. This file was auto-generated from the
> actual codebase — it reflects current state, not planned state.
> Update it at the end of every session before deploying.

Last updated: 2026-04-24
Last commit: a6ec8f2 feat: Strategy Lab foundation + explainer + custom, kline gate, leveraged P&L, blended PARTIAL, historical backfill — April 24
app.py: 2498 lines
index.html: 4173 lines

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
├── HANDOFF.md             ← this file; update every session
├── README.md              ← minimal placeholder; write properly in P4
├── SERVER_GUIDE.md        ← VPS access, deploy, and service management cheat sheet
├── .gitignore             ← covers .env, __pycache__, data/
├── .env                   ← ANTHROPIC_API_KEY, MATRIX_PORT (not committed; never touch)
├── requirements.txt       ← all deps installed; add packages here if needed
├── app.py                 ← entire Flask backend, 2486 lines; keep flat, one file
├── backtest.py            ← standalone script; do NOT import from app.py
├── templates/
│   └── index.html         ← entire frontend: HTML + CSS + JS, 4119 lines; one file
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
    ├── indicators.py      ← RSI, EMA, VWAP, ATR
    ├── laddering.py       ← generate_ladders(price, atr, tiers, direction)
    ├── mexc_stream.py     ← WebSocket client — not used by SSE route (P3e used poll loop)
    └── ai_client.py       ← AI provider fallback chain; call_ai() is the only public fn
```

**Touch policy:**
- `app.py` and `index.html`: always read the relevant sections before editing
- `lib/` files: pure functions only; no imports from app.py; no Flask
- `data/`: never touch directly; managed by `init_db()` and runtime writes
- `docs/`: read-only reference; never edit
- `.env`: never read, never write, never commit
- `static/`: no CSS file exists here; do not create one — CSS lives inline in index.html

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
google-generativeai>=0.8.0
openai>=1.0.0
groq>=0.9.0
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
| `/api/signal/result` | PATCH | Tags a logged signal with WIN/LOSS/PARTIAL/EXPIRED/SKIPPED; accepts optional `exit_price` and `entry_price` to compute and persist `pnl_pct` |
| `/api/signals/history` | GET | Returns logged signal history; filters: strategy, result, symbol, limit |
| `/api/signal/detail/<int:signal_id>` | GET | Returns full trade detail + short Claude AI coach review for a closed signal |
| `/api/outcomes/check` | POST | Evaluates all open (untagged) signals against Min15 klines; auto-tags hits; also runs expire_stale_signals() |
| `/api/prices` | GET | Batch price fetch for multiple symbols — used by open positions panel |
| `/api/stream/prices` | GET | SSE stream: price updates every 3s for `?symbols=` (comma-sep) |
| `/api/strategies` | GET | Returns built-in + enabled custom strategy configs; `?include_disabled=1` includes disabled custom strategies; `performance` object includes `avg_win_pnl` and `avg_loss_pnl` per strategy |
| `/api/strategies/custom` | POST | Creates a custom strategy from a built-in base with validated weights/filters/risk settings |
| `/api/strategies/custom/<strategy_key>` | PATCH | Updates a custom strategy; supports enable/disable and config edits |
| `/api/strategies/custom/<strategy_key>` | DELETE | Deletes a custom strategy definition; historical signals remain intact |
| `/api/analysis` | POST | AI strategy review: sends last 200 tagged outcomes to Claude API |
| `/api/backfill/pnl` | POST | **MAINTENANCE** — Re-evaluates historical signals (result NOT NULL, pnl_pct NULL) against live kline data; writes corrected exit_price, blended PARTIAL, and leveraged pnl_pct. Safe to call repeatedly (skips already-backfilled rows). |

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

  # Market sentiment (from OKX public APIs; Binance/Bybit geo-blocked)
  "binance_ls_long_pct":  float | None,
  "binance_oi":           float | None,
  "bybit_oi":             float | None,
  "okx_ls_long_pct":      float | None,
  "okx_oi":               float | None,
  "sentiment_tracked":    bool,   # False for MEXC-only altcoins, True for major pairs

  # Strategy context
  "strategy_key":         str,    # stable key e.g. "balanced"
  "strategy_is_custom":   bool,
  "strategy_config":      dict,   # weights, filters, min_conviction, leverage_cap, base_key

  # Kline history depth (measured at enrichment time)
  "kline_depth_1h":       int,    # number of 1h candles available
  "kline_depth_4h":       int,    # number of 4h candles available
  "data_quality":         str,    # "low" if thin history; "current" otherwise
}
```

**DB-only columns** (not in the enrich_signal dict — set by the outcome system):

| Column | Type | Set by |
|---|---|---|
| `result` | TEXT \| NULL | `PATCH /api/signal/result` (manual) or `evaluate_outcome()` (auto) |
| `result_note` | TEXT \| NULL | `evaluate_outcome()` — describes which level was hit |
| `result_at` | TEXT \| NULL | UTC ISO timestamp of outcome candle; manual tags use write time |
| `exit_price` | REAL \| NULL | `evaluate_outcome()` — decisive TP/SL level (blended for PARTIAL); NULL for EXPIRED/SKIPPED and manual tags without exit_price |
| `entry_at` | TEXT \| NULL | `evaluate_outcome()` — UTC ISO timestamp of candle where entry1 was first touched |
| `signal_json` | TEXT \| NULL | Full enriched signal snapshot at scan time |
| `data_quality` | TEXT \| NULL | `current` for standard rows; `low` for thin-history signals; `legacy_pre_fill_check` for pre-fix rows |
| `evaluation_version` | TEXT \| NULL | `entry_fill_v2` for auto-evaluation; `backfill_v1` for historical re-evaluation; `pre_entry_fill_v1` for legacy |
| `strategy_key` | TEXT | Stable strategy key e.g. `balanced`; backfilled from display name for old rows |
| `pnl_pct` | REAL \| NULL | Leveraged P&L % (raw_pct × leverage); written by `evaluate_outcome()` and PATCH with exit_price; NULL for EXPIRED/SKIPPED |
| `leverage` | REAL \| NULL | Strategy leverage cap at signal time; written by `log_signals()`; backfilled from `signal_json.leverage_cap` for old rows |

`ai_report` is a JSON string: `[{"label": "Setup", "text": "..."}, ...]`. Four sections: Setup, Structure, Invalidation, Risk.

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
  explainerOpen: false, // true when #strategy-explainer is visible
  editingStrategy: null, // strategy key currently loaded in custom editor
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
  loading:         false,
  openPositions:   [],
  priceCache:      {},
  posRefreshTimer: null,
  posCounterTimer: null,
  posLastRefresh:  null,
  posFetching:        false,
  selectedPositionId: null,
  posSort:            'age',
  posSortDir:         'asc',
  posStratFilter:     '',
  posSymbolFilter:    '',
  closedAll:          [],
  closedSort:         'logged_at',
  closedSortDir:      'desc',
  priceStream:        null,
  strategies:         null,
};

let lastHistorySigs = [];

const STRATEGY_LEVERAGE = { balanced: 20, funding_arb: 10, momentum_breakout: 25, mean_reversion: 15 };
```

`S` and `M` are completely isolated. Never cross-reference them.
`H` manages history tab state — open positions list, price cache, timer handles, sort/filter state, closed signals.

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
- `#open-positions-section` — live open positions (untagged signals) with P&L tracking, SSE price stream
- `#closed-signals-section` — tagged signals with outcome buttons, equity curve, strategy review

**Shared detail panel** (`#detail-panel`, `<aside>`): populated by `renderDetail(sig)` from signals or market tab. Slides in from right on desktop; slides up from bottom on mobile. Contains `#panel-resize-handle` (drag-to-resize, hidden on mobile) and `#panel-body` (all innerHTML writes target this, not the aside itself).

**Strategy bar** (inside `#signals-section`): pills rendered dynamically from `/api/strategies`. Custom strategies get a blue-tinted border (`.strat-btn.custom`). `setStrategy(key, fromUser)` — pass `fromUser=true` only from user click handlers.

**Strategy explainer** (`#strategy-explainer`, below `#strategy-bar`): inline panel hidden by default. Shows regime badge, custom badge if applicable, description, weight bars, gates, parameters, performance stats, and strategy actions (Clone / Edit / Disable / Delete). Populated by `populateExplainer(strat)`. Toggle controlled by `S.explainerOpen`.

**Strategy editor** (`#strategy-editor`, inside `#strategy-explainer`): compact inline form for clone/edit. Uses `POST /api/strategies/custom`, `PATCH /api/strategies/custom/<key>`, `DELETE /api/strategies/custom/<key>`.

**Filter bar** (inside `#signals-section`): direction toggle, volatility filter select, min-volume input. State persisted to localStorage key `mt7_filters`.

**Open positions panel** (`#open-positions-section`):
- `#open-positions-header` — account size input, live performance banner
- `#open-positions-body` — sortable table with live P&L via SSE stream
- Click a row → `showPositionDetail()` — live P&L status bar, TP progress

**Closed signals panel** (`#closed-signals-section`):
- Summary stats bar: Logged · Tagged · Win Rate · W/L/P counts · Avg Conviction
- Equity curve: sparkline with crosshair hover tooltip
- History table: 10 columns desktop / 5 columns mobile (JS-conditional rendering)
- Outcome buttons: WIN / LOSS / PAR / EXP / SKP — PATCH `/api/signal/result`
- Click a closed-signal row → `showClosedDetail(id)` — trade summary + Claude coach review

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
| P3d | Open positions panel — live P&L, SSE price stream, auto-tagging, equity curve | ✅ Done |
| P3d+ | exit_price capture, closed signal detail panel, coach review | ✅ Done |
| P3e | SSE live price refresh for open positions | ✅ Done |
| Strategy Lab | strategy_key end-to-end, /api/strategies, dynamic UI, explainer, custom CRUD | ✅ Done |
| Paper trading data integrity | pnl_pct + leverage columns, blended PARTIAL, leveraged pnl, auto-EXPIRED, historical backfill | ✅ Done |
| Kline depth gate | enrich_signal() gates pairs with < 50 1h / < 20 4h candles; kline_depth fields | ✅ Done |
| P4 | README updated and published to GitHub | ✅ Done (beta testers TBD) |

---

## Current Task List

Next in priority order:

1. **Beta tester recruitment**: Share `https://github.com/bnortey/Matrix_Trader7.0` with 5 external testers. Collect feedback on signal quality, UI clarity on mobile, and paper trading workflow.

2. **Strategy Lab mobile/user QA pass**: Verify clone/edit/save/disable/delete on desktop and iPhone Safari. Pay close attention to inline editor height, keyboard behavior, and strategy bar overflow when custom strategies are added.

2. **Strategy analytics / comparison layer**: Compact analytics comparing built-ins and custom strategies: total signals, open/closed counts, win rate, average leveraged P&L (now available via `pnl_pct`), best/worst symbols, performance by volatility/regime. Build on the existing `/api/strategies` performance object.

3. **P4 — Public release**: Write external-facing `README.md` with full setup instructions. Include local run and VPS deploy/restart notes. Push to GitHub. Recruit 5 external beta testers.

---

## Strategy System Direction

Strategies are scoring profiles in `STRATEGIES` in `app.py`. They share the same scanner pipeline (`score_ticker()` → `enrich_signal()` → `generate_report()` → `log_signals()`). Built-ins: Balanced, Funding Arb, Momentum Breakout, Mean Reversion. Custom strategies persist in SQLite `custom_strategies` table with stable keys.

Current state:
- Strategy selection works for scans via `GET /api/scan?strategy=<key>`
- History logs strategy_key and can filter by strategy/result/symbol
- Open positions and closed signals track outcomes with leveraged pnl_pct
- Custom strategy CRUD is fully wired (POST/PATCH/DELETE, scan support, UI editor)
- `backtest.py` iterates all strategies but remains a standalone live-data script

Recommended next step: strategy analytics/comparison dashboard using the now-accurate pnl_pct data.

---

## What NOT To Do

- Do not call `enrich_signal()` from `backtest.py` — it makes live API calls.
- Do not import from `app.py` in a way that triggers Flask server startup.
- Do not add new columns to the `signals` SQLite table without a migration — wrap in `try/except OperationalError`.
- Do not use `datetime.now()` — always use `datetime.utcnow()`. All timestamps are UTC ISO without Z suffix.
- Do not use `con.row_factory = sqlite3.Row` in write paths — only needed for SELECT + `dict(r)` serialization.
- Do not add JS frameworks. No React, Vue, jQuery, Alpine, or similar.
- Do not add glassmorphism, gradients, or drop shadows to the UI.
- Do not commit `.env`, `data/`, or `__pycache__/`.
- Do not modify `S` state from market tab code or `M` state from signals tab code.
- Do not run the full backtest during CI or import-time.
- Do not filter direction server-side in `/api/signals/history` — direction is filtered client-side.
- Do not use `title` attributes on mobile-only UI elements — native tooltips don't appear on touch.
- Do not rename `fmtAge` — a naming collision caused corrupted scan timestamps (fixed in 8037615).
- Do not touch the equity sparkline's mousemove/crosshair logic without testing hover on mobile.
- Do not change strategy filter option values in the history tab — they must match DB strings exactly: "Balanced", "Funding Arb", "Momentum Breakout".
- Do not add new strategies by editing only one place. Metadata spans: `STRATEGIES` and `_STRATEGY_NAME_TO_KEY` in `app.py`; `STRAT_META` and `STRATEGY_LEVERAGE` in `index.html`.
- Do not refresh a logged signal's live detail with Balanced by accident — `GET /api/signal/<symbol>` resolves strategy from DB.
- Do not set `exit_price` to 0 when unknown — use NULL. Only `evaluate_outcome()` and PATCH with exit_price write this field.
- Do not use `display: none` on `:nth-child()` td/th cells to hide columns in `table-layout: fixed` JS-generated tables — use conditional JS rendering instead.
- Do not write innerHTML directly to `$('detail-panel')` — write to `$('panel-body')` only.
- Do not add `onclick="showClosedDetail(...)"` to open-positions rows — those use `showPositionDetail()`.
- Do not call any AI provider directly from routes — always use `call_ai()` from `lib/ai_client.py`.
- Do not import `anthropic` at the top of `app.py` — the lazy import inside `_call_claude()` in `lib/ai_client.py` handles it.
- Do not use `esc()` for general JS string escaping — it is HTML escaping only. If future strategy keys allow quotes or punctuation, switch onclick wiring to event listeners.
- Do not call `POST /api/backfill/pnl` from a browser tab that may time out — use `curl -X POST` from the VPS shell. The route makes one MEXC API call per signal (~0.1s each).

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

## VPS Deploy / Restart

The live VPS app runs from `/opt/matrix-trader/` on `root@62.238.15.113`. After local edits, sync code and restart:

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0
rsync -avz --exclude='.env' --exclude='data/' --exclude='__pycache__' --exclude='.git' --exclude='*.pyc' ./ root@62.238.15.113:/opt/matrix-trader/
ssh root@62.238.15.113
systemctl restart matrix-trader
systemctl status matrix-trader --no-pager
```

Force-kill fallback if restart hangs:
```bash
pkill -9 python3 2>/dev/null && sleep 3 && pkill -9 python3 2>/dev/null && sleep 2 && systemctl start matrix-trader && sleep 8
ss -tulnp | grep python
```

Post-deploy smoke checks:
```bash
curl -s http://localhost:8080/ | grep "loadStrategies"
curl -s http://localhost:8080/api/strategies
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
4. Self-verification specific to this feature
5. HANDOFF.md update instruction

---

## Verification Checklist

> Run before reporting any task complete.

- [ ] `python3 -c "import app; print('OK')"` exits clean
- [ ] `GET /` returns 200
- [ ] `GET /api/scan` returns JSON with `success: true`
- [ ] `GET /api/signals/history` returns 200
- [ ] `POST /api/outcomes/check` returns 200
- [ ] `GET /api/prices?symbols=BTC_USDT` returns 200
- [ ] `GET /api/signal/detail/<id>` returns 200 for a valid signal_id
- [ ] Signal cards show entry/TP/SL in the detail panel
- [ ] Strategy pills work and update `#strat-lbl`
- [ ] History tab loads on click and shows table (or empty state)
- [ ] Open positions panel shows untagged signals with live prices
- [ ] Outcome buttons update `result` and reload the table
- [ ] Clicking a closed signal row opens the detail panel with trade summary + Coach Review
- [ ] Coach Review shows Claude analysis text or "Analysis unavailable" gracefully
- [ ] Outcome buttons do NOT trigger row click (stopPropagation)
- [ ] Equity sparkline renders and hover tooltip shows date/balance/change
- [ ] Mobile: UI fits on 375px screen without horizontal scroll
- [ ] No `console.error` in browser on page load
- [ ] No Python traceback on server start
- [ ] localStorage key `mt7_filters` persists filter state across reloads
- [ ] localStorage key `mt7_guide_seen` hides the first-run guide on return visits
- [ ] localStorage key `mt7_panel_width` persists detail panel width
- [ ] `/api/strategies` response includes `performance` object per strategy with `avg_win_pnl` and `avg_loss_pnl`
- [ ] History table shows sortable "P&L %" column (desktop) with colored leveraged pnl_pct values
- [ ] Null pnl_pct shows as "—" in muted color in history table
- [ ] "Avg Win P&L" and "Avg Loss P&L" stat boxes appear above history table
- [ ] Strategy explainer performance grid shows 6 cells including avg win/loss P&L
- [ ] Clicking a strategy button shows `#strategy-explainer` with correct data
- [ ] Clicking the same strategy button again hides the explainer
- [ ] `signals` table has `pnl_pct` and `leverage` columns after `init_db()` runs
- [ ] `leverage` backfilled from `signal_json.leverage_cap` for existing rows on startup
- [ ] New scans write `leverage` from `sig.get("leverage_cap")` at log time
- [ ] PARTIAL `exit_price` with stop hit is a blended price, not just tp1
- [ ] WIN/LOSS/PARTIAL write leveraged `pnl_pct` to DB at evaluation time
- [ ] `PATCH /api/signal/result` with `exit_price` in body persists `exit_price` and `pnl_pct`
- [ ] Signals older than 80h get auto-tagged `EXPIRED` on next scan or outcome check
- [ ] `pnl_pct` appears in `/api/signals/history` response
- [ ] `/api/signal/detail/<id>` prefers persisted `pnl_pct`; falls back to on-the-fly for old rows
- [ ] AI review prompt includes `avg pnl` per strategy and `pnl:%` per signal line
- [ ] Pairs with < 50 1h candles are skipped before enrichment
- [ ] Pairs with < 20 4h candles are skipped before enrichment
- [ ] Skipped pairs log a stderr line: `[kline gate] {symbol} skipped — 1h:{n} 4h:{n}`
- [ ] Signals with 50–99 1h candles get `data_quality = "low"`
- [ ] `kline_depth_1h` and `kline_depth_4h` appear in `/api/signal/<symbol>` response
- [ ] `POST /api/backfill/pnl` returns `{"success": true, "updated": N, "skipped": N, "errors": N}`
- [ ] Backfilled rows have `evaluation_version = 'backfill_v1'`
- [ ] Custom strategy clone/edit/save/disable/delete works on desktop
- [ ] `GET /api/stream/prices?symbols=BTC_USDT` returns `Content-Type: text/event-stream`
- [ ] History tab SSE subscribes on entry and closes on tab leave
- [ ] Closed signal detail shows est. notional P&L when account_size is set
- [ ] Closed signal detail shows nothing for notional when account_size is 0 or unset
- [ ] Open position detail shows live notional P&L (est. notional) updating with SSE / 30s poll
- [ ] Open positions table has symbol search input that filters rows in real time
- [ ] Open positions count shows X/Y when filters are active

---

## Returning to Claude

Start every Claude Code session with:

```
Read CLAUDE.md and HANDOFF.md before touching anything.
[Your task here]
```

---

## Session Notes

> Keep the last three sessions verbatim. After that, extract gotchas into
> "What NOT To Do", DOM structure into "Dashboard Structure", and delete
> the raw note. Knowledge graduates into permanent sections.

### 2026-04-24 — Session summary (notional P&L + position symbol search)
Built: Three UI improvements to position panels. (1) Closed signal detail panel: computes `est. notional = acct × 0.01 × (pnl_pct / 100)` client-side and shows it as a small colored sub-line inside the P&L ctx-item; hidden when account_size is 0 or pnl_pct is null. (2) Open position detail panel (`buildStatusBarHTML`): same formula using live `pnl` %; shown as a small colored line below the big `24px` P&L % number; updates dynamically. SSE onmessage handler now also updates `#pos-status-bar` via `outerHTML` replacement whenever the selected position's symbol gets a price tick — so the notional and P&L % stay live at 3s cadence. (3) Open positions table: added `posSymbolFilter` to H state; symbol search `<input id="pos-symbol-filter">` added to `#open-positions-header` (matches `.hist-filter-input` class); `renderOpenPositions()` filters by uppercased substring; count shows `X/Y` when either strategy or symbol filter is active.
Watch out for: `outerHTML` replacement of `#pos-status-bar` removes the old element from DOM and inserts the new one. If other code saves a reference to the element (not current practice), it would become stale. Do not cache `$('pos-status-bar')` across ticks.

### 2026-04-24 — Session summary (pnl_pct UI surface)
Built: Surfaced `pnl_pct` (leveraged P&L %) in three UI locations. (1) History table: sortable "P&L %" column added after Result (desktop only), colored green/red/amber from DB value; sort key `pnl_pct` added to `closedSortValue()`. (2) History summary bar: two new stat boxes "Avg Win P&L" and "Avg Loss P&L" computed client-side from `H.closedAll`. (3) Strategy explainer performance grid: two new cells "Avg win P&L" / "Avg loss P&L" from `performance.avg_win_pnl` / `performance.avg_loss_pnl`; grid expanded from 4→6 cols (mobile: 2→3 cols). Backend: `api_strategies()` now queries `AVG(pnl_pct)` for WIN and LOSS per strategy, returns as `avg_win_pnl` and `avg_loss_pnl` in the `performance` object.
Watch out for: P&L % column is desktop-only (hidden on mobile) to preserve the 5-column mobile layout. The `fmtPct(val, 1)` helper is used for consistent `+X.X%`/`-X.X%`/`—` formatting.

### 2026-04-24 — Session summary (pnl_pct historical backfill)
Built: `POST /api/backfill/pnl` maintenance route. Queries signals where `result IS NOT NULL AND pnl_pct IS NULL AND result NOT IN ('EXPIRED','SKIPPED') AND entry1 IS NOT NULL`. Re-runs `evaluate_outcome()` per signal (live MEXC klines), computes leveraged `pnl_pct` via `_compute_leveraged_pnl()`, writes corrected `exit_price` (blended PARTIAL), `pnl_pct`, `entry_at`, `result_note`, `evaluation_version='backfill_v1'`. 0.1s sleep between each signal. Per-signal errors caught without aborting the run. Initial filter used `exit_price IS NOT NULL` which missed 228 pre-exit_price-column rows; fixed to `result NOT IN ('EXPIRED','SKIPPED')` in a follow-up commit. Final result: 298 of 313 historical signals backfilled; 15 skipped (klines aged out of MEXC 75h window, all from 2026-04-21).
Decided: Route is POST only. Placed in a dedicated "Maintenance routes" section. Does not modify `data_quality` — that reflects scan-time quality, not re-evaluation quality.
Watch out for: Run backfill via `curl -X POST http://localhost:8080/api/backfill/pnl` from VPS shell, not browser (313 signals × 0.1s = ~31s minimum, browser tabs may time out).

### 2026-04-24 — Session summary (paper trading data integrity sprint)
Built: Seven fixes to the paper trading data model. (1) `pnl_pct REAL` and `leverage REAL` columns added via idempotent ALTER TABLE in `init_db()`; leverage backfilled with `json_extract(signal_json, '$.leverage_cap')`. (2) `log_signals()` writes `leverage_cap` at insert time. (3) PARTIAL blended exit price in `evaluate_outcome()`: TP1-then-stopped = `(tp1/3 + stop×2/3)`; TP2-then-stopped = `(tp1/3 + tp2/3 + stop/3)`. (4) `_compute_leveraged_pnl(sig, exit_price)` helper; `api_outcomes_check()` persists `pnl_pct` in the same UPDATE. (5) `PATCH /api/signal/result` accepts `exit_price` and `entry_price`; computes and persists `pnl_pct`. (6) `expire_stale_signals()` tags signals >80h old as EXPIRED; called at top of `run_scan()` and `api_outcomes_check()`. (7) `api_signal_detail()` prefers persisted `pnl_pct`; `api_analysis()` adds avg pnl per strategy and pnl per signal line.
Decided: PARTIAL blended uses equal thirds. Blending only applies to stop_hit PARTIAL; still-open PARTIAL keeps exit_price = last TP hit.
Watch out for: `_compute_leveraged_pnl` defaults leverage to 1.0 if column is NULL and signal_json has no leverage_cap — old test signals show unleveraged P&L, not zero.

### 2026-04-24 — Session summary (kline depth gate)
Built: Minimum kline history gate in `enrich_signal()`. After building the 1h DataFrame, fetches `Hour4` klines (limit=50) to count `n4h`. If `n1h < 50` OR `n4h < 20`, prints `[kline gate] {symbol} skipped — 1h:{n} 4h:{n}` to stderr and returns None. Signals that pass the gate get `kline_depth_1h`, `kline_depth_4h`, and `data_quality` ("low" if n1h < 100 or n4h < 50, else "current") in their dict. `log_signals()` reads `sig.get("data_quality") or "current"` instead of hardcoding. Both depth fields auto-appear in `/api/signal/<symbol>` response.
Decided: Thresholds: 50/20 for hard gate; 100/50 for "low" quality flag. 4h fetch is count-only, not used for indicators. Existing `len(df) < 16` check is now dead code (superseded by the 50 threshold) but left in place as a harmless early guard.
Watch out for: Extra MEXC API call per enriched symbol (top 30 only). If scan times increase, consider caching this depth check.
