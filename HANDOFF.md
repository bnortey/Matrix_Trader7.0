# Matrix Trader 7.0 — AI Handoff Document

> Paste this entire file at the start of any AI session (ChatGPT, Gemini,
> etc.) when continuing work on this project. Read every word before
> writing a single line of code. This file was auto-generated from the
> actual codebase — it reflects current state, not planned state.
> Update it at the end of every session before deploying.

Last updated: 2026-05-03
Last commit: 92514c2 feat: enrich_signal() routes klines/depth/daily to HL client when exchange=HYPERLIQUID
app.py: 4275 lines
index.html: 5755 lines

---

## What This Project Is

Matrix Trader 7.0 is a local web application for high-leverage crypto trading on MEXC perpetual swap markets. A Python Flask backend serves a single-file dark-theme dashboard. The user scans 800+ MEXC perp tickers, receives ranked LONG/SHORT signals with entry/TP/SL ladders derived from ATR, views a 4-section AI trade brief, and executes trades manually. Signal history is auto-logged to SQLite on every scan. It is not yet an execution bot — order placement is a staged future capability (P8–P12), currently disabled. It is not a price forecasting engine, and not a SaaS product.

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

## Execution Safety Rules

Immutable. Cannot be softened by any future session prompt or task description.

1. Live trading is disabled by default. LIVE_TRADING_ENABLED=false in .env is the master gate.
2. Paper simulation (P10) must run successfully before assisted live (P11) begins.
3. User confirmation required before every order in assisted mode — no silent placement.
4. Kill switch must be implemented and tested before P11 ships.
5. No automatic leverage escalation under any condition.
6. No averaging down.
7. No blind retry loops on failed order placement.
8. No execution on stale signal data (signal age > 5 minutes at order time).

---

## File Structure

```
Matrix_Trader_7.0/
├── CLAUDE.md              ← Claude Code orientation; phase status defers to HANDOFF.md
├── AGENTS.md              ← Codex orientation (mirrors CLAUDE.md); keep in sync
├── HANDOFF.md             ← this file; update every session
├── README.md              ← public-facing setup guide
├── STRATEGIES.md          ← user-facing strategy guide: scoring, paper trading, analytics, bot-readiness
├── SERVER_GUIDE.md        ← VPS access, deploy, and service management cheat sheet
├── .gitignore             ← covers .env, __pycache__, data/, *.db
├── .env                   ← secrets/config only: ANTHROPIC_API_KEY, MATRIX_PORT, optional COINGLASS_API_KEY, HL_WALLET_ADDRESS
├── requirements.txt       ← all deps installed; add packages here if needed
├── app.py                 ← entire Flask backend; keep flat, one file
├── backtest.py            ← standalone script; do NOT import from app.py
├── templates/
│   └── index.html         ← entire frontend: HTML + CSS + JS; one file
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
    ├── coinglass_client.py ← optional CoinGlass V4 derivatives context client
    ├── hyperliquid_client.py ← Hyperliquid public scan + read-only account client
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

## Environment Variables

```
ANTHROPIC_API_KEY=        # AI trade briefs / coach reviews
MATRIX_PORT=8080          # optional app port
MEXC_API_KEY=             # optional MEXC private account status
MEXC_API_SECRET=          # optional MEXC private account status
COINGLASS_API_KEY=        # optional derivatives context
HL_WALLET_ADDRESS=        # optional Hyperliquid read-only account status, 0x... wallet
```

Hyperliquid scans do not need API keys or a wallet address. `HL_WALLET_ADDRESS` is only for `/api/hl/account`.

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
| `/api/scan/all` | POST | Fetches tickers once, runs all enabled strategies, logs per-strategy signals, returns `{results: {strategy_key: {signals, total_pairs, strategy}}, total_pairs, scan_time}` |
| `/api/hl/scan` | GET | Hyperliquid scan: fetches `metaAndAssetCtxs`, normalizes tickers, runs the selected existing strategy, logs HL signals |
| `/api/market` | GET | Returns scored market-browser tickers for `?exchange=mexc` or `?exchange=hyperliquid`; includes optional CoinGlass metadata when available |
| `/api/signal/<symbol>` | GET | Fully enriches a single symbol on demand, including optional CoinGlass OI/funding/liquidation context |
| `/api/signal/result` | PATCH | Tags a logged signal with WIN/LOSS/PARTIAL/EXPIRED/SKIPPED; accepts optional `exit_price` and `entry_price` to compute and persist `pnl_pct` |
| `/api/signals/history` | GET | Returns logged signal history; filters: strategy, result, symbol, limit |
| `/api/signal/detail/<int:signal_id>` | GET | Returns full trade detail + short Claude AI coach review for a closed signal |
| `/api/outcomes/check` | POST | Evaluates all open (untagged) signals against Min15 klines; auto-tags hits; also runs expire_stale_signals() |
| `/api/prices` | GET | Batch price fetch for multiple symbols — used by open positions panel |
| `/api/stream/prices` | GET | SSE stream: price updates every 3s for `?symbols=` (comma-sep) |
| `/api/strategies` | GET | Returns built-in + enabled custom strategy configs; `?include_disabled=1` includes disabled custom strategies; `performance` object includes `avg_win_pnl` and `avg_loss_pnl` per strategy |
| `/api/strategies/analytics` | GET | Returns chart-ready strategy analytics: summary, equity curve, outcomes, P&L distribution, best/worst symbols, and volatility-regime performance |
| `/api/strategies/portfolio` | GET | Strategy Portfolio Lab simulator; query `strategies`, `account`, `risk_pct`, `long_vol_gate`; replays closed `pnl_pct` rows and reports account balance, drawdown, gate skips, and per-strategy contribution |
| `/api/risk-gates` | GET | Returns live risk-gate config, mode counts from `filtered_candidates`, and historical impact for the high/extreme-vol LONG gate |
| `/api/risk-gates/<gate_key>` | PATCH | Updates a risk gate live mode; supports `block`, `shadow`, `off` for configured gates such as `long_vol_long` and `short_vol_short`; writes local config to `data/risk_gates.json` |
| `/api/strategies/custom` | POST | Creates a custom strategy from a built-in base with validated weights/filters/risk settings |
| `/api/strategies/custom/<strategy_key>` | PATCH | Updates a custom strategy; supports enable/disable and config edits |
| `/api/strategies/custom/<strategy_key>` | DELETE | Deletes a custom strategy definition; historical signals remain intact |
| `/api/strategies/builtin/<strategy_key>` | PATCH | Enables or disables a built-in strategy; state persists in `data/risk_gates.json` under `disabled_builtins` |
| `/api/analysis` | POST | AI strategy review: sends last 200 tagged outcomes to Claude API |
| `/api/account/status` | GET | MEXC account connection status and equity summary — P8 |
| `/api/hl/account` | GET | Hyperliquid read-only account connection status and equity/position summary from `HL_WALLET_ADDRESS` |
| `/api/account/positions` | GET | Live exchange positions from MEXC private API — P8 |
| `/api/account/balance` | GET | Account balance and available margin — P8 |
| `/api/account/readiness` | GET | Bot readiness metrics from signals DB — P8 |
| `/api/backfill/pnl` | POST | **MAINTENANCE** — Re-evaluates historical signals (result NOT NULL, pnl_pct NULL) against live kline data; writes corrected exit_price, blended PARTIAL, and leveraged pnl_pct. Safe to call repeatedly (skips already-backfilled rows). |
| `/api/cleanup/phantom-events` | POST | **MAINTENANCE** — Deletes TP/SL events in `position_events` for signals whose `entry_at IS NULL` (phantom events logged before entry was confirmed). Does NOT delete `ENTRY_FILLED` rows. Idempotent; returns `{deleted, affected_signals}`. |

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
  "symbol":               str,   # e.g. MEXC "BTC_USDT", Hyperliquid "BTC_USDC"
  "exchange":             str,   # "MEXC" or "HYPERLIQUID"
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

`/api/signal/detail/<id>` returns an additional computed `journey` object for closed signals. It is not stored in DB. It fetches Min15 candles from MEXC for the trade window and computes: `available`, `entry_hit`, `entry_delay_minutes`, `entry_to_close_minutes`, `mae_pct`, `mfe_pct`, leveraged MAE/MFE when leverage is known, `capture_ratio_pct`, `planned_stop_pct`, `stop_pressure_pct`, `best_price`, `worst_price`, `target_hits`, `path_label`, and candle count. If MEXC no longer has the kline window, `journey.available=false` with a reason.

---

## Planned Database Tables (P8+)

Planned additions — not yet created:

```
account_snapshots   — timestamped balance, available margin, unrealized PnL (P8)
live_positions      — active exchange positions, leverage, liquidation price (P8)
execution_plans     — proposed trade plans, risk decisions (P9)
orders              — actual order lifecycle, MEXC order IDs (P11)
execution_events    — append-only audit log: API failures, risk blocks, transitions (P11)
```

All new tables follow existing MT7 DB patterns: ALTER TABLE in try/except OperationalError,
datetime.utcnow() only, UTC ISO timestamps without Z suffix.

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
  exchange: localStorage.getItem('mt7_exchange') || 'mexc',
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
  pairsByExchange: {},
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
  scanResults:        {},
  scanExchange:       null,
  scanResultsByExchange: {},
  lastScanTime:       null,
  detailSymbol:       null,   // symbol currently shown in signal detail panel (for SSE subscription)
};

const A = {
  loading: false,
  analytics: null,
  selectedKey: 'balanced',
  explainerOpen: false,
  explainerKey: null,
  editingStrategy: null,
  accountConnections: null,
};

let lastHistorySigs = [];

const STRATEGY_LEVERAGE = { balanced: 20, funding_arb: 10, momentum_breakout: 25, mean_reversion: 15 };
```

