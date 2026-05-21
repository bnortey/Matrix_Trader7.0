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

# [Matrix_Trader_7.0] recent context, 2026-05-21 2:24pm EDT

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 50 obs (18,853t read) | 510,090t work | 96% savings

### May 19, 2026
249 10:19a 🔵 Live Balanced Strategy Scan Returns Zero Signals Across 884 Pairs
250 10:20a 🔵 Direction Lock Filtering Nearly All Signals — balanced_focus_short Blocking LONG Entries
251 " 🔵 enrich_signal Returns None for Top Candidate — Root Cause of Zero Paper Trades Found
252 " 🔵 enrich_signal Silently Returns None — Internal Failure Path Not Yet Isolated
253 10:21a 🔵 Root Cause Found: fetch_klines Returns None for RON_USDT — All enrich_signal Calls Fail
254 10:37a 🔵 fetch_klines Not Defined in app.py — Import Error Causing enrich_signal Failures
255 " 🔵 fetch_klines Lives in lib/exchange_data.py — Imported into app.py at Line 35
256 " 🔵 fetch_klines MEXC Path Calls fetch_mexc — Failure Traced to fetch_mexc Returning Non-Dict
257 " 🔵 MEXC Kline API Works With "Min60" But fetch_klines Uses "Hour1" — Interval Mismatch Bug
258 " 🔵 fetch_mexc Returns None When MEXC API Returns success=False — Interval "Hour1" Likely Invalid
259 " 🔵 MEXC Kline API Rejects "Hour1" Interval — _MEXC_INTERVAL Mapping Is Wrong
260 10:38a 🔵 MEXC Kline Interval Values Confirmed — Only "Hour1" Is Invalid
261 " 🔴 Fixed MEXC Kline Interval Mapping — "Hour1" → "Min60" in lib/exchange_data.py
262 10:39a 🔴 Deployed exchange_data.py Fix to Production VPS — Paper Bot Kline Fetch Now Working
263 " 🔴 Paper Bot Pipeline Fully Unblocked — Enriched Signals With ATR% Now Flowing
### May 20, 2026
264 11:57p 🔵 Matrix Trader 7.0 — Project State and Roadmap Discovery
265 " 🔵 Matrix Trader 7.0 — Full API Surface and External Learner Architecture
266 " 🔵 Matrix Trader 7.0 — Signal Schema, DB Columns, Journey Metrics, and P8+ Table Plan
267 " 🔵 Matrix Trader 7.0 — Phase Status: P9 Done, P10–P12 Pending, P11 Execution Layer Shipped
268 " 🔵 Matrix Trader 7.0 — Current Next Actions and Pending User Steps
269 " 🔵 0-Signal Bug Root Cause: ThreadPoolExecutor Overloading MEXC Kline Endpoint
### May 21, 2026
270 12:13a ⚖️ Matrix Trader Strategic Direction: Market Structure Intelligence Over Signal Generation
271 " ⚖️ Research Firm Persona Upgrade: Named Agents, Job Titles, Org Chart, and Daily/Weekly/Quarterly Reports
272 " ⚖️ New Signal Logging Fields Proposed: Market Structure Metadata for Backtesting
273 12:14a 🔵 lib/ Directory Contains bybit_client.py and mexc_client.py Not Previously Documented
274 " ⚖️ Multi-Exchange Architecture Standing Rule Codified in Project Memory
275 12:15a ✅ MEMORY.md Updated With Multi-Exchange Standing Rule Entry
276 12:21a 🔵 lib/agents.py Internal Structure: 8-Analyst Layer With NarrativeMarketState and StructuralMarketState Dataclasses
277 12:22a 🔵 agents.py: AgentOutput Dataclass, REGIME_WEIGHTS, LLM Availability Sentinel, and Pipeline Entry Point
278 " 🔵 All 8 Analyst Function Names Identified in agents.py for Persona Mapping
279 " 🔵 Intelligence Tab JS: loadIntelligence() Fetches 3 APIs, renderIntelligence() Builds All UI at Line 6578
280 12:23a 🔵 renderResearchFirm() Already Exists at Line 6782 — Research Firm Section Has Prior Implementation
281 " 🔵 renderResearchFirm() Brief Card Structure: 5 Confidence Levels, Progress Bar, Evidence Stats
282 " 🟣 Visual Companion Server Started, Layout Options Mockup Presented at localhost:52341
S88 Report structure v2 — significantly richer Daily Brief and Weekly Report mockup created with top movers table, explosive move autopsy, and "What's Coiling" forward-looking section (May 21 at 12:47 AM)
S89 Report structure v2 presented — richer Daily Brief with Top Movers, Explosive Move Autopsy, What's Coiling, and Weekly Move Patterns sections, awaiting user approval (May 21 at 12:54 AM)
S90 Report enhancement brainstorm — identified high-value additions to Daily Brief and Weekly Report, categorized by data availability and implementation cost (May 21 at 12:55 AM)
S91 Report structure v3 — full intelligence treatment with 11 sections implemented and served, awaiting user approval before spec writing (May 21 at 12:56 AM)
283 1:03a ⚖️ Report enhancements confirmed — incorporating Tier 1 + high-value sections into design before spec
S92 Additional weekly report sections proposed — paper desk performance, events calendar, agent spotlight, week ahead outlook — plus decision point on "The Firm" org chart mockup (May 21 at 1:03 AM)
S93 Data source mapping finalized — each report section mapped to its system data source, full context available for AI narrative calls, design phase complete and ready for spec writing (May 21 at 1:05 AM)
S94 Cipher Research Group spec written, committed, and Task 6 completed — ready for user review before implementation planning (May 21 at 1:07 AM)
284 1:08a ✅ Task 5 marked completed — design sections presentation phase done
285 " ✅ Spec writing phase started — docs/superpowers/specs/ directory created
286 1:10a 🟣 Cipher Research Group design spec written — comprehensive implementation reference document
287 " ✅ Spec updated — AI call strategy batched to minimize credit usage (2 calls daily, 3 weekly)
288 " ✅ Spec updated — Agent Spotlight rotation state persisted to spotlight_state.json (not gitignored)
289 1:11a 🟣 Cipher Research Group design spec committed to git — commit b52edac
S95 Deployment clarification — reports run on same Flask app (local + VPS), but real data only on VPS; user ready to move to implementation plan (May 21 at 1:11 AM)
S96 Public release architecture discussion — three data layers identified, Docker/SaaS path considered, mt-learner integration decision needed before implementation (May 21 at 1:14 AM)
S97 mt-learner architecture decision — keep separate service, bring into repo as learner/ directory, parallel task to research firm build (May 21 at 1:18 AM)
290 1:20a ✅ Task 7 started — writing implementation plan for Cipher Research Group feature
291 " 🔵 Existing /api/intelligence routes confirmed in app.py — new routes will extend this pattern
292 1:21a 🔵 Reference implementation for fail-closed mt-learner reads confirmed at app.py line 6001
293 " 🟣 Cipher Research Group Implementation Plan Created
294 12:39p 🟣 AGENT_ROSTER and FIRM_META added to lib/agents.py
295 " 🔴 Audit §02 fix: LLM silent-fallback ambiguity resolved in agent pipeline
296 12:48p 🔵 AGENT_ROSTER voice strings use mixed quote styles to avoid escaping
297 12:50p 🔵 Code quality review passed for lib/agents.py Task 1 changes
298 " ✅ Task 8 closed, Task 9 started in Matrix Trader 7.0 development pipeline

Access 510k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>
