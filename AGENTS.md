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

# [Matrix_Trader_7.0] recent context, 2026-06-07 10:16am EDT

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 50 obs (20,926t read) | 677,266t work | 97% savings

### May 30, 2026
S234 Add evaluation progress display and fix missing Expected value for learner suggestions — design decisions finalized, awaiting go-ahead to implement (May 30 at 10:16 PM)
### May 31, 2026
S235 Add evaluation progress display and fix missing Expected value for learner suggestions — diagnosis complete, design finalized, ready to implement (May 31 at 12:10 AM)
S232 Add evaluation progress display and fix missing Expected value for learner suggestions during evaluation review (May 31 at 12:10 AM)
S233 Add evaluation progress display and fix missing Expected value for learner suggestions — clarifying design decisions with user before implementation (May 31 at 12:10 AM)
S236 Add evaluation progress display and fix missing Expected value for learner suggestions — user confirmed to proceed, implementation starting (May 31 at 12:10 AM)
S237 Checking if per-strategy consecutive loss cooldown feature is already implemented in Matrix Trader 7.0 (May 31 at 12:13 AM)
S238 Audit paper bot config panel for missing loss streak cooldown UI controls (May 31 at 12:22 AM)
S239 User confirmed satisfaction ("great") with completed Safety Controls feature on paper config panel (May 31 at 12:28 AM)
S240 Project status check — where are we at with Matrix Trader 7.0 (May 31 at 12:31 AM)
### Jun 1, 2026
805 10:00a 🔵 MT7 Current Task Priority List from HANDOFF.md
806 10:01a 🔵 Production Paper Bot Live Stats — 108 Closed Trades, W+P 47.2%, Net +$106.48
807 " 🟣 Complete Diff of Footprint/Order Flow Chart Overlay Changes in templates/index.html
808 " 🔵 Production Server Already Has the Footprint/Order Flow Fix Deployed
809 10:02a 🔵 Production renderPrimaryOrderFlowView Has OLD Bug — Still Replaces Chart for All Three Modes
810 " 🔴 Footprint/Order Flow Chart Overlay Fix Deployed to Production
811 " 🔴 Production Patch Verified — Chart Overlay CSS and JS Both Confirmed in Place
812 10:03a 🔵 Production Still Has Old renderPrimaryOrderFlowView — Patch Did Not Fix the Core Bug
813 " 🔵 mt-learner Active Suggestion: Suppress balanced Strategy in low_liquidity Regime
814 " 🔵 Diff Shows Only One Line Different Between Production and Local — Safe to rsync
815 " 🔴 Chart Overlay Fix Fully Deployed to Production — renderPrimaryOrderFlowView Confirmed Fixed
816 " 🔵 Live Production Browser Test Failed — Footprint Button Not Found on 207.148.66.39
817 10:04a 🔵 Production Trade ID 2 Not Found — Production DB Has Different Trade IDs Than Local
818 10:05a 🔵 Production Paper Trades Have IDs 700+ — Bot is Actively Trading Multiple Symbols
819 " 🔴 Chart Overlay Fix Fully Verified on Production — Footprint and Order Flow Both Pass
820 " ✅ HANDOFF.md Updated with 2026-06-01 Session Summary and Revised Priority Queue
821 10:06a ✅ HANDOFF.md Paper Gate Stats Updated to Current 108-Trade Numbers
822 " 🔵 Paper Bot Gate Evaluation: 108 Trades, W+P 47.2% — Below P12 Threshold
823 " 🔵 SSH Direct to Production Blocked — "Operation Not Permitted" Error
824 " ✅ HANDOFF.md Significantly Updated with Current Project State
827 " 🔵 paper_trades Table Missing pnl_usd Column — Schema Differs from API Output
825 10:10a 🔵 Production VPS Missing sqlite3 CLI — DB Queries Must Use Python
826 " 🔵 Paper Bot Configuration: paper_config.json Full State Captured
828 10:14a 🔵 Full SQLite Schema for paper_trades and signals Tables Captured
829 " 🔵 Deep Paper Bot Analysis: LONG Direction and funding_arb Strategy Are Primary Drag on W+P
830 10:16a 🔵 Custom Strategy System Supports direction_lock and blocked_agent_regimes — Actionable Fix Path for Paper Bot
831 " 🔵 Live Paper Bot State: 5 Open/Pending Positions, Recent 20 Trades Show ORBS_USDT Double-Loss Pattern
832 10:17a 🔵 PATCH /api/paper/config Returns Empty Response — Strategy Disable Attempt May Have Failed
833 " 🔵 Production Service Had 5-Second Crash on 2026-06-01 at 14:03:56 — Likely Import or Startup Error
834 " 🔵 Paper Config Confirmed: PATCH from External IP Failed, disabled_strategies Still Only Has 2 Entries
835 " 🔵 Effective Conviction Thresholds: funding_arb Floor at 69, Lower Than Strategy Default of 76
836 10:18a ✅ Paper Bot Strategy Filter Tightened: funding_arb and momentum_breakout Now Disabled
837 " ✅ HANDOFF.md Updated with Paper Gate Analysis Session Summary
### Jun 2, 2026
838 11:17a 🔵 Matrix Trader 7.0 Paper EV Deep Analysis — Session Initiated
839 11:18a 🔵 Paper Bot Has ALL Strategies Disabled — EV Sample Critically Thin
840 " 🔵 Paper Bot Architecture: Entry Touch, Exit Evaluation, Safety Controls
841 " 🔵 Production Paper Bot Has 116 Closed Trades — Key EV Breakdown by Strategy/Direction
842 " 🔵 Flow Score and Trend Score Are Strong Paper EV Predictors — Agent Regime Also Material
### Jun 4, 2026
843 10:36a 🔵 Matrix Trader 7.0 Current Project State
844 10:37a 🔵 MT7 Paper Bot P12 Gate Status: Below Threshold, Post-Tightening Cohort Active
845 " 🔵 MT7 Working Tree Has Significant Uncommitted Changes Including New Edge Lab and MT-Learner Files
846 " 🔵 Production Paper Bot Live Stats 2026-06-04: 133 Total Closed, Post-Tightening Cohort Negative EV
847 10:38a 🔵 Production Paper Bot Has 2 Open Underwater SHORT Positions and 1 Pending (Flow Not Confirmed)
848 " 🔵 MT-Learner Active With New Pending Suggestion: Block Choppy Agent Regime
### Jun 6, 2026
849 1:45p 🔵 Matrix Trader 7.0 — Full Project State as of 2026-06-06
850 2:00p 🔵 mt-learner Service Health Confirmed — Top Features Stable
851 2:01p 🔵 mt-learner job_regime Completes in 0.0s — Regime Analysis Effectively a No-Op
852 " 🔵 mt-learner Stuck at 3 Suggestions — Proposal and Regime Jobs Producing Nothing New
853 " 🔵 paper_trades Schema — Full Column List Confirmed on Production
854 " 🔵 Post-Tightening Paper Cohort Stats — funding_arb_focus_short Showing Strong Edge
S241 SSH check of mt-learner status and post-tightening paper cohort analysis (Jun 6 at 2:02 PM)
**Investigated**: mt-learner service health via systemctl and log inspection; full learner.log searched for suggestion/regime/proposal job activity; paper_trades schema confirmed; post-tightening paper cohort stats queried directly from production signals.db

**Learned**: mt-learner is healthy and running — job_proposal fires daily at 03:26 UTC and explicitly logs "0 new suggestions, 3 total" (confirmed June 5 and June 6). job_regime completes in 0.0s with no output. The suggestion freeze is correct behavior, not a bug. There is one unreviewed pending suggestion: regime_funding_arb_choppy_20260529_001 (suppress funding_arb in choppy regime). Post-tightening cohort (since Jun 1): funding_arb_focus_short is the only positive-EV strategy at 28 trades, W+P 50%, avg net +10.56%. mean_reversion is badly negative at -20.15% avg over 7 trades. Disabled strategies (balanced, funding_arb base, momentum_breakout) leaked a few residual trades from before the config change.

**Completed**: Read-only investigation completed — no code changes. Full paper cohort analysis surfaced actionable signal: funding_arb_focus_short is approaching P12 gate criteria; mean_reversion should likely be disabled; one unreviewed learner suggestion exists.

**Next Steps**: User was asked whether to: (1) investigate why disabled strategies are still leaking through into paper trades, and/or (2) review the pending choppy-regime suppression suggestion for funding_arb. Session is paused awaiting user direction.


Access 677k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>
