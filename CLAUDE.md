# Matrix Trader 7.0 — Claude Code Context

> Read this file at the start of every session.
> **Phase status and task list live in HANDOFF.md — that is the source of truth.**
> This file covers orientation, architecture, rules, and what not to touch.
> Do not trust your memory of a prior session. Read the actual files.

---

## What This Is

**Matrix Trader 7.0** is a local web application for high-leverage crypto trading on MEXC perpetual swap markets. A Python Flask backend serves a single-file dark-themed dashboard. It runs on a Mac or VPS and is accessible from iPhone over local WiFi or via the VPS IP.

**The core loop:**
1. User hits "Scan All Perps" — fetches all 800+ MEXC tickers via public API
2. Sees ranked LONG/SHORT signals with conviction scores, entry/TP/SL ladders
3. Clicks a signal → AI-generated 4-section trade brief (Claude API)
4. Tags outcomes (WIN / LOSS / PARTIAL / EXPIRED / SKIPPED) — auto-evaluation runs every 15 min
5. Reviews strategy analytics, equity curve, and per-trade coach reviews
6. Executes manually on MEXC

**It is not:**
- An execution bot yet — order placement is a staged future capability (P8–P12), currently disabled.
- A price forecasting engine (no ARIMA, no ML prediction)
- A SaaS product (local + one VPS for now)
- A multi-exchange aggregator (MEXC is primary; Binance/Bybit/OKX are context only)

---

## Why These Rules Exist — MT2–MT6 Failures

| MT6 Mistake | MT7 Rule |
|---|---|
| Chatbot as delivery mechanism | Web app only |
| ARIMA price forecasting | No forecasting. Signals only. |
| Two competing TUI implementations | One interface: the web dashboard |
| API key committed in plaintext | All keys in `.env`, never committed |
| 17 planning files instead of code | Ship before you plan |
| God class `EnhancedTradingBot` (900+ lines) | `app.py` stays flat — one file |
| Multi-exchange as primary venues | MEXC is primary. Others are context. |
| Jumped to automation without validated edge | Paper bot (P10) before micro-live (P12). Bot Readiness panel tracks progress. You decide when the data is sufficient. |

---

## Phase Status

See **HANDOFF.md** — that file is updated every session and is authoritative.

As of April 27, 2026: phases P0 through P7a are complete. `app.py` is 3,692 lines.
`index.html` is 5,296 lines. The app is live on a VPS at `root@62.238.15.113`.

---

## File Structure

```
Matrix_Trader_7.0/
├── CLAUDE.md              ← this file (orientation + rules)
├── AGENTS.md              ← Codex orientation; mirrors CLAUDE.md; keep in sync
├── HANDOFF.md             ← session state, task list, session summaries — authoritative
├── STRATEGIES.md          ← user-facing strategy guide
├── SERVER_GUIDE.md        ← VPS access, deploy, service management
├── README.md              ← public-facing setup guide (published)
├── .gitignore             ← covers .env, __pycache__, data/, *.db
├── .env                   ← secrets only; never read, never write, never commit
├── requirements.txt       ← all deps installed; add packages here if needed
├── app.py                 ← entire Flask backend — 4,458 lines; keep flat, one file
├── backtest.py            ← standalone script; do NOT import from app.py
├── templates/
│   └── index.html         ← entire frontend: HTML + CSS + JS; one file, no framework
├── static/                ← directory exists; no CSS file — all CSS is inline in index.html
├── docs/
│   ├── design-brief.md    ← original design doc; read-only reference
│   └── project-status.md  ← may be stale; HANDOFF.md is authoritative
├── .claude/
│   └── commands/
│       └── handoff.md     ← /handoff skill: regenerates HANDOFF.md from codebase
├── data/                  ← gitignored; auto-created at runtime; never commit
│   ├── signals.db         ← SQLite: signals, custom_strategies, position_events, filtered_candidates
│   ├── risk_gates.json    ← live risk gate config (block/shadow/off per gate)
│   └── backtest_results.json
└── lib/                   ← pure utility functions only; no Flask, no API calls
    ├── agents.py          ← 8-analyst Phase 1 shadow agent layer
    ├── exchange_context.py ← canonical exchange-agnostic data contract
    ├── adapters/          ← exchange normalization registry
    │   ├── __init__.py
    │   ├── mexc.py
    │   └── hyperliquid.py
    ├── indicators.py      ← RSI, EMA, VWAP, ATR, volatility_regime, daily_trend_direction
    ├── laddering.py       ← generate_ladders(price, atr, tiers, direction)
    ├── mexc_stream.py     ← WebSocket client (built; not used by SSE route — SSE uses poll loop)
    ├── coinglass_client.py ← optional CoinGlass V4 client; fails closed if key is missing
    ├── hyperliquid_client.py ← Hyperliquid public scan + read-only account client
    └── ai_client.py       ← AI provider fallback chain; call_ai() is the only public function
```

