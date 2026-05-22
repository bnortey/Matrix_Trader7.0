# Matrix Trader 7.0 — Claude Code Context

> Read this file at the start of every session.
> **Phase status and task list live in HANDOFF.md — that is the source of truth.**
> This file covers orientation, architecture, rules, and what not to touch.
> Do not trust your memory of a prior session. Read the actual files.

---

## What This Is

**Matrix Trader 7.0** is a local web application for high-leverage crypto trading on MEXC and Hyperliquid perpetual swap markets. A Python Flask backend serves a single-file dark-themed dashboard. It runs on a Mac or VPS and is accessible from iPhone over local WiFi or via the VPS IP.

**The core loop:**
1. User hits "Scan All Perps" — fetches all 800+ MEXC tickers + Hyperliquid in parallel
2. Sees ranked LONG/SHORT signals with conviction scores, entry/TP/SL ladders
3. Clicks a signal → AI-generated trade brief + pre-entry readiness checklist
4. Tags outcomes (WIN / LOSS / PARTIAL / EXPIRED / SKIPPED) — auto-evaluation runs every 15 min
5. Reviews strategy analytics, equity curve, and per-trade coach reviews (Thomas Chen persona, first-person, MAE/MFE/funding alignment)
6. Reads daily/weekly Cipher Research Group intelligence reports (12 named analysts, domain-specific first-person notes)
7. Executes manually on MEXC or Hyperliquid (execution layer built, not yet activated)

**It is not:**
- Live trading yet — P11 execution layer is built (Hyperliquid, kill switch, order confirmation) but `LIVE_TRADING_ENABLED=false` is the master gate. Waiting on paper trading validation and `HL_PRIVATE_KEY` in VPS `.env`.
- A price forecasting engine (no ARIMA, no ML prediction)
- A SaaS product (local + one VPS for now)
- Able to use MEXC private endpoints from VPS — MEXC blocks Hetzner IPs. Public market data works; auth endpoints fail gracefully.

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
| Multi-exchange as primary venues | MEXC is primary. Hyperliquid is secondary. Others are context only. |
| Jumped to automation without validated edge | Paper bot before micro-live. Bot Readiness panel tracks progress. You decide when the data is sufficient. |

---

## Phase Status

See **HANDOFF.md** — that file is updated every session and is authoritative.

As of May 22, 2026:
`app.py` is 7,868 lines. `templates/index.html` is 8,478 lines. The app is live on a VPS at
`root@62.238.15.113`.

**Signal count (live SQLite as of May 22, 2026): 1,399 total**
- 307 WIN / 621 LOSS / 318 PARTIAL / 27 open
- 1,372 closed with terminal outcomes
- ~45% win+partial rate across closed signals

### Audit fixes applied 2026-05-15

External meta-analysis verified 13/14 claims accurate. Surgical fixes shipped:

1. **Outcome evaluator 75h→84h** — kline coverage now exceeds 80h EXPIRED threshold.
2. **Paper bot `min_flow_score`** — was loaded but never passed to `_flow_confirm()`. Now plumbed through.
3. **`/api/paper/config` override surfacing** — response includes `effective_thresholds[strategy_key]`.
4. **`llm_unavailable` flag in agents** — `_analyst_call` tags `_llm_ok`; pipeline sets `AgentOutput.llm_unavailable=True` when <2/8 analysts return parseable JSON.
5. **MEXC false `exchange_stress_notice`** — only fires on `|funding| > 0.002`, not routine settlement window.
6. **Hyperliquid `adl_risk`** — threshold raised from 0.001/hr to 0.005/hr.
7. **`SCORE_VERSION` env var** — v1 (legacy step) and v2 (continuous saturating ramp) live side-by-side.
8. **Bybit disabled in SUPPORTED_EXCHANGES** — no adapter exists; re-enable by building `lib/adapters/bybit.py`.
9. **`app.py` import-safe** — background thread `.start()` calls inside `__main__` guard.

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
├── app.py                 ← entire Flask backend — 7,868 lines; keep flat, one file
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
│   ├── reports/           ← cached daily/weekly Cipher intelligence reports (daily_YYYY-MM-DD.json)
│   └── backtest_results.json
└── lib/                   ← pure utility functions only; no Flask, no API calls
    ├── agents.py          ← 12-analyst Cipher Research Group + 8-analyst signal pipeline
    │                         AGENT_ROSTER and FIRM_META live here
    ├── ai_client.py       ← AI provider fallback chain; call_ai() is the only public function
    │                         Strips <think> blocks; tries all models per provider; Claude→Gemini→DeepSeek→Groq
    ├── exchange_context.py ← canonical exchange-agnostic data contract
    ├── adapters/          ← exchange normalization registry
    │   ├── __init__.py
    │   ├── mexc.py
    │   └── hyperliquid.py
    ├── indicators.py      ← RSI, EMA, VWAP, ATR, volatility_regime, daily_trend_direction
    ├── laddering.py       ← generate_ladders(price, atr, tiers, direction)
    ├── hl_execution.py    ← Hyperliquid execution: place_limit_order, kill_switch, get_positions
    ├── risk_controls.py   ← compute_daily_pnl, compute_position_size, get_readiness_verdict
    ├── mexc_stream.py     ← WebSocket client (built; not used by SSE route)
    ├── coinglass_client.py ← optional CoinGlass V4 client; fails closed if key missing
    └── hyperliquid_client.py ← Hyperliquid public scan + read-only account client
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
AI:         lib/ai_client.py — fallback chain: Claude → Gemini → DeepSeek → Groq
            Strips <think> blocks globally. Qwen3 on Groq has reasoning_effort="none".
            Tries all models per provider before moving to next provider.
Data:       MEXC public contract API (no auth for market data)
            Hyperliquid public API (parallel scan, same pipeline)
            OKX public API (L/S ratio, OI — geo-unrestricted)
            CoinGlass V4 (optional — requires COINGLASS_API_KEY in .env)
SSE:        /api/stream/prices — poll loop pushing prices every 3s
WebSocket:  lib/mexc_stream.py — built but not wired to any route
```

**Dependencies (all installed):**
```
flask, requests, pandas, numpy, websocket-client, python-dotenv,
anthropic, google-generativeai, openai, groq, eth-account, msgpack
```

---

## Environment Variables

```bash
# .env — never commit this file
ANTHROPIC_API_KEY=sk-ant-...     # AI trade briefs, coach reviews, strategy analysis
GEMINI_API_KEY=                   # fallback AI provider
GROQ_API_KEY=                     # fallback AI provider (free tier, Qwen3/Llama)
DEEPSEEK_API_KEY=                 # fallback AI provider (low cost)
MATRIX_PORT=8080                  # optional — defaults to 8080
MEXC_API_KEY=                     # optional — private endpoints blocked from Hetzner VPS
MEXC_API_SECRET=                  # optional
COINGLASS_API_KEY=                # optional — enables CoinGlass OI/liquidation enrichment
HL_WALLET_ADDRESS=                # optional — Hyperliquid read-only account status
HL_PRIVATE_KEY=                   # required for P11 live execution on Hyperliquid
LIVE_TRADING_ENABLED=false        # master gate — must be explicitly set to true to place orders
REPORT_NARRATIVE_MODE=deterministic  # deterministic | free | auto — controls Cipher report AI polish
SCORE_VERSION=v1                  # v1 (legacy step) | v2 (saturating ramp) — A/B scoring test
```

---

## Agent Layer — Cipher Research Group

Two distinct agent systems share `lib/agents.py`:

**Signal pipeline (8 analysts):** Runs from `enrich_signal()` through `run_agent_pipeline()`. Phase 2 is live — `agent_shadow_delta` is now applied to conviction. Tags are no longer shadow-prefixed. `agent_version = v2-phase2-live`. Deterministic Risk Manager hard blocks reduce conviction by 30 and add `agent_blocked`.

**Intelligence reports (12 analysts — AGENT_ROSTER):** Named personas with domains, voice strings, and exchange focus. Used by `_build_deterministic_report_narrative()` and `_call_report_ai()` to produce daily/weekly reports. Analysts: trader (Thomas Chen), risk_manager (Harper), regime (Nadia Reyes), funding (Kenny Zhao), microstructure (Niobe), cross_venue (Ghost), technical (Ryo Tanaka), sentiment (Zara Cole), tokenomics (Dr. Asha Mehta), narrative_debate, structural_debate.

**Report narrative system:**
- `_build_deterministic_report_narrative()` — free, always fires, first-person analyst voices
- `_call_report_ai()` — optional AI polish layer, controlled by `REPORT_NARRATIVE_MODE`
- Reports cached to `data/reports/daily_YYYY-MM-DD.json` and `weekly_YYYY-Www.json`
- Coach reviews pull daily report context for the trade date (fails silently if unavailable)

**Phase 2 monitoring criteria (do not revert without data):**
- Do agent_confirmed signals win more than baseline?
- Do high-disagreement signals lose more?
- Is scan time within 10 seconds of pre-agent baseline?

---

## Running the App

```bash
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY at minimum
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

