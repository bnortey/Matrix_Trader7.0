# Matrix Trader 7.0 — Codex Context

> Read this file at the start of every session. It is the source of truth for what this project is, where it stands, and how to work on it.

---

## What This Is

**Matrix Trader 7.0** is a high-leverage crypto trading assistant for perpetual swap markets on MEXC and Hyperliquid.

It is a **local web application**: a Python Flask backend that serves a dark-themed HTML dashboard. You run it on your Mac with `python3 app.py` and open it in any browser — including on iPhone over local WiFi.

**It is not:**
- An execution bot yet — order placement is a staged future capability (P8–P12), currently disabled.
- A prediction engine (no ARIMA, no price forecasting)
- A SaaS product (local only for now)
- A blind multi-exchange aggregator (MEXC is primary; Hyperliquid is integrated through explicit exchange routing)

**The core loop:**
1. User opens the dashboard
2. Hits "Scan All Perps" — fetches all 800+ MEXC futures tickers via public API
3. Sees a ranked signal table (LONG/SHORT, conviction score, entry/TP/SL)
4. Clicks a signal → requests an AI-generated trade brief through `lib/ai_client.py`
5. Uses the risk calculator to size the position
6. Executes manually on MEXC

---

## Project History

This is the 7th iteration. Versions 2–6 all failed. The MT6 codebase (`Matrix_Trader_6_0/`) was analyzed and the failure modes are documented. Do not repeat them:

| MT6 Mistake | MT7 Rule |
|---|---|
| Matrix chat bot as delivery mechanism | Web app only |
| ARIMA price forecasting | No forecasting. Signals only. |
| Two competing TUI implementations | One interface: the web dashboard |
| Coinglass API key committed in plaintext | All keys in `.env`, never committed |
| 17 planning markdown files instead of code | Ship before you plan |
| God class `EnhancedTradingBot` (900+ lines) | `app.py` stays flat until Phase 2 |
| Multi-exchange as primary venues | MEXC is primary. Others are context. |
| Jumped to automation without validated edge | Paper bot (P10) before micro-live (P12). Bot Readiness panel tracks progress. You decide when the data is sufficient. |

---

## Phase Status
See HANDOFF.md for current phase status and task list. HANDOFF.md is the source of truth.

---

## File Structure

```
Matrix_Trader_7.0/
├── AGENTS.md              ← this file
├── README.md              ← for external users (write in Phase 4)
├── .gitignore
├── .env                   ← ANTHROPIC_API_KEY, MEXC_API_KEY (if needed)
├── requirements.txt
├── app.py                 ← entire Flask backend (keep flat, one file)
├── templates/
│   └── index.html         ← the full dashboard UI
├── static/
│   └── style.css
└── lib/                   ← ported MT6 components, cleaned up
    ├── agents.py          ← 8-analyst Phase 1 shadow agent layer
    ├── exchange_context.py ← canonical exchange-agnostic data contract
    ├── adapters/          ← exchange normalization registry
    │   ├── __init__.py
    │   ├── mexc.py
    │   └── hyperliquid.py
    ├── indicators.py      ← RSI, EMA, VWAP, ATR
    ├── laddering.py       ← generate_ladders(price, atr, tiers, direction)
    ├── hyperliquid_client.py ← Hyperliquid public scan + read-only account client
    └── mexc_stream.py     ← WebSocket wrapper
```

**Rules:**
- `app.py` is the backend. Everything lives here until Phase 2.
- `lib/` files are utilities only — no Flask routes, no API calls, pure functions.
- `lib/agents.py` may call `call_ai()` only; it must not call providers or exchange APIs directly.
- Exchange-specific schema handling belongs in `lib/adapters/*`; agents only read `ExchangeContext`.
- `templates/index.html` is the entire frontend. One file.
- No new files or folders without a specific reason.

---

## Tech Stack

- **Backend:** Python 3.11+ / Flask
- **Frontend:** Single HTML file, vanilla JS, dark theme
- **Data:** MEXC public contract API — no auth required for market data
- **AI layer:** Anthropic Codex API (for signal reports) — key in `.env`
- **WebSocket:** MEXC contract WS `wss://contract.mexc.com/edge`

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