`S` and `M` are completely isolated. Never cross-reference them.
`H` manages history tab state — open positions list, price cache, timer handles, sort/filter state, closed signals.

---

`A` manages the Strategies tab analytics payload, selected strategy, and strategy-management panel state.

## Dashboard Structure

Five tabs, one shared detail panel:

| Tab button | Section div | Loaded by |
|---|---|---|
| `#tab-signals` | `#signals-section` | `scanSignals()` on button click |
| `#tab-market` | `#market-section` | `loadMarket()` on tab switch |
| `#tab-tools` | `#tools-section` | Static, rendered at init |
| `#tab-strategies` | `#strategies-section` | `loadStrategyAnalytics()` on tab switch |
| `#tab-history` | `#history-section` | `loadHistory()` on tab switch (auto) |

**History tab sub-sections:**
- `#open-positions-section` — live open positions (untagged signals) with P&L tracking, SSE price stream
- `#closed-signals-section` — tagged signals with outcome buttons, equity curve, strategy review

**Shared detail panel** (`#detail-panel`, `<aside>`): populated by `renderDetail(sig)` from signals or market tab. Slides in from right on desktop; slides up from bottom on mobile. Contains `#panel-resize-handle` (drag-to-resize, hidden on mobile) and `#panel-body` (all innerHTML writes target this, not the aside itself).

**Strategy bar** (inside `#signals-section`): pills rendered dynamically from `/api/strategies`. Custom strategies get a blue-tinted border (`.strat-btn.custom`). Pills only switch the active scan strategy; they do not open the explainer. The subtitle line includes a static "Manage in Strategies tab →" link.

**Exchange bars** (inside `#signals-section` above `#strategy-bar`, and inside `#market-section` above Market idle/ready states): MEXC / Hyperliquid pills govern the active exchange view for Signals and Market. Selection persists to `localStorage` key `mt7_exchange`. Signals cache is exchange-keyed in `H.scanResultsByExchange`, so switching exchanges swaps to that exchange's last scan or a clean idle state. MEXC scans call `POST /api/scan/all`; Hyperliquid scans call `GET /api/hl/scan?strategy=<key>`. Market cache is exchange-keyed in `M.pairsByExchange`; Market calls `GET /api/market?exchange=<key>`. History and Strategies analytics remain cross-exchange and are not filtered by this selector.

**Strategy explainer** (`#strategy-explainer`, inside `#strategies-section` above analytics content): strategy-management panel hidden by default. Shows a "MANAGING: [Strategy Name]" header, regime badge, custom badge if applicable, description, weight bars, gates, parameters, performance stats, and strategy actions (Clone / Edit / Pause/Resume / Disable / Delete). Populated by `populateExplainer(strat)` and opened by `openStrategyManager(key)` from Strategies table Manage buttons. Toggle controlled by `A.explainerOpen` / `A.explainerKey`; editor state lives in `A.editingStrategy`.

**Strategy editor** (`#strategy-editor`, inside `#strategy-explainer`): compact inline form for clone/edit. Uses `POST /api/strategies/custom`, `PATCH /api/strategies/custom/<key>`, `DELETE /api/strategies/custom/<key>`.

**Strategies tab** (`#strategies-section`): decision-focused analytics and strategy-management page. Includes the strategy-management panel, Bot Readiness, and MEXC/Hyperliquid account connection status lines. `GET /api/strategies/analytics` feeds `A.analytics`; `renderStrategyAnalytics()` renders the comparison table with per-row Manage buttons, total/avg P&L bars, selected strategy equity curve, outcome breakdown, P&L distribution, best/worst symbols, volatility-regime bars, and `renderStrategyLearning()` educational concept blocks. Charts are inline SVG/CSS only — no charting library.

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
| Strategy Analytics | dedicated Strategies tab, `/api/strategies/analytics`, comparison charts, regime/symbol breakdowns, education panels | ✅ Done |
| Paper trading data integrity | pnl_pct + leverage columns, blended PARTIAL, leveraged pnl, auto-EXPIRED, historical backfill | ✅ Done |
| Kline depth gate | enrich_signal() gates pairs with < 50 1h / < 20 4h candles; kline_depth fields | ✅ Done |
| P5a | Strategy risk gate + Strategy Portfolio Lab: high/extreme-vol LONG circuit breaker, shadow filtered-candidate logging, account-balance simulator | ✅ Done |
| P5b | Risk Gates control panel: live `block` / `shadow` / `off` modes, `/api/risk-gates`, local `data/risk_gates.json` config, historical impact summary | ✅ Done |
| P5c | Paper Trading Lifecycle v2: manual closes write realized `pnl_pct` from live/level exit price, `position_events` ledger logs ENTRY/TP/SL events, TP1/TP2 keep remainder open, detail panel shows logged/estimated locked P&L + TP/SL distance, lifecycle badges (`WATCHING` / `FILLED` / `CLOSED`), foreground outcome checks run while History is open | ✅ Done |
| P5d | Apr 26 cleanup: min-ladder-spread guard and Balanced-only extreme-vol SHORT gate in SHADOW mode | ✅ Done |
| P6a | Optional CoinGlass V4 market-data enrichment: all-coin futures snapshot when plan allows; per-symbol OI/funding/liquidation context for enriched signals | ✅ Done |
| P7a | CoinGlass signal enrichment: cross-exchange funding confirmation (Funding Arb), liquidation asymmetry soft modifier, OI/MCap fragility tag — all shadow-only, no hard gates | ✅ Done |
| P7b | Strategy lifecycle controls: built-in pause/resume, direction lock filter, volatility allowlist filter for custom strategies | ✅ Done |
| P4 | README updated and published to GitHub | ✅ Done (beta testers TBD) |
| P8 | MEXC read-only account integration + Bot Readiness tracker panel in Strategies tab | ⏳ Pending |
| P9 | Execution readiness layer — pre-flight validation, position sizing, risk budget checks, max loss gate. New lib/execution_engine.py and lib/risk_controls.py. | ⏳ Pending |
| P10 | Paper bot mode — simulated order lifecycle with fill simulation, fee/funding modeling. Distinct from current candle-based paper tracking. | ⏳ Pending |
| P11 | Assisted live trading — user-approved order placement, tiny size, full execution logging, kill switch mandatory, one strategy only. | ⏳ Pending |
| P12 | Micro-live automation — one proven strategy, automated, strict exposure caps, daily loss limits, consecutive loss shutdown. | ⏳ Pending |

---

## Current Task List

Execution readiness is tracked live in the Bot Readiness panel in the Strategies tab.
Review that panel before beginning any execution phase. You decide when to proceed —
the system surfaces the data, it does not block you.

Next in priority order:
1. Deploy P8 — account routes + readiness tracker (this session)
2. Monitor clone strategies — Balanced Focus Short and Funding Arb Focus Short
3. Review short_vol_short gate after 2+ more weeks of shadow data
4. Run python3 analyze.py on VPS DB weekly to track strategy edge

---

## Session Summary — 2026-05-01

**Job 1 — Documentation.** Updated HANDOFF.md, CLAUDE.md, AGENTS.md, STRATEGIES.md, and .env.example to introduce the P8–P12 execution roadmap. Added Execution Safety Rules (immutable, 8 rules) to CLAUDE.md and AGENTS.md identically. Added P8–P12 phase rows, four new account API routes, four new "What NOT To Do" entries, a Planned Database Tables section, and replaced the task list. No code changes in Job 1.

**Job 2 — lib/mexc_private.py + P8 routes.** Created `lib/mexc_private.py` — pure functions, no Flask, MEXC keys passed as arguments, all errors caught to stderr. Implements `get_account_assets()`, `get_open_positions()`, `get_account_summary()` with HMAC-SHA256 signing. Added four routes to app.py immediately before the entry point: `GET /api/account/readiness` (DB-only, no auth, computes per-strategy readiness_pct from trades/profit-factor/avg-pnl formula), `GET /api/account/status`, `GET /api/account/positions`, `GET /api/account/balance` (all three fail-closed with `connected: false` when keys are absent).

**Job 3 — Bot Readiness panel.** Added `#sa-readiness` div above `#sa-body` in the Strategies tab. `loadStrategyAnalytics()` now fetches `/api/account/readiness` and stores result in `A.readiness`. `renderReadinessPanel()` renders one row per strategy: name, inline progress bar (green ≥70 / amber ≥40 / red <40), readiness %, trades/avg-pnl/profit-factor stats, and "insufficient data" label when trades_with_pnl < 30. Footer shows execution mode as DISABLED. Mobile: stats hidden on narrow screens, bars still render.