| Input | Effect |
|---|---|
| `riseFallRate` (24h change) | Momentum score — strong tier >5%, weak tier >2% |
| `fundingRate` | Funding score — negative = squeeze setup |
| `fairPrice` vs `lastPrice` | Basis spread — premium = bearish lean, discount = bullish |
| `volume24` | Volume multiplier (strategy-defined) when vol > $10M |

Direction = whichever side accumulates more points. Ties go LONG.
Output: `conviction_base` (0–100), `direction`, `tags[]`, base signal dict.

**Stage 2 — `enrich_signal()` — top 30 signals only, runs in 10 concurrent threads**

Adds: 1h klines (RSI, EMA, ATR, trend score), 4h klines (daily trend), order book depth (imbalance), funding rate, market sentiment (OKX L/S, OKX OI), CoinGlass OI/liquidation context (optional), ladders (3-tier ATR-based entry/TP/SL), signal_why, ai_report, agent pipeline (Phase 2 live).

Gate: pairs with < 50 1h candles or < 20 4h candles are skipped.

**Risk gates — applied after Stage 2, before `log_signals()`**

Two live gates in `data/risk_gates.json`, each with `block` / `shadow` / `off` mode:
- `long_vol_long`: high/extreme-ATR LONG circuit breaker (default: `block`)
- `short_vol_short`: extreme-ATR SHORT circuit breaker, Balanced only (default: `shadow`)

Blocked signals are dropped. Shadow signals pass through tagged `*_vol_shadow`. Both log to `filtered_candidates` table.

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

Custom strategies persist in `custom_strategies` SQLite table.

**Do not add a new strategy by editing only one place.** Metadata spans:
- `STRATEGIES` and `_STRATEGY_NAME_TO_KEY` in `app.py`
- `STRAT_META` and `STRATEGY_LEVERAGE` in `index.html`

---

## Database Schema — Key Tables

**`signals`** — one row per logged signal
Key columns: `symbol`, `direction`, `strategy`, `conviction`, `price`, `entry1–3`, `tp1–3`, `stop_loss`, `atr_pct`, `volatility`, `funding_rate`, `rsi_1h`, `trend_score`, `tags`, `signal_why`, `result`, `exit_price`, `entry_at`, `pnl_pct`, `leverage`, `data_quality`, `signal_json`, `evaluation_version`, `strategy_key`

`signal_json` stores: agent outputs, coach_review, coach_review_at, ai_report, ladder data.

**`position_events`** — incremental trade lifecycle ledger
Event types: `ENTRY_FILLED`, `TP1_HIT`, `TP2_HIT`, `TP3_HIT`, `STOP_HIT`, manual close events.

**`custom_strategies`** — user-created strategy clones

**`filtered_candidates`** — signals blocked or shadowed by risk gates

**`paper_trades`** — paper bot simulated trade lifecycle

---

## Dashboard — Six Tabs