Intervals: Min1, Min5, Min15, Min30, Min60, Hour4, Hour8, Day1
```

Response wrapper: `{ "success": true, "data": [...] }`

---

## Signal Scoring Logic

Each ticker gets a score from 0–100 and a direction (LONG/SHORT):

| Input | Weight | Notes |
|---|---|---|
| 24h price change | High | >5% strong momentum |
| Funding rate | High | Negative = short squeeze setup |
| Price vs fair price spread | Medium | Spread > 0 = longs paying |
| Orderbook imbalance | Medium | bid/ask depth ratio (10 levels) |
| Volume vs baseline | Low | relative, not absolute |
| Volatility regime (ATR%) | Modifier | scales leverage recommendation |

Conviction threshold: signals below 55 are filtered out by default.

---

## Key Functions to Know

### From lib/indicators.py
```python
vwap(df)           # Volume-weighted average price
ema(df, period)    # Exponential moving average
rsi(df, period=14) # Relative strength index
atr(df, period=14) # Average true range
```

### From lib/laddering.py
```python
generate_ladders(
    current_price,
    atr_value,
    tiers=3,
    direction="LONG",   # MT7 addition — MT6 only did long-side
    risk_reward=(1, 2)
) -> (entries, exits)
```

### From lib/mexc_stream.py
```python
MexcStreamAPI(on_kline, on_depth, on_funding)
.start(["kline.BTC_USDT.Min15", "depth.BTC_USDT"])
.stop()
```

---

## Rules of the Build

1. **Ship before you plan.** Running code before the next feature.
2. **One file, one job.** `app.py` stays flat. `lib/` files are pure functions.
3. **No features that don't serve the trader.** If it doesn't help make a better trade decision, it doesn't ship.
4. **The mobile test is non-negotiable.** Every UI change must work on iPhone Safari.
5. **No committed secrets.** `.env` only. `.env` is in `.gitignore` from day one.
6. **Error handling is a feature.** Every API call is wrapped in try/except. App never crashes.
7. **Signal quality over quantity.** 20 high-conviction signals beats 200 weak ones.
8. **The tool is for trading, not for looking at.** Aesthetics serve the signal, not the other way around.
9. **No databases for application state.** SQLite is acceptable for signal history logging and outcome tracking.

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

## What's Ported from MT6

These are the only MT6 components worth keeping. Everything else was discarded.

| Component | Source | Status |
|---|---|---|
| RSI, EMA, VWAP, ATR | `Matrix_Trader_6_0/strategies/indicators.py` | Port to `lib/indicators.py` |
| Laddering logic | `Matrix_Trader_6_0/strategies/laddering.py` | Port to `lib/laddering.py`, add short-side |
| WebSocket wrapper | `Matrix_Trader_6_0/mexc_stream_api.py` | Port to `lib/mexc_stream.py` |
| OB imbalance calc | `Matrix_Trader_6_0/enhanced_trading_bot.py` (lines ~2966–2977) | Inline in `app.py` scoring |
| Volatility regime | `Matrix_Trader_6_0/market_extras.py` | Inline in `app.py` scoring |
| Concurrent fetch pattern | `Matrix_Trader_6_0/enhanced_trading_bot.py` (ThreadPoolExecutor) | Already in `app.py` |

---

## Environment Variables

```bash
# .env
ANTHROPIC_API_KEY=<your_anthropic_key>     # required for AI signal reports
MEXC_API_KEY=                    # optional — only needed for private endpoints
MEXC_API_SECRET=                 # optional
HL_WALLET_ADDRESS=               # optional — Hyperliquid read-only account status
```

---

## Agent Shadow Layer

Phase 1 of the Matrix Trader agent system is shadow-first. The 8-analyst pipeline runs from `enrich_signal()` through `lib.agents.run_agent_pipeline()`, but agent conviction deltas are not applied to signal conviction yet. Outputs are stored in `signal_json` under fields such as:

```text
agent_exchange
agent_regime
agent_narrative_bull
agent_structural_bull
agent_version
agent_shadow_delta
agent_shadow_narrative_delta
agent_shadow_structural_delta
agent_shadow_disagreement
```

Agent tags are prefixed with `agent_shadow_`. Deterministic Risk Manager hard blocks can still reduce conviction by 30 and add `agent_blocked` because those are math/risk gates, not LLM judgement.

Phase 2 must not apply `agent_shadow_delta` until at least 50 closed forward-tested signals have agent data, positive shadow deltas beat baseline, negative shadow deltas underperform baseline, high disagreement correlates with worse outcomes, and scan time stays within 10 seconds of the pre-agent baseline.

What not to do:
- Do not let agents read raw MEXC or Hyperliquid dicts directly.
- Do not call LLM providers directly; use `call_ai()` from `lib/ai_client.py`.
- Do not make exchange API calls inside agents.
- Do not add SQLite columns for agent fields; keep Phase 1 output in `signal_json`.
- Do not apply `agent_shadow_delta` to conviction in Phase 1.

---

## Running the App

```bash
pip install -r requirements.txt
python3 app.py
```

Opens at `http://localhost:5000` on Mac.
Opens at `http://192.168.x.x:5000` on iPhone (same WiFi).