**Job 4 — Symbol conviction penalty system.** Added `_load_symbol_performance_cache(min_trades=5)` and `_get_symbol_overrides()` helpers to app.py (both read-only, fail closed). Added penalty block + override block at the end of `score_ticker()` — three tiers: severe (<-30% avg P&L, 5+ trades, -20), moderate (<-15%, 5+ trades, -10), mild (<-5%, 8+ trades, -5). No hard blocks. Override actions: exempt/force_severe/force_moderate/force_mild. `run_scan()` calls both helpers once per scan and passes as `sym_perf_cache`/`sym_overrides` kwargs. `/api/risk-gates` response now includes `symbol_performance` key (penalty_count, top 20 symbols, active overrides). Added `POST /api/risk-gates/symbol-override` and `DELETE /api/risk-gates/symbol-override/<symbol>`. Four new tags in `TAG_META`/`TAG_TIPS`: `sym_penalty_severe`, `sym_penalty_moderate`, `sym_penalty_mild`, `sym_exempt`. Symbol Penalties subsection added to the Risk Gates panel in the Strategies tab with auto-detected penalties table and active overrides table with Exempt/Remove buttons. 15 penalty symbols detected on VPS at time of ship.

**2026-05-01 — Patched `funding_arb_focus_short` `allowed_volatility` to include `"extreme"` (avg +41.5% at conv≥65, 8 trades). `short_vol_short` gate confirmed scoped to `balanced` only — extreme SHORT fires freely on this clone.**

**Job 5 — analyze.py Section 5 fix.** `section_tp1_counterfactual()` previously counted all 748 signals with a `tp1` column value, producing an inflated +33,712 delta. Replaced with a DB query gated on confirmed `TP1_HIT` events from `position_events`. Corrected result: 141 confirmed TP1 hits, delta **-2,319** (laddered exits beat TP1-only, ladder strategy is working). A `_note` key prints the before/after count on every run. Section header updated to "confirmed TP1 hits only".

---

## Session Summary — 2026-05-03

**Hyperliquid Phase 1 — Scan + Account.** Completed the unfinished Claude Code integration. `lib/hyperliquid_client.py` provides pure fail-closed Hyperliquid public functions for `metaAndAssetCtxs`, candles, orderbook, read-only account state, and MT7 ticker normalization. `app.py` now imports that client, reads `HL_WALLET_ADDRESS`, preserves the `exchange` field from scoring through enrichment, routes HYPERLIQUID klines/depth/daily candles away from MEXC endpoints, and exposes `GET /api/hl/scan` plus `GET /api/hl/account`. The HL scan uses existing strategies only and logs signals in the same shape as MEXC.

**Frontend exchange selector.** Signals tab now has MEXC / Hyperliquid pills above the strategy pills. `S.exchange` persists to `localStorage` key `mt7_exchange`; MEXC scans still call `POST /api/scan/all`, while Hyperliquid scans call `GET /api/hl/scan?strategy=<key>`. Hyperliquid signal cards show a blue `HL` badge next to the symbol; MEXC cards remain unchanged. Bot Readiness now shows separate MEXC and Hyperliquid account connection lines. History and Strategies analytics remain cross-exchange.

**Verification.** `python3 -m py_compile app.py lib/hyperliquid_client.py` passed. Inline JS parsed via `node --check`. Flask import and route registration passed after installing `requirements.txt` into the local Python environment. `/api/hl/account` returns `connected:false` when `HL_WALLET_ADDRESS` is absent. A monkeypatched Flask test client verified `/api/hl/scan` response shape. Live Hyperliquid `metaAndAssetCtxs` was verified via elevated `curl`.

**QA fixes — exchange-scoped Signals and Market.** Signals now keeps separate result caches per exchange (`H.scanResultsByExchange`), so switching MEXC ↔ Hyperliquid swaps to that exchange's visible signals or a clean idle state instead of leaving stale signals from the previous exchange. Mid-scan exchange switches are guarded: completed results are stored under the exchange that launched the scan and do not overwrite the currently selected exchange view. Market tab now has the same MEXC / Hyperliquid exchange pills, exchange-keyed pair cache (`M.pairsByExchange`), and `GET /api/market?exchange=<key>` routing. Hyperliquid market rows use normalized HL tickers and show the blue `HL` badge; Market click-to-enrich passes `?exchange=<key>` so HL rows do not resolve through MEXC. Hyperliquid symbols now use `_USDC` in MT7 because Hyperliquid perps settle/quote in USDC; external sentiment lookup still maps major HL coins to USDT venues for cross-exchange context.

**Auto-refresh.** Signals keep the manual Scan button, and after an exchange has been scanned once, the currently selected exchange auto-scans every 5 minutes (`SIGNAL_AUTO_REFRESH_MS`). Market keeps manual Load/Retry, and after the selected exchange has been loaded once, the active Market tab auto-refreshes the currently selected exchange every 60 seconds (`MARKET_AUTO_REFRESH_MS`). Auto-refresh skips while an existing scan/load is in progress and does not trigger for exchanges that have not been manually loaded/scanned yet.

---

## Strategy System Direction

Strategies are scoring profiles in `STRATEGIES` in `app.py`. They share the same scanner pipeline (`score_ticker()` → `enrich_signal()` → `generate_report()` → `log_signals()`). Built-ins: Balanced, Funding Arb, Momentum Breakout, Mean Reversion. Custom strategies persist in SQLite `custom_strategies` table with stable keys.

Current state:
- Strategy selection works for scans via `GET /api/scan?strategy=<key>`
- History logs strategy_key and can filter by strategy/result/symbol
- Open positions and closed signals track outcomes with leveraged pnl_pct
- Custom strategy CRUD is fully wired (POST/PATCH/DELETE, scan support, UI editor)
- `backtest.py` iterates all strategies but remains a standalone live-data script
- P5b/P5d Risk Gates system controls the high/extreme ATR LONG gate and the Balanced-only extreme ATR SHORT gate with live `block`, `shadow`, or `off` modes. Config is local in `data/risk_gates.json`; candidates are logged to `filtered_candidates` with `gate_key` and `gate_mode`; `block` skips the signal, `shadow` lets it through with a `*_vol_shadow` tag, and `off` disables the gate.
- Strategy Portfolio Lab in the Strategies tab replays selected closed strategy outcomes as account balance, with a toggle for the current long-vol gate.
- P5c Paper Trading Lifecycle v2 makes manual outcome buttons send a live/current exit price when available so `pnl_pct` is realized immediately. `position_events` persists `ENTRY_FILLED`, `TP1_HIT`, `TP2_HIT`, `TP3_HIT`, `STOP_HIT`, and manual close events with incremental `realized_pct` and `remaining_size_pct`; `/api/signals/history` and `/api/signal/detail/<id>` include these events. TP1/TP2 no longer close the position by themselves — events are logged and the remainder stays open until TP3 or stop. Signal detail panels show live leveraged P&L plus distance to TP1, TP3, and stop. Open-position detail uses a single richer P&L box and suppresses the generic signal-status box; it shows logged locked P&L from `position_events` when present, otherwise a live estimate. Lifecycle badges show `WATCHING` for logged setups waiting for Entry 1, `FILLED` when `entry_at` or an `ENTRY_FILLED` event exists, and `CLOSED` when `result` is set. History foreground refresh triggers `/api/outcomes/check` at most once per minute while open positions exist; the 15-minute server loop remains the fallback.
- P6a CoinGlass enrichment is optional via `COINGLASS_API_KEY`. `lib/coinglass_client.py` uses CoinGlass V4 and fails closed to empty context if the key is missing, invalid, plan-gated, or the API is unavailable. `/api/market` attempts the plan-gated all-coin futures snapshot for broad rows; enriched top signals also fetch per-symbol open interest, MEXC funding interval/rate, OI change, and 24h long/short liquidation totals. The current live key can access per-symbol OI/funding/liquidation, but `/api/futures/coins-markets` returns `Upgrade plan`, so market rows mostly retain MEXC values while signal details receive CoinGlass context.