**Touch policy:**
- `app.py` and `index.html`: read the relevant section before editing
- `lib/`: pure functions only — no imports from `app.py`, no Flask
- `data/`: never touch directly — managed by `init_db()` and runtime writes
- `docs/`: read-only reference; never edit
- `.env`: never read, write, or commit
- `static/`: no CSS file — do not create one; CSS lives inline in `index.html`

---

## Tech Stack

```
Backend:    Python 3.11+ / Flask
Frontend:   Single HTML file — vanilla JS, inline CSS, dark theme, no build step
Database:   SQLite3 (stdlib) — data/signals.db
AI:         lib/ai_client.py — fallback chain: Claude → GPT → Gemini → Groq
Data:       MEXC public contract API (no auth for market data)
            OKX public API (L/S ratio, OI — geo-unrestricted)
            CoinGlass V4 (optional — requires COINGLASS_API_KEY in .env)
SSE:        /api/stream/prices — poll loop pushing prices every 3s
WebSocket:  lib/mexc_stream.py — built but not wired to any route
```

**Dependencies (all installed):**
```
flask, requests, pandas, numpy, websocket-client, python-dotenv,
anthropic, google-generativeai, openai, groq
```

---

## Environment Variables

```bash
# .env — never commit this file
ANTHROPIC_API_KEY=sk-ant-...     # required — signal reports, coach reviews, strategy analysis
MATRIX_PORT=8080                  # optional — defaults to 8080
MEXC_API_KEY=                     # optional — only needed for private endpoints (not currently used)
MEXC_API_SECRET=                  # optional
COINGLASS_API_KEY=                # optional — enables CoinGlass OI/liquidation enrichment
HL_WALLET_ADDRESS=                # optional — Hyperliquid read-only account status
```

---

## Agent Shadow Layer

Phase 1 of the Matrix Trader agent system is shadow-first. The 8-analyst pipeline runs from `enrich_signal()` through `lib.agents.run_agent_pipeline()`, but agent conviction deltas are not applied to signal conviction yet. Outputs are stored in `signal_json` under fields such as `agent_exchange`, `agent_regime`, `agent_narrative_bull`, `agent_structural_bull`, `agent_version`, `agent_shadow_delta`, `agent_shadow_narrative_delta`, `agent_shadow_structural_delta`, and `agent_shadow_disagreement`.

Agent tags are prefixed with `agent_shadow_`. Deterministic Risk Manager hard blocks can still reduce conviction by 30 and add `agent_blocked` because those are math/risk gates, not LLM judgement.

Phase 2 must not apply `agent_shadow_delta` until at least 50 closed forward-tested signals have agent data, positive shadow deltas beat baseline, negative shadow deltas underperform baseline, high disagreement correlates with worse outcomes, and scan time stays within 10 seconds of the pre-agent baseline.

---

## Running the App

```bash
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY
python3 app.py
# Local:  http://localhost:8080
# iPhone: http://<LAN_IP>:8080 (same WiFi)
# Port configurable via MATRIX_PORT env var
```

**VPS deploy:**
```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0
rsync -avz --exclude='.env' --exclude='data/' --exclude='__pycache__' \
      --exclude='.git' --exclude='*.pyc' ./ root@62.238.15.113:/opt/matrix-trader/
ssh root@62.238.15.113 "systemctl restart matrix-trader"
```

---

## Signal Pipeline — How Scoring Works

Two stages. Stage 1 is fast and free; Stage 2 is expensive and limited to top 30.

**Stage 1 — `score_ticker()` — kline-free, runs on all 800+ tickers**

Inputs from `/contract/ticker` response only (no extra API calls):

| Input | Effect |
|---|---|
| `riseFallRate` (24h change) | Momentum score — strong tier >5%, weak tier >2% |
| `fundingRate` | Funding score — negative = squeeze setup |
| `fairPrice` vs `lastPrice` | Basis spread — premium = bearish lean, discount = bullish |
| `volume24` | Volume multiplier (strategy-defined) when vol > $10M |

Direction = whichever side accumulates more points. Ties go LONG.
Output: `conviction_base` (0–100), `direction`, `tags[]`, base signal dict.

