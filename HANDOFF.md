# Matrix Trader 7.0 — AI Handoff Document

> Paste this entire file at the start of any AI session (ChatGPT, Gemini,
> etc.) when continuing work on this project. Read every word before
> writing a single line of code. This file was auto-generated from the
> actual codebase — it reflects current state, not planned state.
> Update it at the end of every session before deploying.

Last updated: 2026-06-07
Last commit: 387584d chore: document Edge Lab Lite production runner
app.py: 12,273 lines
index.html: 13,111 lines

---

## What This Project Is

Matrix Trader 7.0 is a local web application for high-leverage crypto trading on MEXC and Hyperliquid perpetual swap markets. A Python Flask backend serves a single-file dark-theme dashboard. The user scans 800+ MEXC perp tickers, receives ranked LONG/SHORT signals with entry/TP/SL ladders derived from ATR, views a 4-section AI trade brief, and executes trades manually. Signal history is auto-logged to SQLite. A paper bot runs automated simulated trades. An external mt-learner service analyzes outcomes and generates improvement suggestions. It is not a price forecasting engine and not a SaaS product.

---

## Why These Rules Exist (MT2–MT6 Failures)

| MT6 Mistake | MT7 Rule |
|---|---|
| Matrix chat bot as delivery mechanism | Web app only |
| ARIMA price forecasting | No forecasting. Signals only. |
| Two competing TUI implementations | One interface: the web dashboard |
| Coinglass API key committed in plaintext | All keys in `.env`, never committed |
| 17 planning markdown files instead of code | Ship before you plan |
| God class `EnhancedTradingBot` (900+ lines) | `app.py` stays flat — one file |
| Multi-exchange as primary venues | MEXC is primary. Hyperliquid is secondary. Others are context only. |

---

## Hard Rules

1. **Ship before you plan.** Running code before the next feature.
2. **One file, one job.** `app.py` stays flat. `lib/` files are pure functions.
3. **No features that don't serve the trader.** If it doesn't help make a better trade decision, it doesn't ship.
4. **The mobile test is non-negotiable.** Every UI change must work on iPhone Safari.
5. **No committed secrets.** `.env` only. Never committed.
6. **Error handling is a feature.** Every API call is wrapped in try/except. App never crashes.
7. **Signal quality over quantity.** 20 high-conviction signals beats 200 weak ones.
8. **The tool is for trading, not for looking at.** Aesthetics serve the signal, not the other way around.
9. **State objects are completely isolated.** Never share state between tabs.
10. **No JS frameworks.** Vanilla JS only.
11. **No glassmorphism, gradients, or drop shadows.** Dark flat UI only.
12. **Read the actual files before writing a single line.** Do not assume state from memory or prior sessions.
13. **No databases for application state.** SQLite for signal history and outcome tracking only.

---

## Execution Safety Rules

Immutable. Cannot be softened by any future session prompt or task description.

1. Live trading is disabled by default. `LIVE_TRADING_ENABLED=false` in `.env` is the master gate.
2. Paper simulation must run successfully before assisted live begins.
3. User confirmation required before every order in assisted mode — no silent placement.
4. Kill switch must be implemented and tested before live trading activates. It is implemented.
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
├── STRATEGIES.md          ← user-facing strategy guide
├── SERVER_GUIDE.md        ← VPS access, deploy, and service management (Vultr Singapore)
├── .gitignore             ← covers .env, __pycache__, data/, *.db
├── .env                   ← secrets/config only — never commit
├── requirements.txt       ← all deps installed
├── app.py                 ← entire Flask backend — 9,388 lines; keep flat, one file
├── backtest.py            ← standalone script; do NOT import from app.py
├── templates/
│   └── index.html         ← entire frontend: HTML + CSS + JS; 9,214 lines; one file
├── static/                ← directory exists; no CSS file — all CSS is inline in index.html
├── docs/
│   ├── design-brief.md    ← original design doc; read-only reference
│   ├── project-status.md  ← may be stale; HANDOFF.md is authoritative
│   └── superpowers/
│       ├── specs/         ← approved design specs
│       └── plans/         ← implementation plans
├── .claude/
│   └── commands/
│       └── handoff.md     ← /handoff skill: regenerates HANDOFF.md from codebase
├── data/                  ← gitignored; auto-created at runtime; never commit
│   ├── signals.db         ← SQLite: signals, paper_trades, position_events, custom_strategies, filtered_candidates
│   ├── risk_gates.json    ← live risk gate config
│   ├── paper_config.json  ← paper bot operational settings
│   ├── trading_goals.json ← goal definition file (account balance, targets)
│   ├── strategy_overrides.json ← per-strategy config overrides from learner apply
│   ├── rejected_suggestions.json ← rejection log (read by mt-learner)
│   ├── ai_settings.json   ← active AI model + per-feature model overrides
│   └── reports/           ← cached Cipher intelligence reports (daily/weekly JSON)
│   └── hermes/            ← latest_memo.json + archive/ from Hermes consultancy
└── lib/                   ← pure utility functions only; no Flask, no API calls
    ├── agents.py          ← 12-analyst Cipher Research Group + 8-analyst signal pipeline
    ├── ai_client.py       ← AI provider fallback chain; call_ai() is the only public fn
    │                         Supports provider/model override params for per-feature pinning
    ├── exchange_context.py ← canonical exchange-agnostic data contract
    ├── adapters/          ← exchange normalization registry
    │   ├── __init__.py
    │   ├── mexc.py
    │   └── hyperliquid.py
    ├── indicators.py      ← RSI, EMA, VWAP, ATR, volatility_regime, daily_trend_direction
    ├── laddering.py       ← generate_ladders(price, atr, tiers, direction)
    ├── hl_execution.py    ← Hyperliquid execution: place_limit_order, kill_switch, get_positions
    ├── risk_controls.py   ← compute_daily_pnl, compute_position_size, get_readiness_verdict
    ├── mexc_private.py    ← MEXC private API client (read-only account, positions, balance)
    ├── mexc_stream.py     ← WebSocket client — built, not actively used
    ├── coinglass_client.py ← optional CoinGlass V4 client; fails closed if key missing
    └── hyperliquid_client.py ← Hyperliquid public scan + read-only account client

/opt/mt-learner/           ← External learner service on VPS; local mt-learner/ mirror also exists
    learner.py             ← Scheduler: 4 jobs on 30min/2hr/6hr/24hr intervals
    analyzer.py            ← Feature, threshold, regime analysis from signals.db (net-EV-aware)
    suggester.py           ← Generates pending.json with status: "pending_review" using net EV + W+P evidence
    researcher.py          ← Generates strategy hypothesis briefs
    coach_analyst.py       ← Coach review analysis
    models/                ← feature_weights.json, conviction_thresholds.json, regime_performance.json
    suggestions/pending.json ← Read by /api/intelligence/suggestions
    research/briefs.json   ← Read by /api/intelligence/research
    logs/                  ← learner.log (5MB rotating), last_heartbeat.txt
```

**Touch policy:**
- `app.py` and `index.html`: always read the relevant section before editing
- `lib/` files: pure functions only; no imports from app.py; no Flask
- `data/`: never touch directly; managed by `init_db()` and runtime writes
- `docs/`: read-only reference; never edit
- `.env`: never read, write, or commit
- `static/`: no CSS file; do not create one — CSS lives inline in index.html

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
eth-account>=0.8.0
msgpack>=1.0.0
```