Recommended next step: deploy P6a, verify `COINGLASS_API_KEY` is present on the VPS, then add a shadow-only fragility score that uses CoinGlass OI/liquidation context plus MEXC spread/depth.

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
- Do not call MEXC kline endpoints for Hyperliquid signals — check `exchange` in `enrich_signal()` and route HYPERLIQUID klines to `fetch_hl_klines()`.
- Do not mix `HL_WALLET_ADDRESS` with `MEXC_API_KEY` / `MEXC_API_SECRET` — these are separate read systems for separate exchanges.
- Do not add `onclick="showClosedDetail(...)"` to open-positions rows — those use `showPositionDetail()`.
- Do not call any AI provider directly from routes — always use `call_ai()` from `lib/ai_client.py`.
- Do not write TP/SL events to `position_events` for signals without a prior `ENTRY_FILLED` event. `evaluate_outcome()` enforces this gate (`entry_hit` must be True before any TP/SL log call is reached). See P5c.
- Do not render "Past TPn" state, "→TPn hit" pills, or any non-zero "locked" P&L for WATCHING signals (entry not confirmed). Gate on `lifecycleState(pos).label === 'WATCHING'` in `signalLiveStatusHTML` and `buildStatusBarHTML`.
- Do not import `anthropic` at the top of `app.py` — the lazy import inside `_call_claude()` in `lib/ai_client.py` handles it.
- Do not use `esc()` for general JS string escaping — it is HTML escaping only. If future strategy keys allow quotes or punctuation, switch onclick wiring to event listeners.
- Do not call `POST /api/backfill/pnl` from a browser tab that may time out — use `curl -X POST` from the VPS shell. The route makes one MEXC API call per signal (~0.1s each).
- Do not place `direction_lock` in the `filters` dict — it lives at the top level of the strategy config (and `config_json`). The filters whitelist in `validate_custom_strategy_payload()` is unchanged.
- Do not apply the `direction_lock` gate inside `score_ticker()` — `direction` is set there, but the gate belongs in `enrich_signal()` where it can short-circuit without wasting enrichment quota.
- Do not place P7a CoinGlass conviction adjustments (`cg_funding_confirmed`, `cg_funding_divergence`, `liq_aligned`, `liq_contrary`, `fragility_high`, `fragility_extreme`) inside `score_ticker()` — per-symbol liquidation and funding context is only fetched in stage-2 `enrich_signal()`. All six tags belong after the `get_symbol_derivatives_context()` call.
- Do not promote `fragility_high`/`fragility_extreme` thresholds (0.20/0.40) to hard gates without reviewing 2+ weeks of tag performance data. They are deliberately soft discounts only.
- Do not apply Change 1 (cross-exchange funding confirmation) to any strategy other than `funding_arb`.
- Do not call any MEXC private API endpoint from app.py directly — all private calls go through lib/mexc_private.py.
- Do not commit MEXC_API_KEY or MEXC_API_SECRET — .env only, never source.
- Do not place a live order without a kill switch check — no execution code ships before P11.
- Do not share position state between paper tracking (signals table) and live positions — separate systems.
- Do not call `_load_symbol_performance_cache()` inside `score_ticker()` — call it once in `run_scan()` and pass the result as the `sym_perf_cache` kwarg. Calling it per-ticker would hit the DB 800+ times per scan.
- Do not apply `sym_overrides` inside `_load_symbol_performance_cache()` — overrides are a scoring concern and belong in `score_ticker()` after the penalty block.
- Do not use `signals.tp1 IS NOT NULL` as a proxy for "TP1 was hit" — that column is set at signal creation time. Always join to `position_events WHERE event_type = 'TP1_HIT'` for confirmed hits.

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

- [ ] `python3 analyze.py` exits 0 on live `data/signals.db` and writes valid `data/audit_report.json`
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
- [ ] Closed signal detail shows Trade Journey with MAE, MFE, capture, stop pressure, timing, best/worst price, and target hit pills
- [ ] `/api/signal/detail/<id>` includes `journey` object; old/out-of-window trades return `journey.available=false` gracefully
- [ ] Coach Review shows Claude analysis text or "Analysis unavailable" gracefully
- [ ] Coach Review prompt includes journey stats but does not recommend changing a strategy from one trade
- [ ] History banner, equity sparkline, filtered summary P&L, and closed table `$P&L` use persisted `pnl_pct`, not the old fixed R model
- [ ] History banner labels show `ACTUAL P&L`, `BEST P&L%`, and `SIM ACCOUNT`
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
- [ ] Ladders with `abs(entry1 - stop_loss) / price < 0.001` are skipped before the daily trend fetch
- [ ] Skipped tight ladders log a stderr line: `[ladder gate] {symbol} skipped — entry1=... stop=... spread_pct=...`
- [ ] Signals with 50–99 1h candles get `data_quality = "low"`
- [ ] `kline_depth_1h` and `kline_depth_4h` appear in `/api/signal/<symbol>` response
- [ ] `POST /api/backfill/pnl` returns `{"success": true, "updated": N, "skipped": N, "errors": N}`
- [ ] Backfilled rows have `evaluation_version = 'backfill_v1'`
- [ ] `POST /api/backfill/pnl` does NOT overwrite `result` or `result_note` on manually-tagged signals (only writes exit_price, pnl_pct, entry_at, evaluation_version)
- [ ] Strategy Lab card win rate denominator includes PARTIAL count (matches /api/strategies/analytics formula)
- [ ] avg_win_pnl in strategy explainer is average of all pnl_pct > 0 trades (not just result='WIN')
- [ ] avg_loss_pnl in strategy explainer is average of all pnl_pct <= 0 trades (not just result='LOSS')
- [ ] Open position status bar Live P&L % is leveraged (e.g. 20x leverage × 1% move = 20% displayed)
- [ ] Open position status bar label reads "est. P&L $" not "est. notional"
- [ ] Selecting a strategy in the open positions dropdown scopes the performance banner (pb-* stats) to that strategy's closed trades only
- [ ] Selecting a strategy updates the equity sparkline to show only that strategy's equity curve
- [ ] Selecting "All" (empty value) in the strategy dropdown reverts the banner and sparkline to full-aggregate numbers
- [ ] "Filtered: Balanced" (or equivalent strategy name) label appears below the equity sparkline when a strategy is selected
- [ ] The filter label disappears when "All" is selected
- [ ] The open positions table, banner, sparkline, and filter label all update on a single dropdown change without page reload
- [ ] H.closedAll is never mutated — getFilteredClosed() only reads and filters it
- [ ] Custom strategy clone/edit/save/disable/delete works on desktop
- [ ] `GET /api/stream/prices?symbols=BTC_USDT` returns `Content-Type: text/event-stream`
- [ ] History tab SSE subscribes on entry and closes on tab leave
- [ ] Closed signal detail shows est. notional P&L when account_size is set
- [ ] Closed signal detail shows nothing for notional when account_size is 0 or unset
- [ ] Open position detail shows live notional P&L (est. notional) updating with SSE / 30s poll
- [ ] Open positions table has symbol search input that filters rows in real time
- [ ] Open positions count shows X/Y when filters are active
- [ ] Scan button calls POST /api/scan/all and shows "Scanning..." while in progress
- [ ] After scan, H.scanResults is populated with results keyed by strategy
- [ ] Switching strategy pills loads cached results instantly (no rescan)
- [ ] POST /api/scan/all returns {success, results, total_pairs, scan_time}
- [ ] run_scan() accepts optional tickers param; skips ticker fetch when provided
- [ ] Signal detail panel header price shows live price from H.priceCache when available
- [ ] Detail panel price updates in-place every 3s via SSE without re-rendering the panel
- [ ] Opening any signal detail panel (scan or market) adds its symbol to the SSE subscription
- [ ] Closing the detail panel removes the symbol from the SSE subscription (reverts to positions-only)
- [ ] refreshPriceSubscription() does not reconnect when the symbol set hasn't changed
- [ ] Rescanning while AIA SHORT Balanced is open does not create a new AIA SHORT Balanced row in DB
- [ ] Different strategies CAN have the same symbol open simultaneously (each has its own open row)
- [ ] Open positions panel shows strategy group headers when sorted by age or strategy
- [ ] Group headers do NOT appear when sorting by pnl, stop, tp1, entry, current, dir, or symbol
- [ ] Historical duplicate rows are collapsed per symbol+direction+strategy with a "N signals" badge
- [ ] `GET /api/strategies/analytics` returns `success: true` and analytics keyed by strategy
- [ ] Strategies tab renders comparison table and selected strategy metric cards
- [ ] Strategies tab renders equity curve, outcome breakdown, P&L distribution, symbol bars, and volatility-regime bars
- [ ] Strategies tab Learn section shows core idea, best conditions, risks, read guidance, key signals, and weight concept chart
- [ ] Strategies tab equity curve hover shows symbol, outcome, timestamp, trade P&L, and cumulative P&L
- [ ] Strategies tab charts include short context descriptions explaining how to interpret the data
- [ ] `GET /api/risk-gates` returns both `long_vol_long` and `short_vol_short`
- [ ] Balanced strategy has `risk_gates.block_short_volatility = ["extreme"]`
- [ ] Balanced SHORT extreme-vol candidates are tagged/logged with `short_vol_shadow` while `short_vol_short` mode is `shadow`
- [ ] Momentum SHORT extreme-vol candidates are not gated by `short_vol_short`
- [ ] Strategies tab Risk Gates panel renders mode controls for every returned gate
- [ ] `.env` / VPS `.env` may include `COINGLASS_API_KEY`; app starts and scans normally when it is missing
- [ ] `GET /api/market` returns `coinglass_enabled` and `coinglass_pairs` metadata, and each pair includes `coinglass_available` + `derivatives_open_interest`
- [ ] `GET /api/signal/BTC_USDT` includes CoinGlass fields when key/plan allow: `coinglass_open_interest_usd`, `coinglass_oi_change_1h_pct`, `coinglass_funding_oi_weighted`, `coinglass_funding_interval_hours`, `coinglass_liq_long_24h_usd`, `coinglass_liq_short_24h_usd`
- [ ] Signal detail context grid renders CoinGlass futures OI, OI changes, funding interval/rate, and 24h liquidation totals without console errors
- [ ] Mobile: tab bar horizontally scrolls and Strategies table does not force page-level horizontal scroll
- [ ] SHORT open position (e.g. stop 1.6075, current 1.4240, TP1 1.3952, TP3 1.2537): progress dot at ≈51.9%, TP1 tick at ≈60.0%; dot renders LEFT of TP1 tick (not past it)
- [ ] LONG open position (stop 100, current 112, TP1 110, TP2 115, TP3 120): dot at 60%, TP1 tick at 50%, TP2 tick at 75%; dot renders between TP1 and TP2 ticks
- [ ] Degenerate bar (stop == tp3, or tp3 null): renders neutral "Stop — / TP3 —" fallback, no NaN in DOM, no console error
- [ ] WATCHING open position: detail panel shows big P&L in `var(--text2)` with "Hypothetical · entry not filled" sublabel, no "Past TPn" banner, no "TPn full close" locked display, no non-zero "$ locked"
- [ ] WATCHING open position: forward-looking "→TP1 X.X% / →TP3 X.X% / →SL X.X%" distance pills still render correctly
- [ ] FILLED / CLOSED open positions: detail panel behavior unchanged from pre-fix (no regression)
- [ ] `POST /api/cleanup/phantom-events` returns `{"success": true, "deleted": N, "affected_signals": M}`; second call returns `{"deleted": 0, "affected_signals": 0}`
- [ ] After cleanup: diagnostic SQL (entry_at IS NULL + phantom TP/SL events) returns zero rows
- [ ] `POST /api/cleanup/phantom-events` does NOT delete any `ENTRY_FILLED` rows
- [ ] `PATCH /api/strategies/builtin/momentum_breakout {"enabled": false}` removes it from `GET /api/strategies` (without `?include_disabled=1`)
- [ ] `balanced` still appears in registry even when disabled via the builtin PATCH route
- [ ] `POST /api/scan/all` response does not include keys for disabled built-ins
- [ ] `GET /api/scan?strategy=momentum_breakout` returns 400 when that strategy is disabled
- [ ] A custom strategy with `direction_lock: "SHORT"` returns no LONG signals in `/api/scan`
- [ ] A custom strategy with `allowed_volatility: ["low","medium"]` returns no high/extreme vol signals
- [ ] Strategy editor reads back `direction_lock` and `allowed_volatility` when editing an existing custom strategy
- [ ] Pause/Resume button appears for built-ins in the explainer; Disable/Enable/Delete appear for custom strategies only
- [ ] Disabled built-in shows as `.strat-btn.paused` (italic, reduced opacity) in the strategy bar
- [ ] `save_risk_gates()` preserves `disabled_builtins` key when writing gate config changes
- [ ] `cg_funding_confirmed` / `cg_funding_divergence` appear on `funding_arb` signals when CoinGlass OI-weighted funding data is present and MEXC rate exceeds ±0.0003
- [ ] `liq_aligned` / `liq_contrary` appear when `liq_ratio >= 3.0` (larger liq side ÷ smaller side); absent when ratio < 3.0 or either value is None
- [ ] `fragility_high` appears when `coinglass_oi_market_cap_ratio > 0.20`; applies 10% conviction discount
- [ ] `fragility_extreme` appears when `coinglass_oi_market_cap_ratio > 0.40`; applies 20% discount (replaces fragility_high)
- [ ] All six P7a tags absent when `COINGLASS_API_KEY` is not set
- [ ] No conviction change applied when relevant CoinGlass field is None
- [ ] Change 1 not applied to `balanced`, `momentum_breakout`, or `mean_reversion` signals

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