| Tab | Section | What It Does |
|---|---|---|
| Signals | `#signals-section` | Strategy bar, filter bar, ranked signal cards, detail panel |
| Market | `#market-section` | All 800+ tickers paginated, sortable, searchable |
| Tools | `#tools-section` | Risk calculator, compound planner |
| Strategies | `#strategies-section` | Analytics: equity curves, regime breakdown, symbol performance, Portfolio Lab |
| History | `#history-section` | Open positions (live P&L via SSE) + closed signals (equity curve, outcome tagging, coach reviews) |
| Intelligence | `#intelligence-section` | The Firm (analyst roster), Shadow Validation, Edge Lab, daily/weekly Cipher reports |

**Shared detail panel** (`#detail-panel`): slides in from right (desktop) / up from bottom (mobile). Always write innerHTML to `#panel-body`, not the aside.

**State objects** — fully isolated, never cross-reference:
- `S` — Signals tab state
- `M` — Market tab state
- `H` — History tab state
- `A` — Strategies tab analytics state
- `I` — Intelligence tab state (reportDate, reportWeek, reportCache)

---

## Key API Routes

| Route | Method | What |
|---|---|---|
| `/api/scan` | GET | Scans with one strategy |
| `/api/scan/all` | POST | Fetches tickers once, runs all enabled strategies |
| `/api/market` | GET | All scored tickers for market browser |
| `/api/signal/<symbol>` | GET | Full enrichment of a single symbol on demand |
| `/api/signal/result` | PATCH | Tag outcome; accepts `exit_price` to compute `pnl_pct` |
| `/api/signals/history` | GET | Signal history with filters |
| `/api/signal/detail/<id>` | GET | Full trade detail + coach review (closed signals) |
| `/api/signal/detail/<id>/regenerate-review` | POST | Clear cached coach review; regenerates on next load |
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
| `/api/paper/config` | GET / PATCH | Paper bot config |
| `/api/paper/stats` | GET | Paper bot aggregate stats |
| `/api/paper/filter-stats` | GET | Live winner/loser ATR% and trend_score averages from signals DB |
| `/api/paper/trades` | GET | Paper trade history |
| `/api/intelligence/roster` | GET | Cipher Research Group analyst roster |
| `/api/intelligence/reports/daily` | GET | Daily intelligence report (cached) |
| `/api/intelligence/reports/weekly` | GET | Weekly intelligence report (cached) |
| `/api/intelligence/reports/regenerate` | POST | Force regenerate a cached report |
| `/api/execution/status` | GET | Hyperliquid execution readiness |
| `/api/execution/place` | POST | Place limit order (gated by LIVE_TRADING_ENABLED) |
| `/api/execution/kill-switch` | POST | Cancel all orders + close all positions |
| `/api/account/daily-pnl` | GET | Today's realized P&L from signals DB |
| `/api/backfill/pnl` | POST | MAINTENANCE — re-evaluate historical signals |
| `/api/cleanup/phantom-events` | POST | MAINTENANCE — delete orphan position events |

Background threads: `_outcome_loop` (15 min), `_snapshot_loop`, `_coach_review_loop` (10 min, 5 trades/batch), `_paper_bot_loop`.

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
- Do not place CoinGlass conviction adjustments in `score_ticker()` — they belong in `enrich_signal()`
- Do not promote `fragility_high`/`fragility_extreme` thresholds to hard gates without 2+ weeks of data
- Do not run `POST /api/backfill/pnl` from a browser — use `curl -X POST` from the VPS shell
- Do not let agents read raw exchange dicts — normalize through `lib/adapters` into `ExchangeContext` first
- Do not call LLM providers directly from agents — use `call_ai()` from `lib/ai_client.py` only
- Do not make MEXC or Hyperliquid API calls inside agents — use data passed from `enrich_signal()`
- Do not add SQLite columns for agent fields — agent output belongs in `signal_json`
- Do not hardcode AI provider names in routes — always go through `call_ai()`
- Do not cache coach reviews that contain `<think>` blocks or preamble text — if found, clear via regenerate route

---

## When Starting a New Session

1. Read this file (you just did)
2. Read HANDOFF.md — check the phase table and current task list
3. Read the relevant section of `app.py` or `index.html` before touching anything
4. Complete one task fully before moving to the next
5. Update HANDOFF.md session summary before ending the session

Do not start a new task until the previous one works end-to-end.
Do not assume anything about current state — read the actual files.