SQLite3 is stdlib. AI fallback chain: Claude → Gemini → DeepSeek → Groq. Always use `call_ai()` from `lib/ai_client.py` — never import providers directly. `call_ai()` accepts optional `provider` and `model` kwargs to pin a specific model for a feature (e.g. coach reviews).

---

## Environment Variables

```bash
ANTHROPIC_API_KEY=        # AI trade briefs, coach reviews, reports
GEMINI_API_KEY=           # fallback AI provider
GROQ_API_KEY=             # fallback AI provider (Qwen3/Llama)
DEEPSEEK_API_KEY=         # fallback AI provider
MATRIX_PORT=8080          # optional — defaults to 8080
MEXC_API_KEY=             # optional — private account endpoints
MEXC_API_SECRET=          # optional
COINGLASS_API_KEY=        # optional — CoinGlass OI/liquidation enrichment
HL_WALLET_ADDRESS=        # optional — Hyperliquid read-only account
HL_PRIVATE_KEY=           # required for P11 live execution on Hyperliquid
LIVE_TRADING_ENABLED=false # master gate — must be explicitly true to place orders
REPORT_NARRATIVE_MODE=free # deterministic | free | auto
SCORE_VERSION=v1          # v1 (legacy step) | v2 (saturating ramp)
MT7_API_TOKEN=            # optional bearer token for API auth
MAX_DAILY_LOSS_USDT=0     # optional daily loss circuit breaker
REGIME_COUNTER_ENABLED=false # counter-trend conviction boost
LEARNER_PENDING_PATH=     # override for pending.json path
LEARNER_REJECTED_PATH=    # override for rejected_suggestions.json path
LEARNER_HEARTBEAT_PATH=   # override for last_heartbeat.txt path
```

---

## MEXC API Reference

```
Base URL: https://contract.mexc.com/api/v1

GET /contract/ticker                    — all perp tickers (800+ pairs)
GET /contract/detail                    — contract specs
GET /contract/kline/{symbol}            — OHLCV data (max 2000 candles/request at Min1)
GET /contract/depth/{symbol}            — order book
GET /contract/funding_rate/{symbol}     — current funding rate
GET /private/account/assets             — account balance (auth required)
GET /private/position/open_positions    — open positions (auth required)

Intervals: Min1, Min5, Min15, Min30, Min60, Hour4, Hour8, Day1
```

Sentiment APIs (no key needed):
- OKX L/S: `https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio`
- OKX OI:  `https://www.okx.com/api/v5/public/open-interest`

---

## Flask Routes

| Route | Method | Description |
|---|---|---|
| `/` | GET | Serves index.html dashboard |
| `/api/scan` | GET | Single strategy scan: scores 800+ tickers, enriches top 30, logs to DB |
| `/api/scan/all` | POST | Fetches tickers once, runs all enabled strategies |
| `/api/hl/scan` | POST | Hyperliquid scan |
| `/api/bybit/scan` | POST | Bybit scan stub (no adapter — fails gracefully) |
| `/api/exchanges` | GET | Lists enabled exchanges and status |
| `/api/exchanges/config` | PATCH | Enable/disable exchanges |
| `/api/scan/multi` | POST | Multi-exchange scan across enabled exchanges |
| `/api/market` | GET | All scored market-browser tickers |
| `/api/market/summary` | GET | Market-wide stats summary |
| `/api/signal/<symbol>` | GET | Fully enriches a single symbol on demand |
| `/api/signal/result` | PATCH | Tags a signal WIN/LOSS/PARTIAL/EXPIRED/SKIPPED |
| `/api/signals/stats` | GET | Aggregate signal stats |
| `/api/signals/history` | GET | Signal history with filters |
| `/api/signal/detail/<id>` | GET | Full trade detail + coach review |
| `/api/signal/detail/<id>/regenerate-review` | POST | Clear cached coach review |
| `/api/outcomes/check` | POST | Auto-evaluate open signals against klines |
| `/api/prices` | GET | Batch price fetch |
| `/api/stream/prices` | GET | SSE price updates every 3s |
| `/api/strategies` | GET | All strategies with performance stats |
| `/api/strategies/analytics` | GET | Chart-ready strategy analytics |
| `/api/strategies/portfolio` | GET | Strategy Portfolio Lab simulator |
| `/api/risk-gates` | GET | Live risk gate config + historical impact |
| `/api/risk-gates/extreme_vol_firebreak` | PATCH | Toggle extreme vol firebreak |
| `/api/risk-gates/<gate_key>` | PATCH | Change gate mode (block/shadow/off) |
| `/api/risk-gates/symbol-override` | POST | Add per-symbol risk gate override |
| `/api/risk-gates/symbol-override/<symbol>` | DELETE | Remove per-symbol override |
| `/api/strategy-overrides` | GET | Current per-strategy config overrides |
| `/api/strategy-overrides/<key>` | DELETE | Remove a strategy override |
| `/api/strategies/custom` | POST | Create custom strategy |
| `/api/strategies/custom/<key>` | PATCH | Edit or enable/disable custom strategy |
| `/api/strategies/custom/<key>` | DELETE | Delete custom strategy |
| `/api/strategies/builtin/<key>` | PATCH | Pause/resume built-in strategy |
| `/api/analysis` | POST | AI strategy review (last 200 tagged outcomes) |
| `/api/backfill/pnl` | POST | MAINTENANCE — re-evaluate historical signals |
| `/api/backfill/journey` | POST | MAINTENANCE — backfill journey metrics |
| `/api/cleanup/phantom-events` | POST | MAINTENANCE — delete orphan position events |
| `/api/account/daily-pnl` | GET | Today's realized P&L from signals DB |
| `/api/account/readiness` | GET | Bot readiness metrics |
| `/api/account/status` | GET | MEXC account connection status and equity |
| `/api/account/positions` | GET | Live MEXC positions |
| `/api/account/balance` | GET | MEXC account balance and margin |
| `/api/hl/account` | GET | Hyperliquid read-only account summary |
| `/api/intelligence/hermes` | GET | Full Hermes context packet + latest memo |
| `/api/intelligence/hermes/coach-reviews` | GET | Full coach review corpus (paginated; filterable by result/strategy) |
| `/api/intelligence/status` | GET | Shadow validation status |
| `/api/intelligence/suggestions` | GET | Learner suggestions with baseline metrics |
| `/api/intelligence/suggestions/<id>` | PATCH | Legacy apply/dismiss (backward compat) |
| `/api/intelligence/suggestions/<id>/apply` | POST | Apply suggestion — one-at-a-time enforced |
| `/api/intelligence/suggestions/<id>/reject` | POST | Reject suggestion, write to rejection log |
| `/api/intelligence/suggestions/<id>/park` | POST | Park stale evaluating suggestion without rejecting or reverting overlay |
| `/api/intelligence/research` | GET | Strategy hypothesis briefs |
| `/api/intelligence/roster` | GET | Cipher Research Group analyst roster |
| `/api/intelligence/reports/daily` | GET | Daily Cipher report (cached); rejects future dates |
| `/api/intelligence/reports/weekly` | GET | Weekly Cipher report (cached) |
| `/api/intelligence/reports/regenerate` | POST | Force regenerate a cached report |
| `/api/execution/status` | GET | Hyperliquid execution readiness |
| `/api/execution/place` | POST | Place limit order (gated by LIVE_TRADING_ENABLED) |
| `/api/execution/kill-switch` | POST | Cancel all orders + close all positions |
| `/api/ai/health` | GET | AI provider health check |
| `/api/settings/ai` | GET | Current AI model settings (global + per-feature) |
| `/api/settings/ai` | PATCH | Update global or coach_review model |
| `/api/paper/trades` | GET | Paper trade history |
| `/api/paper/filter-stats` | GET | Live winner/loser ATR% and trend_score averages |
| `/api/paper/stats` | GET | Paper bot aggregate stats |
| `/api/paper/account` | GET | Paper account value, P&L, drawdown |
| `/api/goals` | GET / PATCH | Goal definition file + computed actuals |
| `/api/paper/config` | GET / PATCH | Paper bot operational config |
| `/api/flow/<symbol>` | GET | Order flow data for symbol |
| `/api/paper/reset` | POST | Reset paper trading state |