**Stage 2 — `enrich_signal()` — top 30 signals only, runs in 10 concurrent threads**

Adds: 1h klines (RSI, EMA, ATR, trend score), 4h klines (daily trend), order book depth (imbalance), funding rate, market sentiment (OKX L/S, OKX OI; Binance/Bybit geo-blocked), CoinGlass OI/liquidation context (optional), ladders (3-tier ATR-based entry/TP/SL), signal_why, ai_report.

Gate: pairs with < 50 1h candles or < 20 4h candles are skipped.

**Risk gates — applied after Stage 2, before `log_signals()`**

Two live gates in `data/risk_gates.json`, each with `block` / `shadow` / `off` mode:
- `long_vol_long`: high/extreme-ATR LONG circuit breaker (default: `block`)
- `short_vol_short`: extreme-ATR SHORT circuit breaker, Balanced only (default: `shadow`)

Blocked signals are dropped. Shadow signals pass through tagged `*_vol_shadow`. Both modes log to `filtered_candidates` table.

**Conviction threshold:** default 55. Signals below this are filtered from results.

---

## Strategy System

Four built-in strategies in `STRATEGIES` dict in `app.py`:

| Key | Name | Leverage | Character |
|---|---|---|---|
| `balanced` | Balanced | 20x | General-purpose — all regimes |
| `funding_arb` | Funding Arb | 10x | Requires meaningful funding rate |
| `momentum_breakout` | Momentum Breakout | 25x | Requires strong 24h move |
| `mean_reversion` | Mean Reversion | 15x | RSI extremes only |

Custom strategies persist in `custom_strategies` SQLite table. They clone a built-in base and can override weights, filters, leverage cap, min conviction, and regime.

**Do not add a new strategy by editing only one place.** Metadata spans:
- `STRATEGIES` and `_STRATEGY_NAME_TO_KEY` in `app.py`
- `STRAT_META` and `STRATEGY_LEVERAGE` in `index.html`

---

## Database Schema — Key Tables

**`signals`** — one row per logged signal
Key columns: `symbol`, `direction`, `strategy`, `conviction`, `price`, `entry1–3`, `tp1–3`, `stop_loss`, `atr_pct`, `volatility`, `funding_rate`, `rsi_1h`, `trend_score`, `tags`, `signal_why`, `result`, `exit_price`, `entry_at`, `pnl_pct`, `leverage`, `data_quality`, `signal_json`, `evaluation_version`, `strategy_key`

**`position_events`** — incremental trade lifecycle ledger
Event types: `ENTRY_FILLED`, `TP1_HIT`, `TP2_HIT`, `TP3_HIT`, `STOP_HIT`, manual close events.
Each event stores `realized_pct` and `remaining_size_pct`.

**`custom_strategies`** — user-created strategy clones

**`filtered_candidates`** — signals that were blocked or shadowed by risk gates
Stores `gate_key`, `gate_mode`, and why the signal was suppressed.

---

## Dashboard — Five Tabs

| Tab | Section | What It Does |
|---|---|---|
| Signals | `#signals-section` | Strategy bar, filter bar, ranked signal cards, detail panel |
| Market | `#market-section` | All 800+ tickers paginated, sortable, searchable |
| Tools | `#tools-section` | Risk calculator, compound planner |
| Strategies | `#strategies-section` | Analytics: equity curves, regime breakdown, symbol performance, Portfolio Lab |
| History | `#history-section` | Open positions (live P&L via SSE) + closed signals (equity curve, outcome tagging) |

**Shared detail panel** (`#detail-panel`): slides in from right (desktop) / up from bottom (mobile). Always write innerHTML to `#panel-body`, not the aside.

**State objects** — fully isolated, never cross-reference:
- `S` — Signals tab state
- `M` — Market tab state
- `H` — History tab state (open positions, price cache, closed signals)
- `A` — Strategies tab analytics state

---

## Key API Routes