### 2026-04-28 — Session summary (strategy management moved to Strategies tab)
Moved: `#strategy-explainer` was removed from `#signals-section` and placed inside `#strategies-section` above `#sa-body`, so Signals now keeps only strategy pills and scan controls. `setStrategy()` no longer toggles the explainer; it only updates `S.strategy`, active pill styling, cached scan results, and the subtitle line. The subtitle now includes a "Manage in Strategies tab →" link that switches tabs.
State: removed `S.explainerOpen` and `S.editingStrategy`; added `A.explainerOpen`, `A.explainerKey`, and `A.editingStrategy`. The management panel is scrollable with `max-height: 70vh`.
Built: `openStrategyManager(key, fallbackStrat)` opens the explainer from the Strategies comparison table, calls `populateExplainer(strat)`, and scrolls the panel into view. `closeStrategyManager()` hides the panel and resets `A.explainerOpen` / `A.explainerKey`. The Strategies comparison table now has a right-side Manage button per row; row selection behavior remains unchanged.
CRUD flow: clone/edit/save/delete and built-in pause/resume still call existing APIs and `loadStrategies()` / `renderStrategyButtons()` so Signals pills stay current. After successful saves or toggles, Strategy Analytics refreshes and the manager reopens in the Strategies tab instead of reopening under Signals.
Verified: `python3 -m py_compile app.py` passes; inline JS parses with `JS OK`; no `S.explainerOpen` or `S.editingStrategy` references remain.

### 2026-04-28 — Session summary (strategy editor scroll clipping fix)
Fixed: strategy editor form scroll clipping — `#strategy-explainer` now scrollable (`max-height: 80vh`, `overflow-y: auto`); editor scrolls into view on open and resets to top on close. Lines changed in `index.html`: CSS rule at line 134; `scrollIntoView` after `target.innerHTML` assignment; `scrollTop=0` on Cancel inline handler; `scrollTop=0` after successful save in `saveStrategyEditor`.

### 2026-04-28 — Session summary (P7b strategy lifecycle controls)
Built: Three new strategy lifecycle capabilities driven by bear-regime data showing LONG signals deeply negative across all four built-in strategies.
Change 1 — Built-in strategy pause/resume: `_get_disabled_builtins()` reads a `disabled_builtins` key from `data/risk_gates.json`. `_set_disabled_builtins()` writes it back preserving all other keys. `builtin_strategy_config()` now sets `enabled: key not in disabled_builtins` instead of hardcoded `True`. `get_strategy_registry()` was rewritten to loop built-ins and filter by `enabled`; `balanced` is always included (even when disabled) as a fallback guard. `save_risk_gates()` was updated to preserve non-gate keys when writing so that `save_risk_gates(gates)` calls from the risk gate PATCH route no longer wipe `disabled_builtins`. New `PATCH /api/strategies/builtin/<key>` route accepts `{"enabled": bool}`, adds/removes key from the disabled set, and returns the updated strategy config.
Change 2 — Direction lock for custom strategies: `direction_lock` is a top-level config field (`"LONG"`, `"SHORT"`, or `null`). Validated in `validate_custom_strategy_payload()`. Persisted in `config_json`. A direction lock gate in `enrich_signal()` runs immediately after the kline depth gate (before momentum calcs) — `direction` is already set from stage-1 `score_ticker()` via `base["direction"]`. Prints `[direction lock]` to stderr and returns None when locked direction doesn't match.
Change 3 — Volatility allowlist for custom strategies: `allowed_volatility` is a list of allowed regimes or null. Validated in `validate_custom_strategy_payload()`. The gate in `enrich_signal()` runs after the existing vol gate block (after `should_block` check) since `vol_regime` is computed there. Prints `[vol allowlist]` to stderr and returns None for non-allowed regimes.
Frontend: Added `.strat-btn.paused` CSS (0.45 opacity, italic). `renderStrategyButtons()` adds the `paused` class to built-ins with `enabled === false`. `populateExplainer()` now shows a Pause/Resume button (amber/green) for built-ins alongside Clone; Disable/Enable/Delete remain custom-only. Filters section in explainer shows `direction_lock` and `allowed_volatility` when set. `showStrategyEditor()` gained a Direction Lock `<select>` and four Volatility checkboxes. `buildEditorPayload()` reads and sends `direction_lock` and `allowed_volatility`. New `toggleBuiltinStrategy(key, enable)` JS function calls the new PATCH route and refreshes the strategy bar.
Both `api_create_custom_strategy()` and `api_update_custom_strategy()` persist `direction_lock` and `allowed_volatility` in `config_json`. The PATCH merge dict was updated to include existing values for both fields, and the field list in the merge loop was extended.
Watch out for: The `balanced` guard in `get_strategy_registry()` ensures `balanced` is always present as a fallback. If `balanced` is disabled, it still appears in the registry with `enabled: False` so `api_signal()` and `get_strategy_config()` continue to work. Scans will not fire Balanced signals (since `api_scan_all()` iterates only enabled strategies), but the fallback for detail enrichment remains intact. The `_set_disabled_builtins()` writer reads the entire file and rewrites it — this is safe because it's only called from the new builtin PATCH route, not from hot-path scan code.