Background threads: `_outcome_loop` (15 min), `_snapshot_loop` (1 hr), `_coach_review_loop` (10 min, 5 trades/batch), `_paper_bot_loop` (60s exit check, scan on interval).

---

## Signal Data Shape

Full dict returned by `enrich_signal()`:

```python
{
  "symbol":               str,
  "exchange":             str,   # "MEXC" or "HYPERLIQUID"
  "direction":            str,   # "LONG" or "SHORT"
  "strategy":             str,
  "strategy_key":         str,
  "leverage_cap":         int,
  "conviction":           int,   # 0–100
  "price":                float,
  "entries":              list[float],
  "exits":                list[float],
  "stop_loss":            float,
  "change_24h_pct":       float,
  "change_4h_pct":        float,
  "change_1h_pct":        float,
  "funding_rate":         float,
  "open_interest":        float,
  "next_funding_minutes": int | None,
  "volume_24h":           float,
  "atr_pct":              float,
  "volatility":           str,   # "low" | "medium" | "high" | "extreme"
  "rsi_1h":               float,
  "trend_score":          int,
  "vol_spike_ratio":      float | None,
  "daily_trend":          str | None,
  "daily_trend_aligned":  bool | None,
  "tags":                 list[str],
  "signal_why":           str,
  "ai_report":            str,
  "okx_ls_long_pct":      float | None,
  "okx_oi":               float | None,
  "sentiment_tracked":    bool,
  "strategy_is_custom":   bool,
  "strategy_config":      dict,
  "kline_depth_1h":       int,
  "kline_depth_4h":       int,
  "data_quality":         str,
  "agent_exchange":       str | None,
  "agent_regime":         str | None,
  "agent_blocked":        bool | None,
  "agent_version":        str | None,
  "agent_shadow_delta":   int | None,
  "agent_shadow_disagreement": float | None,
}
```

`signal_json` DB column stores: agent outputs, coach_review, coach_review_at, ai_report, ladder data, journey metrics.

---

## JavaScript State Objects

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
  autoRefreshId: null,
  volFilter:  'any',
  minVolume:  0,
};

const M = {
  phase:        'idle',
  pairs:        [],
  filtered:     [],
  pairsByExchange: {},
  dir:          'all',
  sort:         'conviction_base',
  search:       '',
  page:         0,
  pageSize:     50,
  renderedCount: 0,
  sortDir:      'desc',
  autoRefreshId: null,
};

let currentTab = 'signals';
let currentTVSymbol   = null;
let currentTVInterval = '60';
let currentTVExchange = 'MEXC';
```

`A` — Strategies tab: analytics payload, selected strategy, explainer state.
`I` — Intelligence tab: report cache, suggestions, briefs, roster, Hermes memo.
`H` — History tab: open positions, price cache, closed signals, SSE stream.

State objects are completely isolated. Never cross-reference between tabs.

---

## Dashboard Structure

Seven tabs:

| Tab button | Section div | Loaded by |
|---|---|---|
| `#tab-signals` | `#signals-section` | `scanSignals()` on button click |
| `#tab-market` | `#market-section` | `loadMarket()` on tab switch |
| `#tab-tools` | `#tools-section` | Static + `loadAIModels()` on tab switch |
| `#tab-strategies` | `#strategies-section` | `loadStrategyAnalytics()` + `loadGoalBenchmark()` |
| `#tab-history` | `#history-section` | `loadHistory()` on tab switch |
| `#tab-intelligence` | `#intelligence-section` | `loadIntelligence()` on tab switch |
| `#tab-paper` | `#paper-section` | `loadPaperTrading()` on tab switch |

**Shared detail panel** (`#detail-panel`, `<aside>`): always write innerHTML to `#panel-body`, never to the aside itself.

**Intelligence sub-tabs:** Overview · The Firm · Reports · Suggestions · Edge Lab · Shadow Validation · Hermes

**Strategies tab:** Goal Benchmark strip (`#goal-benchmark`) at top, then strategy explainer, then analytics.

**Tools tab:** AI Model card has two dropdowns — Active Model (global) and Coach Review Model (per-feature override).

---

## TradingView Integration

```js
function toTVSymbol(mexcSymbol, exchange) {
  exchange = exchange || 'MEXC';
  // MEXC: BTC_USDT → MEXC:BTCUSDT.P
  // HYPERLIQUID: BTC_USDC → HYPERLIQUID:BTCUSD.P
}
```