| Route | Method | What |
|---|---|---|
| `/api/scan` | GET | Scans with one strategy; `?strategy=<key>&threshold=<n>` |
| `/api/scan/all` | POST | Fetches tickers once, runs all enabled strategies |
| `/api/market` | GET | All scored tickers for market browser |
| `/api/signal/<symbol>` | GET | Full enrichment of a single symbol on demand |
| `/api/signal/result` | PATCH | Tag outcome; accepts `exit_price` to compute `pnl_pct` |
| `/api/signals/history` | GET | Signal history with filters |
| `/api/signal/detail/<id>` | GET | Full trade detail + Claude coach review (closed signals) |
| `/api/outcomes/check` | POST | Auto-evaluate open positions against klines |
| `/api/stream/prices` | GET | SSE: price updates every 3s for `?symbols=` |
| `/api/strategies` | GET | All strategies with performance stats |
| `/api/strategies/analytics` | GET | Chart-ready analytics for Strategies tab |
| `/api/strategies/portfolio` | GET | Strategy Portfolio Lab simulator |
| `/api/risk-gates` | GET | Current risk gate config + historical impact |
| `/api/risk-gates/<key>` | PATCH | Change gate mode live (block/shadow/off) |
| `/api/strategies/custom` | POST | Create custom strategy |
| `/api/strategies/custom/<key>` | PATCH / DELETE | Edit or delete custom strategy |
| `/api/analysis` | POST | AI strategy review (last 200 tagged outcomes) |
| `/api/backfill/pnl` | POST | MAINTENANCE — re-evaluate historical signals |
| `/api/cleanup/phantom-events` | POST | MAINTENANCE — delete orphan position events |

Background thread `_outcome_loop` runs `api_outcomes_check()` every 15 minutes.

---

## Rules of the Build

1. **Ship before you plan.** Running code before the next feature.
2. **One file, one job.** `app.py` stays flat. `lib/` files are pure functions.
3. **No features that don't serve the trader.** If it doesn't help make a better trade decision, it doesn't ship.
4. **The mobile test is non-negotiable.** Every UI change must work on iPhone Safari.
5. **No committed secrets.** `.env` only. Never committed.
6. **Error handling is a feature.** Every API call is wrapped in try/except. App never crashes.
7. **Signal quality over quantity.** 20 high-conviction signals beats 200 weak ones.
8. **The tool is for trading, not for looking at.** Aesthetics serve the signal, not the other way around.
9. **S and M state objects are completely isolated.** Never share state between tabs.
10. **No JS frameworks.** Vanilla JS only.
11. **No glassmorphism, gradients, or drop shadows.** Dark flat UI only.
12. **Read the actual files before writing a single line.** Do not assume state from memory or prior sessions.
13. **No databases for application state.** SQLite for signal history and outcome tracking only.

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

## What NOT To Do

A condensed version of HANDOFF.md's full list — the most critical items:

- Do not call `enrich_signal()` from `backtest.py` — it makes live API calls
- Do not import from `app.py` in a way that triggers Flask server startup
- Do not add new SQLite columns without a migration — wrap in `try/except OperationalError`
- Do not use `datetime.now()` — always `datetime.utcnow()`; all timestamps are UTC ISO without Z
- Do not add JS frameworks — no React, Vue, jQuery, Alpine
- Do not write innerHTML to `$('detail-panel')` — write to `$('panel-body')` only
- Do not filter direction server-side in `/api/signals/history` — client-side only
- Do not commit `.env`, `data/`, or `__pycache__/`
- Do not modify `S` state from market tab code or `M` state from signals tab code
- Do not call any AI provider directly from routes — always use `call_ai()` from `lib/ai_client.py`
- Do not import `anthropic` at top of `app.py` — lazy import inside `lib/ai_client.py` handles it
- Do not add new strategies by editing only one place — metadata spans `app.py` and `index.html`
- Do not write TP/SL events to `position_events` without a prior `ENTRY_FILLED` event
- Do not place CoinGlass conviction adjustments in `score_ticker()` — they belong in `enrich_signal()`
- Do not promote `fragility_high`/`fragility_extreme` thresholds to hard gates without 2+ weeks of data
- Do not run `POST /api/backfill/pnl` from a browser — use `curl -X POST` from the VPS shell
- Do not let agents read raw exchange dicts directly — normalize through `lib/adapters` into `ExchangeContext` first
- Do not call LLM providers directly from agents — use `call_ai()` from `lib/ai_client.py` only
- Do not make MEXC or Hyperliquid API calls inside agents — use data passed from `enrich_signal()`
- Do not apply `agent_shadow_delta` to conviction in Phase 1
- Do not add SQLite columns for agent fields — Phase 1 output belongs in `signal_json`

---

## When Starting a New Session

1. Read this file (you just did)
2. Read HANDOFF.md — check the phase table and current task list
3. Read the relevant section of `app.py` or `index.html` before touching anything
4. Complete one task fully before moving to the next
5. Update HANDOFF.md session summary before ending the session

Do not start a new task until the previous one works end-to-end.
Do not assume anything about current state — read the actual files.
