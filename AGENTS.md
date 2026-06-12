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

# [Matrix_Trader_7.0] recent context, 2026-06-12 5:22pm EDT

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 50 obs (20,068t read) | 299,477t work | 93% savings

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
### Jun 4, 2026
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
### Jun 9, 2026
855 2:28p 🔵 Paper Trader History Missing — Investigation Started
856 2:29p 🔵 Local signals.db Has Only 2 Closed Paper Trades — Production Has 108
857 " 🔵 Production VPS Paper Trades Also Reset — Only 5 Closed Trades Remain (IDs 1178-1195)
858 2:30p 🔵 Production signals Table Has No created_at Column — Schema Difference from Expected
859 " 🔵 Production paper_trades Sequence at 1200 — ~1175 Historical Rows Were Deleted
860 " 🔵 Production VPS Running Two Services: matrix-trader and mt-learner
861 2:31p 🔵 Root Cause Confirmed: POST /api/paper/reset Called June 8 2026 16:58:45 — Wiped All Paper Trades
862 " 🔵 Reset Triggered by Automated Scanner/Browser Tool — Uninterpolated JS Template Literals in HTTP Requests
863 2:32p 🔵 position_events Table Intact with 3,048 Rows Dating Back to April 25 — Survived the paper_trades Reset
864 2:41p 🔵 Pasted Session History Reveals Paper Reset Was a Second Unintended Wipe — Post-Tightening Cohort Had 29 Trades Before June 8
865 " 🔵 HANDOFF.md Reveals Two Cohort Resets Before June 8 — Focus-Short Cohort Started June 7, Then Wiped June 8
867 " 🚨 Scanner IP 163.7.3.220 Identified as ByteDance/Byteplus Cloud Infrastructure in Jakarta
866 2:43p 🔵 Full Log Context Confirms External Security Scanner — Not gstack — Triggered the Reset
868 2:46p 🚨 APNIC RDAP Confirms Scanner IP as BYTEPLUS-SG Network — ByteDance Abuse Contact bd_abuse@bytedance.com
869 " 🚨 MT7_API_TOKEN Auth Exists in app.py But /api/paper/reset Does NOT Use It
### Jun 12, 2026
870 4:48p 🔵 Matrix Trader 7.0 — Current State and Next Priorities
871 " 🔵 Matrix Trader 7.0 — Detailed Current Task Queue and Uncommitted Changes
872 4:49p 🟣 Paper Reset Route Hardened with ALLOW_PAPER_RESET Gate, Auth, and Auto-Backup
873 4:51p 🔵 Hermes Advisory Memo 2026-06-12 — Live Account at 63.86% Drawdown, Two Regime Suppressions Approved
874 " 🔵 require_api_token() Called in api_paper_reset() But Function Does Not Exist in app.py
875 " 🔴 Added Missing require_api_token() Helper to app.py
876 " 🔵 Paper Trade History Was Wiped on June 8 by External Scanner Traffic Hitting Unprotected Reset Route
877 4:54p 🔵 HANDOFF.md Session Summary Patch Failed — Context Mismatch After Earlier Patch
878 4:55p 🔵 HANDOFF.md Session Summary Patch Context Bug — "---" Separator Between Anchors
879 " ✅ HANDOFF.md 2026-06-12 Session Summary Successfully Written
880 4:56p ✅ README.md Updated with MT7_API_TOKEN and ALLOW_PAPER_RESET Security Documentation
881 " 🔵 Post-Hardening Verification Passed — app.py Compiles Clean, Frontend Reset Button Confirmed Removed
882 " 🔵 Live Flask Test Confirms Paper Reset Returns 403 by Default
883 " 🔵 Second Gate Test Confirmed — ALLOW_PAPER_RESET=true Without MT7_API_TOKEN Returns 403
884 4:57p 🔵 Full Reset Gate Chain Verified — All Four Security Layers Confirmed Working
885 4:58p 🔵 Full Happy-Path Reset Test Timed Out at 30s — No Result Returned
886 5:00p 🔵 Happy-Path Reset Test Passed — Full Reset Executes Correctly With All Gates Satisfied
887 " 🔵 Pre-Commit State — 5 Files Modified, data/backups Empty (Test Cleanup Confirmed)
888 " ✅ HANDOFF.md Line Counts Corrected to Actual wc -l Values
889 5:03p ✅ Second Commit Staged — HANDOFF.md Line Count Correction After Initial Push
890 5:04p ✅ Committed "fix: harden paper reset route" — Commit 63aaf3d
891 5:05p ✅ Hardening Changes Deployed to Production VPS 207.148.66.39
892 5:06p ✅ matrix-trader Service Restarted on Production VPS — Hardening Live
893 " 🔵 Production Verification Passed — Paper Reset Blocked, Fresh Cohort Has 18 Closed Trades at 66.7% W+P
894 5:07p 🔵 Production Frontend Verification Confirmed — No Reset Button or Old Reset URL in Served HTML
895 5:08p 🔵 Production File Integrity Verified — Hardened app.py and index.html Confirmed on VPS Disk
896 5:09p ✅ Commit 63aaf3d Pushed to GitHub — Session Complete

Access 299k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>