`loadTVChart(symbol, interval, exchange)` sets `currentTVExchange` before rendering.

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
| P2d | Market sentiment (OKX live, graceful fallback) | ✅ Done |
| P2e | Retry logic, specific error messages, volatility/volume filters, localStorage | ✅ Done |
| UX | First-run guide, strategy tooltips, tag hover tooltips | ✅ Done |
| Backtest | backtest.py — 14 symbols × 4 strategies, real funding rate history | ✅ Done |
| P3a | SQLite signal history — auto-log on scan, PATCH outcome, GET history | ✅ Done |
| P3b | History tab UI — summary bar, outcome buttons, filters, win rate | ✅ Done |
| P3c | AI strategy review — POST /api/analysis, Claude API, History tab button | ✅ Done |
| P3d | Open positions panel — live P&L, SSE price stream, auto-tagging, equity curve | ✅ Done |
| P3d+ | exit_price capture, closed signal detail panel, coach review | ✅ Done |
| P3e | SSE live price refresh for open positions | ✅ Done |
| Strategy Lab | strategy_key end-to-end, /api/strategies, dynamic UI, explainer, custom CRUD | ✅ Done |
| Strategy Analytics | dedicated Strategies tab, analytics charts, regime/symbol breakdowns | ✅ Done |
| Paper trading data integrity | pnl_pct + leverage columns, blended PARTIAL, auto-EXPIRED, backfill | ✅ Done |
| Kline depth gate | enrich_signal() gates pairs with < 50 1h / < 20 4h candles | ✅ Done |
| P5a | Strategy risk gate + Portfolio Lab | ✅ Done |
| P5b | Risk Gates control panel: live block/shadow/off modes | ✅ Done |
| P5c | Paper Trading Lifecycle v2: position_events ledger, TP/SL lifecycle badges | ✅ Done |
| P5d | Min-ladder-spread guard, Balanced extreme-vol SHORT gate in SHADOW | ✅ Done |
| P6a | Optional CoinGlass V4 enrichment | ✅ Done |
| P7a | CoinGlass signal tags: funding confirm, liq asymmetry, fragility | ✅ Done |
| P7b | Strategy lifecycle: pause/resume, direction lock, volatility allowlist | ✅ Done |
| Cipher Research Group | 12-analyst intelligence reports, daily/weekly Cipher briefs, first-person narratives | ✅ Done |
| mt-learner | External VPS service: feature analysis, threshold suggestions, researcher hypotheses | ✅ Done |
| P8 | MEXC read-only account + Bot Readiness tracker | ✅ Done |
| P9 | Trade Readiness Panel — pre-flight checklist, position sizing recommendation | ✅ Done |
| P10 | Paper bot — live on VPS, 50 closed trades, dynamic position sizing | ✅ Done (running) |
| Self-improving loop A+B | Goals file, apply/reject API, benchmark strip, Suggestions sub-tab | ✅ Done |
| AI model selector | Per-feature model pinning: global + coach_review_model in Tools tab | ✅ Done |
| Hermes Advisory Group | External consultancy bridge: context packet API, Hermes sub-tab, memo display, weekly timer | ✅ Done |
| Hermes coach reviews | Two-tier system: compact theme summary in packet + full corpus endpoint | ✅ Done |
| Paper/live data isolation | source field in dedup guard; paper bot only links/updates source='paper' signals | ✅ Done |
| P11 | Execution layer built (Hyperliquid kill switch, order placement, confirmation modal) | ✅ Built — NOT activated. Waiting on paper bot validation. |
| Paper bot realism | Pending entry1 wait, max-hold expiry, net fee/slippage P&L, chunked Min1 evaluator parity, UI stats | ✅ Done |
| mt-learner net objective | Threshold/regime suggestions optimize actual net `pnl_pct`, W+P, loss streak | ✅ Done/deployed |
| Liquidation price engine | `lib/risk_liquidation.py` — exchange-aware liq price on signals, paper trades, UI | ✅ Done |
| Paper hard-dollar P&L | `pnl_usd`, `gross_pnl_usd`, `cost_usd` on paper trades; dollar stats in Paper tab | ✅ Done |
| Paper closed detail panel | Clickable closed paper trade rows open right-side detail panel with full breakdown | ✅ Done |
| Org chart drill-downs | Cipher analyst cards + Hermes desk cards open profile/mandate modals with report data | ✅ Done |
| Order flow chart markers | Pair workspace chart overlays Absorb/Δ Div/Sweep/Exhaust event markers on candles | ✅ Done |
| Chart marker controls | Toggle bar + legend for Trade Events / Order Flow / Large Prints / Levels / Liquidation; localStorage | ✅ Done |
| Strategy Context card | Pair workspace sidebar: recent 20/10 perf, symbol fit, direction fit, cold streak warnings | ✅ Done |
| Hermes on-demand run | `POST /api/intelligence/hermes/run` + Run Now button with async status polling | ✅ Done |
| Market fullscreen chart | `.panel-fullscreen .chart-panel` expands to 58vh; chart reloads on toggle | ✅ Done |
| Edge Lab pipeline | `edge_lab/` package + weekly Lite timer + daily incremental refresh — candle fetch, path labeling, materializer, factor analysis → `edge_lab.db` + `factor_report.json` | ✅ Deployed/running |
| Edge Lab cohort attribution | `/api/paper/cohort-edge` + Paper tab panel attribute current paper cohort trades to Edge Lab factor states | ✅ Done/deployed |
| P12 | Micro-live automation — one proven strategy, automated, exposure caps | ⏳ Pending — gated on paper bot proving edge (50%+ W+P, positive EV after fees, 50+ trades) |

---

## Current Task List

**Next in priority order:**

1. **Evaluate paper bot gate before P12**: production has 100+ closed paper trades, but edge is not strong enough yet. Review strategy/regime concentration, outliers, drawdown, W+P, EV after costs, and whether performance survives without the biggest winner.
2. **Monitor focus-short cohort**: production paper config now runs `balanced_focus_short` and `funding_arb_focus_short` only, with `current_cohort_started_at="2026-06-07T14:50:00"`.
3. **Tighten strategy filters from evidence**: use paper stats, learner suggestions, and Edge Lab output to identify which strategy/regime combinations should remain enabled.
4. **Use Edge Lab output as research priors**: weekly Lite timer is running; review `data/factor_report.json` top states before changing strategy filters.
5. **If paper proves edge** (50+ trades, 50%+ W+P, EV > 0 after fees): prepare P12 micro-live design. Do not activate yet.

**Do NOT do yet:**
- Add `HL_PRIVATE_KEY` to VPS — paper bot has not proven edge
- Build P12 automation — gated on P11 validation
- Let Hermes directly mutate configs, trade, or read private exchange keys — Hermes is advisory only

**Production server:** Vultr Singapore `207.148.66.39` — SSH key-auth only.
**Hermes workstation:** old Hetzner `62.238.15.113` — isolated advisory agent host only.

---

## What NOT To Do

- Do not call `enrich_signal()` from `backtest.py` — it makes live API calls
- Do not import from `app.py` in a way that triggers Flask server startup
- Do not add new SQLite columns without a migration — wrap in `try/except OperationalError`
- Do not use `datetime.now()` — always `datetime.utcnow()`; all timestamps are UTC ISO without Z
- Do not add JS frameworks — no React, Vue, jQuery, Alpine
- Do not write innerHTML to `$('detail-panel')` — write to `$('panel-body')` only
- Do not filter direction server-side in `/api/signals/history` — client-side only
- Do not commit `.env`, `data/`, or `__pycache__/`
- Do not modify one tab's state object from another tab's code
- Do not call any AI provider directly — always use `call_ai()` from `lib/ai_client.py`
- Do not import `anthropic` at top of `app.py` — lazy import inside `lib/ai_client.py`
- Do not add new strategies by editing only one place — metadata spans `app.py` and `index.html`
- Do not write TP/SL events to `position_events` without a prior `ENTRY_FILLED` event
- Do not run `POST /api/backfill/pnl` from a browser — use `curl -X POST` from the VPS shell
- Do not let agents read raw exchange dicts — normalize through `lib/adapters` into `ExchangeContext` first
- Do not make MEXC or Hyperliquid API calls inside agents — use data passed from `enrich_signal()`
- Do not add SQLite columns for agent fields — agent output belongs in `signal_json`
- Do not hardcode AI provider names in routes — always go through `call_ai()`
- Do not cache coach reviews that contain `<think>` blocks — clear via regenerate route
- Do not generate reports for future dates — backend returns 400, frontend caps navigation at today
- Do not use "choppy" or "low_liquidity" as `allowed_volatility` values — not valid system regimes
- Do not pass behavioral regime labels into `api_payload.allowed_volatility`; learner regime suppressions must use `api_payload.blocked_agent_regimes`
- Do not sync goals file changes to paper config — they are independent
- Do not use the `_apply_suggestion_config()` fallthrough for unknown suggestion types — must explicitly return `False`
- Do not use `source` in `log_signals()` dedup without including it in the WHERE clause — paper and live signals for the same symbol/strategy must be separate rows
- Do not allow `_paper_check_exits()` to write results back to `source='live'` signal rows — always guard with `AND source='paper'`
- Do not compare paper strict WIN rate to live WIN+PARTIAL rate — use the same metric on both sides
- Do not treat paper `pnl_pct` as gross after 2026-05-24 — it is net after configurable fee/slippage; use `gross_pnl_pct` when gross value is needed
- Do not let learner threshold suggestions optimize strict WIN labels alone — `mt-learner/analyzer.py` now treats actual `pnl_pct` as net EV and W+P/loss streak as secondary evidence
- Do not hard-code strategy learnings directly into `STRATEGIES`; use reversible overlays in `data/strategy_overrides.json` and record learner applications in `data/experiment_ledger.json`