### 2026-04-27 — Session summary (P7a CoinGlass signal enrichment)
Built: Three additive conviction adjustments in `enrich_signal()`, all placed after the `get_symbol_derivatives_context()` merge (so per-symbol liquidation data is available), and before `why_signal()` so the final conviction is reflected in the why-line and AI report.
Change 1 (Funding Arb only): reads `coinglass_funding_oi_weighted` and MEXC `funding_rate`. When both exceed ±0.0003 and agree in sign, adds `cg_funding_confirmed` (+8 conviction). When they disagree in sign, adds `cg_funding_divergence` (−5 conviction). Gated strictly to `strategy_key == "funding_arb"`.
Change 2 (all strategies): reads `coinglass_liq_long_24h_usd` and `coinglass_liq_short_24h_usd`. Computes `liq_ratio = larger / smaller` (capped at 10.0 when smaller is zero). When ratio ≥ 3.0 and dominant liquidation side aligns with signal direction, adds `liq_aligned` (+5). When it opposes, adds `liq_contrary` (−5). No tag when ratio < 3.0 (symmetric market).
Change 3 (all strategies, shadow only): reads `coinglass_oi_market_cap_ratio`. Above 0.20 → `fragility_high` tag + 10% conviction discount. Above 0.40 → `fragility_extreme` tag + 20% discount (replaces, not stacks). Thresholds have no backtested basis — do not escalate to a hard gate without 2+ weeks of closed-signal data. Comment block in code states this explicitly.
Frontend: added `TAG_META` object mapping the six new tag keys to display labels (e.g. "CG ✓ Funding") and CSS color classes (`tag-green`, `tag-amber`, `tag-red`). Both tag renderers in `rowHTML` and `renderDetail` now check `TAG_META` before falling back to `t.replace(/_/g,' ')`. Added tooltip entries for all six tags in `TAG_TIPS`. Added three CSS rules for `.tag-green`, `.tag-amber`, `.tag-red`.
Verified: `python3 -m py_compile app.py lib/coinglass_client.py` passes; `python3 -c "import app; print('OK')"` exits clean; all 8 logic unit tests (confirmed/divergence/liq_aligned/liq_contrary/fragility_high/fragility_extreme/all_None/strategy_scope) pass; inline JS parses with `JS OK` (localStorage ReferenceError is browser-only, not a parse error).
Watch out for: `coinglass_funding_oi_weighted` is available from the all-coin snapshot (stage-1 base dict) AND can be updated by per-symbol context. Both paths are safe since Change 1 reads from `sig` after the context merge. The `coinglass_liq_*` fields are per-symbol only and are always None in the base dict — never reference them in `score_ticker()`.

### 2026-04-27 — Session summary (CoinGlass V4 market-data enrichment)
Built: `lib/coinglass_client.py`, an optional CoinGlass V4 client using `COINGLASS_API_KEY` and `CG-API-KEY` auth. It has cached all-coin futures market snapshots (`/api/futures/coins-markets`) and cached per-symbol derivatives context from `/api/futures/open-interest/exchange-list`, `/api/futures/funding-rate/exchange-list`, and `/api/futures/liquidation/exchange-list`. Missing/invalid keys, plan-gated endpoints, DNS/API failures, and partial responses all return empty context instead of crashing scans.
Built: `score_ticker()` now accepts a CoinGlass snapshot and adds `coinglass_*` fields plus `derivatives_open_interest` to market rows and base signals. `run_scan()`, `/api/market`, and `/api/signal/<symbol>` pass the snapshot/context through. `enrich_signal()` adds per-symbol CoinGlass OI, MEXC funding rate/interval, OI 1h/24h change, and 24h long/short liquidation totals to enriched signal dictionaries.
Built: Dashboard market rows display CoinGlass aggregated futures OI when available, falling back to MEXC open interest/hold volume. Signal detail context grid now renders CoinGlass futures OI, OI change, OI/MCap if available, funding interval/rate, 24h long/short liquidations, CoinGlass volume, and CoinGlass price. `.env.example` now includes `COINGLASS_API_KEY=`.
Live verification: The local visible CoinGlass key is accepted for per-symbol BTC context. `/api/futures/coins-markets` returns `Upgrade plan`, so broad market rows currently fall back to MEXC data; per-symbol enriched signals work. BTC smoke returned `coinglass_open_interest_usd`, `coinglass_oi_change_1h_pct`, `coinglass_funding_oi_weighted`, `coinglass_funding_interval_hours`, `coinglass_liq_long_24h_usd`, and `coinglass_liq_short_24h_usd`.
Verified: `python3 -m py_compile app.py lib/coinglass_client.py` passes; inline dashboard JS parses with `JS OK`; `python3 -c "import app; print('OK')"` exits clean aside from sandbox Arrow warnings; live `/api/market` smoke returned 200 and includes CoinGlass metadata/fields; live `/api/signal/BTC_USDT` smoke returned 200 with CoinGlass per-symbol context.

### 2026-04-27 — Session summary (Apr 26 audit cleanup: ladder guard + Balanced SHORT gate)
Diagnostic run on VPS production DB (`/opt/matrix-trader/data/signals.db`) confirmed Case B from the Apr 26 prompt: Balanced SHORT extreme had n=25, total_pnl=-856.0, avg_pnl=-34.2, wins=2; Momentum SHORT extreme stayed positive with n=14, total_pnl=746.7, avg_pnl=53.3, wins=1. High-vol SHORT rows were also positive for both Balanced (+998.9 total) and Momentum (+1044.7 total). Decision: do not touch Momentum; do not add a broad both-direction gate.
Built: Min-ladder-spread guard in `enrich_signal()` immediately after `generate_ladders(...)` and before daily trend fetch. If `abs(entries[0] - stop_loss) / price < 0.001`, it logs `[ladder gate] ...` to stderr and returns `None` fail-fast.
Built: Direction-aware `blocked_volatility_regimes(direction, strategy)` with a backward-compatible `blocked_long_volatility_regimes()` wrapper. Existing LONG behavior remains default-on for high/extreme via `long_vol_long`. SHORT behavior is opt-in only through strategy risk gates.
Built: Added Balanced strategy `risk_gates: {"block_short_volatility": ["extreme"]}` and completed `short_vol_short` gate in `DEFAULT_RISK_GATES` with mode `shadow`. Balanced SHORT extreme candidates are shadow-logged/tagged as `short_vol_shadow`; if promoted to `block`, they will be refused as `short_vol_refuse`. Momentum has no `block_short_volatility`, so Momentum SHORT extreme remains open.
Built: `/api/risk-gates` now computes historical impact generically per gate using direction, volatility list, and optional `strategy_scope`. The Strategies tab Risk Gates panel now renders controls for every returned gate, not only `long_vol_long`.
Verified: `python3 -m py_compile app.py` passes; `python3 -c "import app; print('OK')"` exits clean (sandbox prints Arrow CPU-feature warnings before `OK`); Flask test client `GET /api/risk-gates` returns 200 with `['long_vol_long', 'short_vol_short']` and `short_vol_short.mode == 'shadow'`; helper smoke confirmed Balanced SHORT blocks `['extreme']`, Momentum SHORT blocks `[]`, Balanced LONG blocks `['extreme', 'high']`; inline dashboard JavaScript parses with `JS OK`.

### 2026-04-25 — Session summary (phantom position_events + WATCHING-state display fix)
Diagnostic findings (VPS production DB): 9 signals had phantom TP/SL events in `position_events` with `entry_at IS NULL` in the signals table — 10 total phantom rows. Root cause: `evaluate_outcome()` correctly gates TP/SL logging on `entry_hit`, but when it returns `None` (TP1 or TP2 hit but no final outcome yet), `api_outcomes_check()` never updated `entry_at` in the `signals` table. On the next run, the function found new TP tiers hit on subsequent candles and logged them — but `entry_at` remained NULL. The WOJAK "WATCHING + PAST TP3" display is a separate frontend-only bug: `signalLiveStatusHTML` and `buildStatusBarHTML` compute "Past TP3" / "TP3 full close (live estimate)" from current price alone, with no lifecycle gating.
Backend fixes: (1) `evaluate_outcome()` now bootstraps `entry_hit=True` and `entry_at` from `sig.entry_at` when entry is already confirmed; candle scan window narrows to post-entry candles when entry_at is set. (2) `api_outcomes_check()` now persists `entry_at` from the ENTRY_FILLED position_event when `evaluate_outcome()` returns None and `sig.entry_at` is NULL. (3) New maintenance route `POST /api/cleanup/phantom-events` deletes TP/SL events for signals with `entry_at IS NULL`; idempotent; does not touch ENTRY_FILLED rows.
Frontend fixes: (1) `signalLiveStatusHTML` gates `state`/`stateColor` and "→TP1 hit"/"→TP3 hit" pills on `!isWatching` — WATCHING signals show lifecycle label and distances only. (2) `buildStatusBarHTML` detects WATCHING via `lifecycleState(pos).label === 'WATCHING'`; shows P&L in `var(--text2)` with "Hypothetical · entry not filled" sublabel; replaces the realized/locked block with "Watching · waiting for Entry 1 — " row.
Production cleanup run: `POST /api/cleanup/phantom-events` → `{deleted: 16, affected_signals: 11}`. Second call → `{deleted: 0, affected_signals: 0}` (idempotent confirmed).
Panels touched by frontend fix: `signalLiveStatusHTML` (Signals tab + Market tab detail panel) and `buildStatusBarHTML` (History tab open-position detail). No other panels render this status box.
Watch out for: The `isWatching` check in `buildStatusBarHTML` uses `lifecycleState(pos)` which reads both `pos.entry_at` AND `pos.position_events`. After the backend fix, signals that had ENTRY_FILLED events but NULL `entry_at` will now have their `entry_at` persisted — they will correctly show FILLED once the DB is updated. Legacy open signals without ENTRY_FILLED AND without entry_at will still show WATCHING until the next `api_outcomes_check()` confirms entry.