---

---

## When Starting a New Session

1. Read this file (you just did)
2. Check which P3 tasks are unchecked above
3. Look at the current state of `app.py` and `templates/index.html`
4. Pick the next unchecked task and complete it fully before moving to the next
5. Update the checkbox in this file when a task is done

Do not start a new task until the previous one works end-to-end.


<claude-mem-context>
# Memory Context

# [Matrix_Trader_7.0] recent context, 2026-05-07 3:34am EDT

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 50 obs (23,703t read) | 1,684,600t work | 99% savings

### Apr 30, 2026
S15 VPS DB statistics pull — signals issued, trades closed, P&L by strategy (Apr 30 at 5:50 PM)
S16 VPS DB stats pull — signals issued, trades closed, P&L by strategy; session wrapped with data synced locally (Apr 30 at 6:01 PM)
S14 Matrix Trader 7.0 — Fix Momentum Breakout invisible in strategy bar + deploy to VPS (Apr 30 at 6:01 PM)
### May 1, 2026
S17 VPS DB stats pull + risk_gates.json state check — confirmed Mean Reversion is active (not disabled), only Momentum Breakout is paused (May 1 at 11:48 AM)
S19 User selected option "1" — subagent-driven development approach to execute the Hyperliquid integration plan (May 1 at 11:49 AM)
### May 2, 2026
31 10:21p ✅ Implementation Plan File Created for Hyperliquid Integration
32 " 🟣 Complete Hyperliquid Integration Implementation Plan Written
S21 Double-check Codex's completed coding work in Matrix_Trader_7.0 — verify correctness of agent layer, adapters, performance fixes, and Research Firm feature (May 2 at 10:24 PM)
S18 MT7 — Hyperliquid Exchange Integration (Phase 1): Add Hyperliquid as a second exchange source with working scan + read-only account integration (May 2 at 10:24 PM)
33 11:25p 🟣 hyperliquid_client.py code quality fixes — interval validation and wallet guard added
34 " 🔵 Primary session stuck in restart loop — Task 1 re-dispatched multiple times, Tasks 2-10 not started
### May 3, 2026
35 11:36a 🟣 hyperliquid_client.py code quality fixes committed — commit 93deec0
36 12:19p 🟣 Matrix Trader 8-Analyst Agent Intelligence Layer — Phase 1 Shadow Mode
### May 4, 2026
37 12:46a 🔵 Pre-implementation state: agent files not yet created, toTVSymbol bug confirmed present
38 " 🟣 Exchange adapter layer created: lib/exchange_context.py, lib/adapters/__init__.py, lib/adapters/mexc.py, lib/adapters/hyperliquid.py
39 " 🟣 Phase 1 Agent Shadow Layer Deployed to Production
### May 5, 2026
40 1:55a ⚖️ Matrix Trader Intelligence Layer — Three-Phase Architecture Plan
41 1:56a 🔵 Agent Shadow Layer Files Confirmed Present on VPS
42 " 🔵 Agent Shadow Layer Live and Producing Data — v2-phase1-shadow
43 1:58a 🔵 TradingView Hyperliquid Fix Confirmed Deployed — No Agent Timeout Errors
44 1:59a 🔵 VPS Scan Time 14.4s — Exceeds Phase 2 Criterion Threshold
45 " 🔵 VPS Has No /opt/venv — Only System Python 3.12 Available
46 2:00a 🔵 Full analyze.py Audit: Strategy Performance, Blacklist Candidates, Direction-Flip Warnings
47 2:01a 🔵 VPS Audit Complete — Prompt 1 All 8 Checks Passed with One Flag
48 " 🔵 index.html Structure Mapped — switchTab() Not showTab(), State Objects Located
52 2:05a 🟣 Intelligence Tab Frontend Implemented in index.html
53 " 🔵 Subagent Ran Out of Usage Before Completing Intelligence Tab Tasks
49 2:08a ⚖️ Dimensional Atlas — Project Specification Defined
50 2:09a 🔵 Dimensional Atlas Project Directory Exists But Is Empty
51 2:10a 🟣 Dimensional Atlas — P0 Foundation Docs Created
### May 6, 2026
54 4:38p 🔵 app.py Scan Architecture Pre-Change Baseline
55 4:39p 🟣 AGENT_TOP_N = 10 Constant Added to app.py
56 " ⚖️ Matrix Trader: Scan Performance Optimization Strategy Defined
57 " 🟣 Research Firm: Deterministic Hypothesis Discovery Engine for mt-learner
58 " 🟣 Research Firm Integrated into mt-learner Scheduler and Matrix Trader API
59 " 🟣 Intelligence Tab: Research Firm Section Added as 4th Panel
60 4:46p 🟣 AGENT_TOP_N Constant Limits Agent Pipeline to Top 10 Signals
61 " 🟣 api_scan_all() Converted to Parallel Strategy Execution via ThreadPoolExecutor
62 " 🟣 5-Minute TTL Cache Added for Daily Kline API Calls
63 " 🟣 researcher.py Created: Deterministic Strategy Hypothesis Engine
64 " 🟣 Two New Learner Jobs: job_hypothesis (6hr) and job_brief_reeval (daily 04:00 UTC)
65 " 🟣 GET /api/intelligence/research Route Added to app.py
66 " 🟣 Research Firm Section Added as 4th Panel in Intelligence Tab
### May 7, 2026
67 1:15a 🟣 Multi-Exchange Adapter Layer and AI Agent System Added to Matrix Trader 7.0
68 1:16a 🟣 Research Firm UI and mt-learner Integration Added to Intelligence Tab
69 " 🟣 Agent Pipeline Gated to Top-N Signals with Scan Rank and Daily Kline Cache
70 " 🔵 lib/researcher.py Missing — Research Briefs Consumed Directly from mt-learner File Path
71 " 🔵 mt-learner External Service Architecture and Research Briefs Contract Confirmed in HANDOFF.md
72 1:17a 🔵 HANDOFF.md Current Task List Reveals Full Scope, Verification Results, and Performance Measurements
73 " 🔵 Research PDF Corpus and Uncommitted Working Tree State Identified
S20 Verify Codex completed the last bits of coding correctly in Matrix_Trader_7.0 (May 7 at 1:17 AM)
S22 Verify Codex work then identify what's next — P9 Execution Readiness Layer identified as next phase, brainstorm proposed (May 7 at 1:18 AM)
74 1:20a ⚖️ P9 Brainstorm Structured as 6-Step Design Workflow Before Any Code
75 1:21a 🔵 P9 Formal Description Confirmed in HANDOFF.md Phase Table
S23 P9 Execution Readiness Layer brainstorm — clarifying questions phase begun, first question posed to user about P9 output UX (May 7 at 1:21 AM)
76 1:46a 🔵 May 7 analyze.py Audit Reveals Three Critical Signal Selection Problems Destroying P&L
77 " ⚖️ Three Targeted score_ticker() Fixes Planned for Balanced Strategy Signal Quality
78 " 🔵 score_ticker() Structure Mapped — TAG_META/TAG_TIPS Dicts Not Found by Name in app.py
79 1:47a 🔵 TAG_TIPS and TAG_META Are JavaScript Dicts in index.html, Not Python Dicts in app.py
80 " 🔴 Fix 1 Applied: short_squeeze LONG Now Requires Positive Price Momentum Before Awarding Full Score

Access 1685k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>