---

## How to Run

```bash
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY at minimum
python3 app.py
# Local:  http://localhost:8080
# iPhone: http://<LAN_IP>:8080 (same WiFi)
```

**VPS deploy (Vultr Singapore):**
```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0
rsync -avz --exclude='.env' --exclude='data/' --exclude='__pycache__' \
      --exclude='.git' --exclude='*.pyc' ./ root@207.148.66.39:/opt/matrix-trader/
ssh root@207.148.66.39 "systemctl restart matrix-trader"

# mt-learner only:
rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
      --exclude='models/' --exclude='suggestions/' --exclude='research/' \
      mt-learner/ root@207.148.66.39:/opt/mt-learner/
ssh root@207.148.66.39 "systemctl restart mt-learner"
```

---

## Returning to Claude

Start every Claude Code session with:

```
Read CLAUDE.md and HANDOFF.md before touching anything.
[Your task here]
```

---

## Session Notes

### 2026-06-07 — Session summary (learner heartbeat + focus-short paper cohort)

- Fixed misleading `learner_running: false` UI/API status: `mt-learner/learner.py` now writes `logs/last_heartbeat.txt` every 60 seconds while the scheduler loop is alive, not only when the 30-minute feature job runs.
- Deployed `mt-learner/learner.py` to production `207.148.66.39`; restarted `mt-learner`; service is active and `/api/intelligence/suggestions` now reports `learner_running: true`.
- Confirmed Claude's earlier diagnosis: disabled-strategy "leaks" in the post-tightening cohort were old open/pending trades closing after the cutoff, not new disabled entries. Current paper config was already blocking `balanced`, `custom_balanced_no_extreme_vol`, `funding_arb`, and `momentum_breakout`.
- Tightened paper config again: disabled `mean_reversion` after the post-tightening sample showed `7` closed trades with negative avg net P&L on both LONG and SHORT sides.
- Started new paper cohort: `current_cohort_started_at="2026-06-07T14:50:00"`, `current_cohort_label="Focus-short only cohort"`. Active strategies are now `balanced_focus_short` and `funding_arb_focus_short`; current cohort count starts at `0/20`.
- The pending `regime_funding_arb_choppy_20260529_001` suggestion was not applied. The app correctly blocks it because `regime_balanced_low_liquidity_20260524_001` is still `evaluating`; also, base `funding_arb` is disabled, so applying it now would create another stale evaluation guard. Revisit after adding an explicit "finish/park experiment" path or re-enabling base `funding_arb`.
- Added `POST /api/intelligence/suggestions/<id>/park` plus a Suggestions-tab `Park Evaluation` button for evaluating suggestions that cannot reach the trade window. Parking is neutral: it unlocks the learner queue, records `parked_at` / `park_reason`, appends an experiment-ledger note, and does not write a rejection record or revert overlays.
- Parked `regime_balanced_low_liquidity_20260524_001` because `balanced` is disabled and the evaluation was stuck at `9/20`; the `balanced.blocked_agent_regimes=["low_liquidity"]` guard remains active.
- Applied then parked `regime_funding_arb_choppy_20260529_001`: production `strategy_overrides.json` now has `funding_arb.blocked_agent_regimes=["choppy"]` and `funding_arb.min_conviction=69`; it is parked because base `funding_arb` is disabled and cannot generate an active evaluation cohort.
- Final learner status: `learner_running=true`; suggestions are `thresh_balanced_20260523_001=applied`, `regime_balanced_low_liquidity_20260524_001=parked`, `regime_funding_arb_choppy_20260529_001=parked`. Learner queue is unlocked for future suggestions.
- Verified Edge Lab Lite on production: `edge-lab-lite.timer` is active, last run was `2026-06-07 03:25:28 UTC`, next run is `2026-06-14 03:19:38 UTC`. The run completed at `2026-06-07T03:41:38Z`.
- Production Edge Lab outputs: `data/edge_lab.db` is present (`9.2G`), `data/factor_report.json` is present (`236K`), and `/api/intelligence/factor-report` returns success with `2,157,163` candles across templates `TP0_5_SL0_5`, `TP1_0_SL0_5`, `TP1_5_SL0_75`, `TP2_0_SL1_0`.
- Added the production Edge Lab Lite runner and systemd units to the repo: `scripts/run_edge_lab_lite.sh`, `scripts/systemd/edge-lab-lite.service`, `scripts/systemd/edge-lab-lite.timer`. Checksum dry-run against production shows content matches; only timestamp/group metadata differs.
- Added `/api/paper/cohort-edge` plus a compact Paper tab "Edge Lab Cohort Attribution" panel. It matches current cohort paper trades to same-symbol `Min15` Edge Lab candle states within a configurable 30-minute window, reports coverage, favorable/mild/unfavorable alignment buckets, strategy breakdowns, top positive factors, and recent closed trade attribution. This is research-only and does not mutate paper config or signal scoring.
- Deployed `app.py` and `templates/index.html` to production `207.148.66.39`; `matrix-trader` restarted and is active. Production endpoint verification returned `success=true` for the Focus-short only cohort with `0` current cohort trades, `0%` coverage, Edge Lab report metadata present, and latest Edge candle `2026-06-06T03:30:00`. That empty attribution is expected until the focus-short cohort produces trades and the weekly Edge Lab dataset catches up.
- Added a daily incremental Edge Lab refresh alongside the weekly Lite run: `scripts/run_edge_lab_daily.sh`, `scripts/systemd/edge-lab-daily.service`, and `scripts/systemd/edge-lab-daily.timer`. Daily timer runs Mon-Sat around `03:45 UTC` with jitter; weekly Lite remains Sunday. Both share the same lock so jobs cannot overlap.
- Manual production run of `edge-lab-daily.service` completed successfully on `2026-06-07T15:25:07Z`: processed `120` eligible top-volume symbols, fetched `81,509` recent candles, inserted/materialized `5,279` new rows, rebuilt `factor_report.json`, and advanced `/api/paper/cohort-edge` max Edge candle from `2026-06-06T03:30:00` to `2026-06-06T15:15:00`. Runtime was about `9m42s`; factor analysis is still the long pole (`~364s`).
- Cleaned Edge Lab incremental fetch logging so short lookback runs no longer produce false "partial history" warnings against the 90-day backfill expectation.

### 2026-05-24 — Session summary (paper realism, regime cleanup, net-EV learner)