### 2026-04-25 — Session summary (TP/SL tick mark coordinate-system fix)
Bug: Open-position progress bar had a coordinate-system mismatch. The fill and dot correctly used `(stop − current) / (stop − tp3)` for SHORT (inverse for LONG), but the label row beneath was `display:flex;justify-content:space-between` with four evenly-spaced spans at 0/33/66/100% visual positions. The "TP1" text appeared at ~33% while the actual TP1 tick mark was at its true ~60% position, making the dot (at 52%) appear to have crossed TP1 when price hadn't reached it.
Root cause: Tick mark `div`s were correctly using `left:${_tp1pct}%` absolute positioning, but the label spans below were in a separate flex container that evenly distributed them regardless of price distances.
Fix: Added `levelPos(level)` arrow function inside `buildStatusBarHTML` — single source of truth for the proportional coordinate formula, returns null for degenerate inputs (missing/NaN values, stop==tp3 → range=0). Replaced `progress`/`_tp1pct`/`_tp2pct` with `levelPos` calls. Replaced the flex label row with a `position:relative;height:16px` track where each label uses `position:absolute;left:${pct}%` (Stop anchored left:0, TP3 anchored right:0, TP1/TP2 centered with `transform:translateX(-50%)`). Added a TP3 tick mark at 100%. Added `validBar` guard (false when `levelPos(tp3)===null`) that renders a neutral empty bar with "Stop — / TP3 —" on degenerate inputs.
Panels touched: only `buildStatusBarHTML` in index.html. No duplicate bars exist in `renderDetail`, `showClosedDetail`, or any other panel.
Watch out for: `levelPos` closes over `isLong`, `stop`, and `tp3` from `buildStatusBarHTML`'s outer scope — it is not a standalone helper and must not be extracted to module level without adding those as parameters.

### 2026-04-25 — Session summary (analyze.py — closed signal audit script)
Built: `analyze.py` — standalone read-only diagnostic script that audits `data/signals.db` and outputs a 6-section terminal report + `data/audit_report.json`. Sections: (1) volatility regime breakdown, (2) conviction band breakdown, (3) tag edge delta ranking, (4) symbol blacklist candidates, (5) TP1-only counterfactual vs laddered exits, (6) direction-flip anti-correlation check. Includes a reconciliation block verifying Sec1 and Sec2 totals agree within ±0.5 per strategy.
Schema findings committed to script comments: `volatility` column (not `volatility_regime`); `tags` is comma-separated string; `tp1`/`entry1`/`leverage` are direct columns; `signal_json` used only as leverage fallback.
Audit result on local DB: 0 qualifying rows (all 35 closed signals have `pnl_pct IS NULL` — pre-backfill data). Script exits 0 and writes an empty report with a clear message. Real audit data is on the VPS (`/opt/matrix-trader/data/signals.db`). Run `python3 analyze.py` on VPS after syncing to get the actual findings.
Decided: Do not build new strategies until `analyze.py` has been run on the VPS DB and the worst regime, worst conviction band, blacklist candidates, TP1 counterfactual delta, and direction-flip result are known. These findings are the direct input for the next session's strategy filter additions.
Watch out for: `data/audit_report.json` is gitignored (inside `data/`). Run the audit fresh each session — don't commit stale results.

### 2026-04-25 — Session summary (strategy-scoped performance banner and equity curve)
Built: `getFilteredClosed()` helper function that returns `H.closedAll` when `H.posStratFilter` is `''`, or filters by `(s.strategy_key || strategyNameToKey(s.strategy || '')) === H.posStratFilter` when a strategy is selected. Placed just before `updatePerfBanner`.
Built: All three `updatePerfBanner(H.closedAll, ...)` call sites replaced with `updatePerfBanner(getFilteredClosed(), ...)` — initial history load (line ~3555), tagOutcome reload (~4003), and fetchAndRenderPositions refresh (~4299).
Built: Strategy dropdown `#pos-strat-filter` onChange extended to call `updatePerfBanner(getFilteredClosed(), H.openPositions)` and `renderHistorySummary(getFilteredClosed())` after `renderOpenPositions()`, so the banner, sparkline, and closed summary bar all update immediately on filter change.
Built: `#pb-filter-label` element below the equity sparkline. `updatePerfBanner` sets its text to `"Filtered: <strategy name>"` (using `STRAT_META` for display name) when `posStratFilter` is active, and hides it when set to `''`.
Decided: `H.closedAll` is never mutated — `getFilteredClosed()` only reads it and returns a filtered copy. The source of truth is always the full set.
Watch out for: `getFilteredClosed()` uses `(s.strategy_key || strategyNameToKey(s.strategy || ''))` — must stay in sync with the identical expression in `renderOpenPositions()` at the strategy filter line. If the matching logic in `renderOpenPositions` ever changes, update `getFilteredClosed()` to match. A mismatch would cause the table to show N rows but the banner to compute on 0.

### 2026-04-24 — Session summary (trade accounting bug fixes — 5 fixes)
Fixed: Five trade accounting and display bugs identified by code review of commit 2d50454.

(1) **Backfill overwrote manually-set result tags** (`app.py /api/backfill/pnl`): The UPDATE in the backfill loop wrote `result` and `result_note` in addition to `pnl_pct`, silently overwriting any manually-tagged outcome with kline re-evaluation results. Root cause: the original implementation treated backfill as a full re-evaluation rather than a P&L-only fill. Fix: removed `result=?` and `result_note=?` from the UPDATE — backfill now only writes `exit_price`, `pnl_pct`, `entry_at`, and `evaluation_version`.

(2) **Live P&L in open position status bar was unleveraged** (`index.html`): `pos._pnl_pct` and `selPos._pnl_pct` were computed as raw percentage price move with no leverage multiplier, making open positions show e.g. +2% while the same trade showed +40% when closed. Root cause: leverage multiplication was omitted when computing the live figure. Fix: both the `showPositionDetail` initial compute and the SSE `_priceStreamOnMessage` handler now multiply by `pos.leverage || 1` (the DB column written at log time). The dollar estimate in the status bar auto-corrects since it derives from `pnl_pct`.

(3) **Strategy Lab card win rate excluded PARTIAL from denominator** (`app.py api_strategies()`): The win rate formula was `wins / (wins + losses)`, inflating the displayed rate for strategies with significant PARTIAL outcomes. Root cause: PARTIAL count was not fetched from DB or included in the denominator. Fix: added `SUM(CASE WHEN result='PARTIAL' THEN 1 ELSE 0 END)` to the query and changed formula to `wins / (wins + losses + partials)`, matching the formula already used by `/api/strategies/analytics` and `renderHistorySummary`.

(4) **avg_win_pnl / avg_loss_pnl excluded PARTIAL trades** (both `app.py` and `index.html`): The averages filtered on `result='WIN'` and `result='LOSS'`, so strategies with mostly PARTIAL outcomes showed `None` for both averages even though real `pnl_pct` data existed. Root cause: averaging was by result label instead of P&L sign. Fix: backend SQL now uses `pnl_pct > 0` and `pnl_pct <= 0`; frontend `renderHistorySummary` now filters `sigs` by pnl_pct sign instead of wins/losses arrays.

(5) **"est. notional" label was misleading** (`index.html buildStatusBarHTML`): The field in the status bar showing the dollar P&L on 1% risk was labeled "est. notional" — same label as the separate "Notional" field showing position size. Fix: renamed to "est. P&L $" in the status bar only. The matching label in `showClosedDetail` (fixed-price panel, not the live status bar) was left unchanged per spec.

Watch out for: `pos.leverage` (the DB column) is used for live P&L leverage, not `getLeverage(pos)` which reads `pos.leverage_cap` (a signal-dict field not present on DB-fetched positions). This is intentional — `pos.leverage` is the authoritative value logged at scan time.

### 2026-04-24 — Session summary (strategy guide documentation)
Built: Added `STRATEGIES.md`, a user-facing strategy guide covering the scanner pipeline, built-in strategy theses, strategy inputs, strategy keys, custom strategies, paper trading lifecycle, deduplication, outcomes, actual `pnl_pct` accounting, open-position monitoring, closed Trade Journey metrics, Coach Review behavior, Strategies page analytics, profitability interpretation, data/sample-size guidelines, bot-trading readiness, and optimization philosophy. Linked it from the Strategy Lab section of `README.md` and added it to project structure docs.
Decided: Keep strategy documentation separate from the main README because it is conceptual and operational guidance, not quick-start setup.

### 2026-04-24 — Session summary (dedicated Strategies analytics page)
Built: New top-level `Strategies` tab (`#tab-strategies` / `#strategies-section`) with a decision-focused analytics page. Backend route `GET /api/strategies/analytics` returns chart-ready data per strategy: summary counts, open/closed, win rate, avg/total leveraged P&L, equity curve points, outcome counts, P&L distribution buckets, best/worst symbol performance, and volatility-regime performance. Frontend state `A` caches the payload and selected strategy.
Built: The page renders a strategy comparison table, total/avg P&L comparison bars, selected-strategy metric cards, equity curve SVG, outcome bars, P&L distribution, volatility-regime bars, best/worst symbol bars, and a Learn section. Learn content explains core idea, best conditions, failure modes, read guidance, key signals, and a strategy weight concept chart for Balanced, Funding Arb, Momentum Breakout, and Mean Reversion; custom strategies inherit their base concept copy.
Built: Chart interpretation layer for Strategies. Every analytics chart now has a concise context description explaining what the data means and how a trader should use it. The strategy equity curve has visible points plus hover/focus detail showing symbol, outcome, timestamp, individual trade leveraged P&L, and cumulative strategy P&L. Bars include hover titles for count/P&L details.
Decided: Charts use inline SVG/CSS only, no charting library. The tab bar now scrolls horizontally on mobile because there are five top-level tabs.
Verified: `python3 -c "import app; print('OK')"` exits clean (sandbox prints Arrow CPU-feature warnings before `OK`). Inline dashboard JavaScript parses with `JS OK`. Flask test client returns `200 True True` for `/api/strategies/analytics` and confirms the Balanced payload exists. Local HTTP checks returned `200` for `/` and `/api/strategies/analytics`. Playwright visual smoke could not run because the bundled browser binary is not installed locally.

### 2026-04-24 — Session summary (closed trade journey + richer coach review)
Built: `compute_trade_journey(sig, pnl_pct)` and `format_journey_for_prompt(journey)` in `app.py`. Closed signal detail now fetches Min15 candles for the trade window and computes MAE (deepest adverse move), MFE (best favorable move), capture ratio, stop pressure, signal-to-entry delay, entry-to-close duration, best/worst price, target hit timestamps, candle count, and path labels like `clean_follow_through`, `near_stop_win`, `failed_fast`, and `partial_then_pressure`. The journey object is returned by `/api/signal/detail/<id>` and degrades gracefully when old trades are outside MEXC's kline window.
Built: Closed-signal detail panel now renders a deterministic `Trade Journey` section before Coach Review. It explains MAE/MFE/capture in plain language and shows cards for Signal→Entry, Entry→Close, MAE, MFE, Capture, Stop Pressure, Best Price, Worst Price, plus TP1/TP2/TP3 hit pills. Coach Review prompt now includes journey facts and asks for two short grounded paragraphs: price journey first, signal lesson second, with an explicit instruction not to recommend strategy changes from a single trade.
Decided: Journey stats are descriptive trade journaling, not optimization decisions. Aggregated journey stats can later feed strategy optimization, but single-trade reviews should only provide evidence.
Verified: `python3 -c "import app; print('OK')"` exits clean (sandbox Arrow CPU-feature warnings before `OK`), `python3 -m py_compile app.py` passes, inline JS parses with `JS OK`, a synthetic candle-path smoke test returned expected MAE/MFE/capture/TP hit values, and a mocked Flask detail-route smoke returned `200 True 10.0 coach ok`.

### 2026-04-24 — Session summary (history accounting uses actual pnl_pct)
Built: History performance now uses persisted `pnl_pct` as the source of truth instead of the old fixed R approximation. `calcEquityCurve()` filters to trades with real `pnl_pct` and compounds `balance * 1% * (pnl_pct / 100)`. `updatePerfBanner()`, `renderHistorySummary()`, and the closed table `$P&L` / Balance columns now use the same actual-P&L account model. The old `RESULT_R` remains only for the optional R column and legacy result sorting.
Built: Dashboard labels now make the accounting basis explicit: `ACTUAL P&L`, `BEST P&L%`, `SIM ACCOUNT`, and History summary `Actual P&L`. Equity tooltip now includes the trade's persisted `pnl_pct` at each point.
Decided: Strategy Analytics and History should tell the same performance story. R-multiple remains secondary and should not be used for headline profitability until/unless true R is computed from actual entry/stop/exit per trade.
Verified: Inline JS parses with `JS OK`; `python3 -c "import app; print('OK')"` exits clean. VPS accounting check using the new model reported `actual-model final=177.41 return=-11.29 trades=357` at the time of verification, reflecting additional closed trades since the earlier analysis.

### 2026-04-24 — Session summary (open position dedupe + grouping)
Built: `log_signals()` now dedupes at log time against open rows by `symbol + direction + strategy_key + result IS NULL`. If an open row already exists, the new scan candidate is skipped and the existing row is left untouched with its original entries, targets, stop, and logged_at. `init_db()` also creates `idx_signals_open_dedupe` on `(symbol, direction, strategy_key, result)` so the per-signal dedupe SELECT is index-backed; different strategies can still hold the same symbol and direction independently.
Built: `renderOpenPositions()` now collapses historical duplicate open rows by `symbol + direction + strategy`, keeps the most recent representative, and shows a small "N signals" badge on collapsed rows. Age/default and strategy sorts show sticky strategy group header rows; other sorts hide group headers. The open-position strategy filter now compares against `strategy_key` (or a display-name fallback), matching the key values used by the filter dropdown.
Verified: `python3 -c "import app; print('OK')"` exits clean (sandbox prints Arrow CPU-feature warnings before `OK`). Inline dashboard JavaScript parses with `JS OK`. Temp-DB dedupe smoke test returned `DEDUPE OK counts=1,2,3`, proving same strategy duplicates are skipped while different strategies and opposite directions still insert.

### 2026-04-24 — Session summary (SSE subscription includes detail panel symbol)
Built: `refreshPriceSubscription()` — unified function that builds the desired SSE symbol set from `H.openPositions` + `H.detailSymbol`, compares it against the current stream URL, and reconnects only when the set changes. `subscribePositionStream()` now delegates to it (one-liner). The `onmessage` handler was extracted to `_priceStreamOnMessage()` (module-level) so both paths share one handler. `H.detailSymbol` added to H state. `renderDetail()` now sets `H.detailSymbol = sig.symbol` and calls `refreshPriceSubscription()` after setting `_detailPanelSymbol`. `closePanel()` sets `H.detailSymbol = null` and calls `refreshPriceSubscription()`. Net effect: any scan signal that's not an open position now gets live SSE price updates while its detail panel is open.
Watch out for: `refreshPriceSubscription()` uses the stream's `.url` property to extract current symbols — this works because `EventSource` exposes the URL it was opened with. If symbols arrive in different order, both sides sort before comparing. Do not call `subscribePositionStream()` directly to trigger a reconnect; call `refreshPriceSubscription()` instead.

### 2026-04-24 — Session summary (live price in signal detail panel)
Built: Signal detail panel header price now shows live market price. `renderDetail()` now reads `H.priceCache[sig.symbol].price` when available, falling back to `sig.price`. The `.d-price` div gets `id="detail-live-price"` so the SSE handler can target it directly. Module-level `_detailPanelSymbol` tracks which symbol is currently open; set in `renderDetail()`, cleared in `closePanel()`. SSE `onmessage` handler: after updating `H.priceCache`, checks `_detailPanelSymbol === symbol` and calls `$('detail-live-price').textContent = fmtPrice(price)` — updates only the price text, no panel re-render. Result: price in the detail header updates at the same 3s SSE cadence as the open positions table.
Watch out for: `_detailPanelSymbol` is only set/cleared by `renderDetail` and `closePanel`. If the panel is re-populated by other paths (e.g. `showClosedDetail`, `enrichMarketPair`) it will NOT set `_detailPanelSymbol`, which is intentional — closed signals have fixed prices and market pairs are enriched on demand, not SSE-tracked.

### 2026-04-24 — Session summary (all-strategy scan refactor)
Built: `POST /api/scan/all` backend route + frontend cache. Backend: `run_scan()` gained optional `tickers: list | None` parameter — when provided, skips `fetch_mexc` and `expire_stale_signals` (caller handles both). `api_scan_all()` fetches tickers once, calls `run_scan(strategy_key=key, tickers=tickers)` for every enabled strategy in registry, logs per-strategy signals, returns `{results: {key: {signals, total_pairs, strategy}}, total_pairs, scan_time}`. Frontend: `scanSignals()` now calls `POST /api/scan/all` (no threshold param), stores `data.results` in `H.scanResults`, displays current strategy's cached results. `setStrategy()` now checks `H.scanResults[key]` before touching the explainer — if cached results exist and scan isn't running, it loads them instantly (no new network call). H state gained `scanResults: {}` and `lastScanTime: null`.
Decided: Ticker fetch is shared across all strategies (the expensive MEXC call). Each strategy still enriches its own top-30 independently (stage-2 enrichment is strategy-specific: different leverage caps, stage-2 filters). Deduplication of enrichment across strategies was considered but deferred — it requires re-stamping strategy metadata on each re-used signal, which adds complexity for marginal gain.
Watch out for: The threshold param was dropped from `scanSignals()`. The "Lower threshold" empty-state button now just triggers a full rescan. If per-strategy threshold overrides are needed later, add a `?threshold=N` query param to `POST /api/scan/all`.

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