**Built/deployed to Vultr `207.148.66.39`:**
- Market regime widget fixed: `low_liquidity` is now a first-class regime bucket instead of being collapsed into `unknown`.
- Added agent regimes in `lib/agents.py`: `compression`, `breakout_trend`, `liquidation_cascade`, `funding_crowded`, `risk_off_beta`; backend/frontend regime distribution knows these buckets.
- Paper bot pending-entry lifecycle:
  - Confirmed paper trades insert as `status='pending'`.
  - `_paper_check_entries()` promotes to `open` only after Min1 candle touches `entry1`.
  - Pending entries expire after `entry_timeout_hours` (default 24h) as `EXPIRED`, not losses.
  - `queued_at` and `filled_at` added/backfilled on `paper_trades`.
- Paper max-hold lifecycle:
  - `_paper_expire_stale_open_trades()` expires open paper trades after `max_holding_hours` (default 80h), freeing slots without assigning loss P&L.
  - Existing stale ENJ paper trade expired cleanly; linked `source='paper'` signal updated to `EXPIRED`.
- Paper net-cost accounting:
  - Configurable defaults: `paper_maker_fee_bps=0`, `paper_taker_fee_bps=2`, `paper_slippage_bps=3`.
  - Added `gross_pnl_pct`, `fee_cost_pct`, `slippage_cost_pct`; paper `pnl_pct` is now net after costs.
  - Backfilled 41 closed paper trades: gross avg `+1.01%`, net avg `+0.53%`, avg cost drag `0.48%`, total net `+21.60%`.
  - Paper UI now shows Strict Win Rate, W+P Rate, Net Avg P&L, Gross Avg P&L, Avg Cost, Pending Entries, Entry Expired, Hold Expired.
- mt-learner objective upgrade:
  - `analyzer.py` threshold analysis now optimizes actual `pnl_pct` net EV, includes W+P rate and max loss streak.
  - Feature analysis compares positive vs negative P&L rows instead of WIN vs LOSS labels.
  - Regime analysis emits W+P, avg net P&L, total net P&L.
  - `suggester.py` only proposes threshold changes when net EV improves; regime suppression uses W+P and/or net EV evidence.
  - Deployed `/opt/mt-learner/analyzer.py` and `/opt/mt-learner/suggester.py`; `mt-learner` restarted and active.

**Production checks after deploy:**
- `matrix-trader` active; `/api/paper/config` exposes cost and hold settings.
- `/api/paper/stats` after chunked Min1 parity: W+P `56.2%`, strict win `37.5%`, net avg `+3.63%`, gross avg `+4.09%`, avg cost `0.46%`, closed `48`, open `1`, pending `1`, hold-expired `1`.
- `mt-learner --dry-run` passed threshold/regime/proposal jobs; service restarted and active.
- Current meaningful pending suggestion: suppress `balanced` in `low_liquidity` (`112` trades, strict win `26.2%`, W+P `44.7%`, net EV `-8.9%`).

**Min1 evaluator parity fix:**
- `_fetch_klines_for_signal()` now accepts `end_ts` and chunks MEXC Min1 requests with `start`/`end` windows when the requested span exceeds a single 1440-candle call.
- `api_outcomes_check()` now evaluates live open signals with `evaluate_outcome(... interval="Min1", kline_limit=1440)` and writes `evaluation_version="min1_chunked_v1"`.
- `_paper_check_exits()` keeps using Min1, but long holds now replay the full configured holding window instead of only 24h; linked paper signals write `evaluation_version="paper_min1_chunked_v1"`.
- Production validation on `207.148.66.39`:
  - Direct 72h BTC probe fetched `4316` Min1 candles in chunks.
  - `/api/outcomes/check` evaluated `17` live open signals successfully, tagged `0`, skipped `17`.
  - Manual paper exit pass closed `3` long-held paper trades using chunked windows (`3556`, `3445`, and `3011` Min1 candles).

**Flexible learner overlay fix:**
- Added reversible `blocked_agent_regimes` strategy overlay support in `app.py`; this is intentionally separate from ATR `allowed_volatility` (`low`, `medium`, `high`, `extreme`).
- `run_scan()` now applies `blocked_agent_regimes` after `enrich_signal()` because `agent_regime` exists only after the agent pipeline runs.
- `strategy_to_api()` and the Strategy manager UI expose blocked agent regimes; active overrides panel shows min-conviction, ATR volatility, and blocked agent-regime overlays separately.
- `_apply_suggestion_config()` now accepts learner `regime_suppress` payloads as `blocked_agent_regimes`; old ATR-style `allowed_volatility` suppressions still work only for valid ATR regimes.
- `mt-learner/suggester.py` now emits `api_payload: {"blocked_agent_regimes": [regime]}` for future behavioral regime suppressions.
- Added `data/experiment_ledger.json` append-only records for applied learner experiments.
- Production `207.148.66.39` status after apply:
  - `data/strategy_overrides.json`: `balanced.min_conviction=60`, `balanced.blocked_agent_regimes=["low_liquidity"]`; `funding_arb.min_conviction=69`.
  - `thresh_balanced_20260523_001` was marked `applied` to clear the one-experiment guard; its threshold override remains active.
  - `regime_balanced_low_liquidity_20260524_001` is now `evaluating` with baseline snapshot at `2026-05-25T03:27:41Z` (`paper_ev_per_trade_pct=0.82`, `win_partial_rate=0.4932`, `current_value_usd=248.30`).

**Coach analyst AI fix:**
- `mt-learner/coach_analyst.py` no longer calls Groq directly. It loads env keys from `/opt/mt-learner/.env` and `/opt/matrix-trader/.env`, imports shared `lib.ai_client.call_ai()`, starts with Claude Sonnet (`claude-sonnet-4-6`), then falls through to configured/free providers if Claude has no key/credits or fails.
- Prompt now samples worst losses, best positive outcomes, and recent reviews, trims each excerpt, and caps prompt size around 18k chars so fallback models do not hit Groq-style 413 limits.
- Production dry-run on `207.148.66.39` succeeded with Anthropic 200s and updated 4 coach-pattern briefs: `funding_arb_focus_short`, `balanced_focus_short`, `mean_reversion`, `momentum_breakout`. `mt-learner` restarted and active.

**Follow-up correction after Claude wrong-VPS investigation:**
- Claude initially inspected/deployed to old host `62.238.15.113`; current production is `207.148.66.39`.
- The missing graduated strategy was actually present on `207.148.66.39`, but malformed as `custom_balanced_no_treme_vol` / `Balanced (no treme vol)`.
- Root cause was in `mt-learner/researcher.py` re-evaluation parsing: `stop_pressure:balancedxextreme` was split on raw `"x"`, producing `"treme"` because `extreme` contains `x`.
- Fixed/deployed researcher parser to use the known strategy prefix; invalid stop-pressure volatility now returns no proposed strategy rather than silently producing a bogus clone.
- Repaired production DB and brief: strategy is now `custom_balanced_no_extreme_vol` / `Balanced (no extreme vol)` with `allowed_volatility=["low","medium","high"]`. No signals had used the bad key.
- Do not accept the app-side “strip invalid allowed_volatility values” approach; validation should stay strict and researcher should emit valid payloads.

**Still pending before P12:**
- Paper gate review: current all-closed paper stats are `108` closed, W+P `47.2%`, strict win `27.8%`, net avg `+0.14%`, hard P&L `+$106.48`, profit factor `1.11`, best trade `+$116.94`, worst trade `-$67.25`. Sample size is sufficient, but the edge is not strong enough for P12 yet.

### 2026-06-01 — Session summary (order-flow chart overlay production fix)

- Fixed the production pair-workspace chart mode behavior on `207.148.66.39`: `Order Flow` and `Footprint` now keep the candlestick chart visible and render their flow/footprint panels below it; only `DOM` remains a chart-replacement ladder view.
- Added order-flow panel framing/scroll constraints so the panel does not consume the whole pair workspace.
- Deployed `templates/index.html` to `/opt/matrix-trader/templates/index.html`; restarted `matrix-trader`; service is active.
- Browser-verified live on `http://207.148.66.39:8080/#/pair/743`: Footprint shows `Charting by TradingView` plus `Latest FP Delta`; Order Flow shows chart plus Value Area/Bid Walls.
- Checked `mt-learner`: `systemctl is-active mt-learner` is `active`, logs show jobs running on June 1, and `/api/intelligence/suggestions` reports `learner_running: true`.
- Current production paper stats at check time: `108` closed trades, W+P `47.2%`, strict win `27.8%`, avg net P&L `+0.14%`, hard P&L `+$106.48`, profit factor `1.11`, best trade `+$116.94`, worst trade `-$67.25`. This is enough sample, but not enough edge for P12.

### 2026-06-01 — Session summary (paper gate analysis + strategy tightening)

- Analyzed all `108` closed production paper trades directly from `/opt/matrix-trader/data/signals.db`.
- Headline: overall W+P `47.2%`, strict win `27.8%`, avg net `+0.14%`, hard P&L `+$106.48`, profit factor `1.11`.
- Outlier dependency is too high: removing the top/bottom 3 trades changes the sample to `102` trades, avg net `-1.18%`, hard P&L `-$29.83`, profit factor `0.96`; removing top/bottom 5 drops to avg net `-1.86%`, profit factor `0.87`.
- Direction split is decisive: `LONG` trades are `58` closed, W+P `36.2%`, avg net `-5.39%`, hard P&L `-$194.26`, PF `0.68`; `SHORT` trades are `50` closed, W+P `60.0%`, avg net `+6.56%`, hard P&L `+$300.73`, PF `1.74`.
- Base `funding_arb` is not P12-ready: `74` closed, W+P `43.2%`, avg net `-1.8%`, hard P&L `-$20.14`, PF `0.95`. Its LONG side is the main drag: `funding_arb|LONG` has `48` closed, W+P `33.3%`, avg net `-3.82%`, hard P&L `-$51.06`, PF `0.84`; `funding_arb|SHORT` is modestly positive.
- `funding_arb_focus_short` remains the cleaner experiment: `9` closed, W+P `55.6%`, avg net `+7.74%`, hard P&L `+$29.52`, PF `1.60`, but sample is still small.
- Regime warning: `low_liquidity` remains dangerous despite some winners: `20` closed, avg net `-8.27%`, hard P&L `-$158.88`, PF `0.61`.
- Production paper config was tightened via `PATCH /api/paper/config`: `disabled_strategies` is now `["balanced", "custom_balanced_no_extreme_vol", "funding_arb", "momentum_breakout"]`. This stops new base `funding_arb` and `momentum_breakout` entries. Existing open/pending trades were not touched.
- Next paper review should judge the post-tightening sample separately, with focus on `funding_arb_focus_short`, `balanced_focus_short`, and any remaining `mean_reversion` experiment. Do not mix pre-tightening base `funding_arb` performance into future P12 readiness.
- Added compact post-tightening cohort tracking to `/api/paper/stats` and the Paper tab. Config keys: `current_cohort_started_at="2026-06-01T14:17:46"` and `current_cohort_label="Post-tightening cohort"`.
- The Paper tab now shows one slim cohort strip above the existing stat cards: progress toward 20 closed trades, W+P, avg net P&L, profit factor, hard P&L, and active strategies. It intentionally avoids another large card section.
- Live browser verification passed on production: cohort strip displayed `Post-tightening cohort`, active strategies `balanced_focus_short`, `funding_arb_focus_short`, `mean_reversion`, and progress `1/20` shortly after deploy.

### 2026-06-01 — Session summary (live paper prices + visible footprint/order flow)

- Fixed why open paper positions showed `Entry` equal to `Current`: `/api/stream/prices` emits `{symbol, price}` events, but the frontend was treating each event as a symbol→price map. The Paper tab now parses both shapes safely.
- `/api/paper/trades` now seeds `current_px` for open/pending paper trades from one MEXC ticker call so first render uses market price instead of falling back to entry while the SSE stream connects.
- Paper open rows now update live price, P&L, stop distance, TP1 distance, and remove the fallback `~` marker once live price is available.
- Pair workspace now subscribes to the same live market stream for the selected trade and updates the Position Management `Current`, `P&L`, and `Risk To Stop` values in place.
- Fixed hidden order-flow/footprint visibility: `.orderflow-panel` is inserted directly after `#pair-chart-container`, before marker controls/tabs, so Footprint and Order Flow are visible immediately under the candle chart.
- Deployed `app.py` and `templates/index.html` to production `207.148.66.39`; `matrix-trader` restarted and is active.
- Production API verification: `/api/paper/trades` returned live `current_px` for open trades (`COTI_USDT` entry `0.01267497`, current `0.01281`; `MANTRA_USDT` entry `0.00852374`, current `0.00833`). SSE verification returned live `{symbol, price}` events for both symbols.
- Browser verification on `http://207.148.66.39:8080`: Paper open rows showed live Current/P&L instead of entry fallback; `#/pair/751` showed live Current/P&L in the right Position Management panel; Footprint mode rendered chart plus footprint panel with `Tape Delta`, `Latest FP Delta`, `POC`, and imbalance data.

---

### 2026-05-26 — Session summary (Hermes on-demand, market fullscreen chart, doc updates)

- `POST /api/intelligence/hermes/run` + `GET /api/intelligence/hermes/run/status` — triggers `scripts/run_hermes_weekly_from_vultr.sh` in background thread (5 min timeout); returns 409 if already running.
- "Run Now" button added to Hermes panel header with async status polling and auto-refresh on completion.
- Market tab fullscreen fix: `.panel-fullscreen .chart-panel { height: 58vh !important }` — chart expands when panel goes fullscreen. `togglePanelFullscreen()` now reloads active chart after toggle so it renders at new size.
- HANDOFF.md and README.md updated to reflect all work since May 24.

### 2026-05-26 — Session summary (Codex: order flow markers, chart controls, strategy context)

- Pair workspace chart now overlays order-flow event markers from recorded flow history: Absorb (diamond), Δ Div (circle), Sweep (arrow), Exhaust (square). Merged with paper trade lifecycle markers.
- Flow event context added to chart hover/readout: event type and confidence score on candle hover.
- Chart marker toggle bar + legend added under pair workspace chart. Toggles: Trade Events, Order Flow Events, Large Prints, Levels, Liquidation. Preferences in localStorage.
- Strategy Context card added to pair workspace right sidebar. Shows: recent 20/10 perf, symbol-specific EV, direction fit, current streak, avg win/loss, cold streak warnings.
- `GET /api/paper/strategy-context/<trade_id>` backend endpoint added.
- `edge_lab/` package built locally: candle fetch (`mexc_data.py`), path labeler, feature engine, materializer, factor engine (ATR/RSI/regime/trend/volume/tag), factor report → `data/factor_report.json`. Separate `edge_lab.db`. CLI runners: `edge_lab_build.py`, `edge_lab_factors.py`, `edge_lab_materialize.py`. Also: `fade_hypothesis.py`, `analyze.py` standalone analysis scripts. **Not yet deployed to VPS.**

### 2026-05-25 — Session summary (org chart drill-downs)

- Built richer clickable org-chart cards in `templates/index.html`.
- Cipher Research Group analyst cards now open a larger profile modal with role/specialty, exchange coverage, decision inputs owned, latest daily report excerpts, and domain-specific evidence tables.
  - Example verified: Kenny Hassan shows Funding Autopsy plus funding heatmap rows.
  - Report data is loaded from `/api/intelligence/reports/daily` and cached in `I.reportCache`.
- Hermes Advisory Group desk cards are now clickable and open a desk modal with mandate, owned metrics/signals, latest synced Hermes memo section, and recent memo archive.
  - Example verified: Performance Audit Desk shows Bottom-Line Scoreboard slot, paper/live EV ownership, W+P ownership, sample size, and archive entries.
- Deployed to production `207.148.66.39`; `matrix-trader` restarted and active.
- Validation: frontend script parse passed (`JS OK`); headless browser smoke test clicked Cipher firm card and Hermes desk card successfully. Only browser console issue observed was a harmless `404` for a missing static resource/favicon.

### 2026-05-25 — Session summary (paper hard P&L + closed detail)

- Paper stats now expose hard-dollar performance in `/api/paper/stats`: total/avg paper P&L dollars, gross dollar P&L, cost dollars, profit factor, best/worst trade dollars.
- `/api/paper/trades` now enriches each trade row with `pnl_usd`, `gross_pnl_usd`, and `cost_usd` using `size_usd * pnl_pct / 100`.
- Paper tab now shows Paper Account, Hard P&L, Avg $ / Trade, Profit Factor, Costs Paid, Best Trade $, and Worst Trade $.
- Closed paper trades table now includes `$ P&L` and `SIZE` columns. Rows are clickable.
- Clicking a closed paper trade opens a right-side detail panel similar to History with hard P&L, net/gross/cost breakdown, position size, leverage, entry/exit/stop/TP, flow score/reasons, duration, and linked signal journey/coach review when available.
- Fixed a production `api/signal/detail` 500 caused by `_generate_coach_review()` referencing `load_ai_settings` without importing it locally.
- Deployed to `207.148.66.39`; `matrix-trader` active.
- Validation: Python compile passed, frontend JS parse passed, API returned hard-dollar fields, and headless browser smoke test confirmed hard P&L cards, `$ P&L` column, clickable closed row, paper detail panel, and linked trade journey.

---

### 2026-05-24 — Session summary (paper/live data integrity + coach review improvements + Hermes depth)

**Built:**
- Per-feature AI model selector: `coach_review_model` / `coach_review_provider` in `data/ai_settings.json`; second dropdown in Tools tab AI card; `call_ai()` extended with `provider`/`model` override params in `lib/ai_client.py`
- Hermes two-tier coach review system: compact theme summary (10 keyword categories, result breakdown) always in Hermes packet; `coach_reviews_recent_20` full text; new `/api/intelligence/hermes/coach-reviews` deep-dive endpoint (paginated, filterable by result/strategy)
- Paper/live signal source isolation: `log_signals()` dedup now includes `source`; paper bot post-log lookup requires `source='paper'`; `_paper_check_exits()` guards signal UPDATE with `AND source='paper'`
- Historical data repair: 37 signal rows on VPS corrected from `source='live'` → `source='paper'`

**Decided:**
- Coach reviews: full context was attempted after Anthropic credits topped up, but mt-learner `coach_analyst.py` still has Groq 413 payload failures as of later 2026-05-24 session; next session should cap/summarize payloads.
- Paper bot W+P was actually 55% (not 35%) — prior metric was comparing apples to oranges (strict WIN vs W+P). Later work moved paper P&L to net-after-cost accounting and chunked Min1 parity; current net avg is `+3.63%` over 48 closed trades.
- Four-lever paper realism is complete for current MT7 needs: entry1 wait, max-hold expiry, fee/slippage deduction, and full chunked Min1 evaluator parity for long holds.

**Deferred:**
- P12 micro-live automation

**Watch out for:**
- Paper W+P 55% is still a small sample (40 trades) — EV is the more important metric and it's near zero
- `source` column must be in dedup guard or paper/live rows will collide for same symbol/strategy
- `_paper_check_exits()` must never write back to live signal rows — always `AND source='paper'`
- When comparing paper vs live performance always use the same metric (both W+P or both strict WIN)

---

### 2026-05-23/24 — Session summary (Hermes Advisory Group bridge)

- Added `/api/intelligence/hermes` and Hermes sub-tab to MT7 Intelligence
- Deployed old-VPS runner at `/opt/mt7-hermes/run_consultancy.sh` on `62.238.15.113`
- Installed `mt7-hermes-weekly.timer` on Vultr (Sundays 05:30 UTC)
- First Hermes memo generated and synced to production
- Fixed self-improving loop bugs: unknown suggestion types, legacy PATCH guard, goals/paper config decoupling
- Fixed future-date reports: backend returns 400, frontend caps navigation at today
- Fixed researcher.py vocabulary: behavioral labels ("choppy") must not flow into `api_payload.allowed_volatility`

---

### 2026-05-23 — Session summary (Ops / Vultr / MEXC migration)

- Migrated production to Vultr Singapore `207.148.66.39`; SSH key-auth only
- MEXC subaccount IP whitelist set to Vultr IP; private endpoints now work
- `lib/mexc_private.py` fixed: correct signing target, `/private/` endpoint paths
- Edge Lab Lite weekly timer installed (Sundays 03:15 UTC)

---

### 2026-05-22 — Session summary (Cipher Research Group gaps + first-person narrative rewrite)

Reviewed Codex's Intelligence tab implementation. Fixed 4 gaps: analyst bio expand modal, date/week navigation, in-memory report cache, weekly spotlight render. Full narrative rewrite: all analyst notes now first-person with specific data references and forward calls. Deployed to VPS.

---

### 2026-05-13 — Session summary (Phase 2 agents + History stats + P11 execution layer)

Phase 2 agents live — `agent_shadow_delta` applied to conviction. History tab stats overhauled. P11 execution layer shipped: EIP-712 signing, kill switch, order placement gated by `LIVE_TRADING_ENABLED`.

---

### 2026-05-12 — Session summary (0-signal bug fix + P9 Trade Readiness Panel)

Fixed 0-signal bug: strategies now run sequentially in `api_scan_all()`. P9 Trade Readiness Panel shipped.

---

### 2026-05-19/20 — Session summary (signal quality research + scoring improvements + paper bot simulation fix)

6 scoring improvements deployed. Paper bot exit evaluation fixed to use Min1 klines.
