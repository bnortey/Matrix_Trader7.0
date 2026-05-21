# Matrix Trader 7.0 — State of the System Report
*Generated 2026-05-15 for cross-AI continuity (OpenClaw handoff)*

---

## What It Is

Matrix Trader 7.0 is a personal, high-leverage crypto trading dashboard for **MEXC perpetual swap markets** (800+ pairs). It is a local Python/Flask web app with a single-file dark-theme frontend, running on a Mac and a Hetzner VPS. The user scans all perp tickers, receives ranked LONG/SHORT signals with conviction scores and 3-tier ATR-based entry/TP/SL ladders, reads an AI-generated 4-section trade brief, and executes manually on MEXC.

It is **not**: an execution bot (order placement is staged, gated behind safety checks), a price forecasting engine (no ARIMA, no ML price prediction), or a SaaS product.

---

## Architecture at a Glance

| Layer | What | Size |
|---|---|---|
| `app.py` | Entire Flask backend | 6,376 lines |
| `templates/index.html` | Entire frontend — HTML + CSS + JS, no framework | 8,023 lines |
| `lib/` | Pure utility functions (indicators, laddering, agents, AI client, exchange adapters, execution) | ~12 files |
| `edge_lab/` | Research-only candle labeling + factor engine | Separate DB, no live wiring |
| `/opt/mt-learner/` | VPS-only standalone learner service | Reads signals.db read-only |
| `data/signals.db` | SQLite signal history, outcomes, paper trades | ~1,148+ closed signals |

**Tech stack:** Python 3.11 / Flask / SQLite3 / vanilla JS / SSE for live prices / TradingView embedded charts. AI provider chain: Claude → GPT → Gemini → Groq (fallback). No JS framework. No CSS framework.

---

## Current Capabilities (All Shipped)

### Signal Pipeline
- **Stage 1** (kline-free, all 800+ tickers): momentum score, funding score, basis spread, volume multiplier → `conviction_base` 0–100
- **Stage 2** (top 30 only, 10 concurrent threads): RSI, EMA, ATR, trend score, 4h daily trend, order book depth imbalance, OKX L/S ratio + OI, optional CoinGlass OI/funding/liquidation context, 3-tier ATR ladder (entry/TP/SL), AI report
- **Kline depth gate**: skips pairs with < 50 1h or < 20 4h candles
- **Two exchanges**: MEXC (primary) and Hyperliquid (full parallel scan)

### Conviction Scoring Layer
- 8-analyst shadow agent pipeline (`lib/agents.py`) — **Phase 2 live**: `agent_shadow_delta` now applied to conviction. Tags: `agent_exchange`, `agent_regime`, `agent_blocked`, `agent_version` = v2-phase2-live
- Regime-aware counter-trend boost: LONG in bearish EMA structure +5–8 conviction (`regime_counter_long`); SHORT in bullish structure +5–8 (`regime_counter_short`) — derived from 5.7M candle factor engine analysis
- Extreme vol firebreak gate (default ON): drops all extreme-volatility signals before enrichment
- Risk gates: `long_vol_long` (block/shadow/off), `short_vol_short` (shadow default), high/extreme-vol circuit breakers
- Strategy-specific conviction floors: Balanced 65, Funding Arb 76, Momentum Breakout 55, Mean Reversion 65
- Learner-applied overrides: `data/strategy_overrides.json` can override `min_conviction` per strategy without touching code

### Strategies
Four built-in: **Balanced** (20x), **Funding Arb** (10x), **Momentum Breakout** (25x, currently paused — anti-correlated), **Mean Reversion** (15x). Full custom strategy CRUD (clone base, override weights/filters/leverage/conviction). Strategy lifecycle: pause/resume, direction lock, volatility allowlist, enable/disable.

### Signal Data Per Card
Each signal carries: price, 3-tier entries + TP + SL, conviction, RSI 1h, trend score, ATR%, volatility regime, funding rate, daily trend alignment, OKX L/S ratio, OI data, CoinGlass context (optional), signal_why (one-liner), AI report (4 sections: Setup / Structure / Invalidation / Risk), 30+ tags with hover tooltips.

### History & Outcome Tracking
- Every scan auto-logged to SQLite `signals` table
- Outcome tagging: WIN / LOSS / PARTIAL / EXPIRED / SKIPPED (manual or auto-evaluated)
- Auto-evaluation: Min15 klines check open positions every 15 min (`_outcome_loop` background thread)
- `pnl_pct` is leveraged P&L % — persisted at close time, drives all analytics
- PARTIAL blended exit price (TP1-then-stopped = 1/3 TP1 + 2/3 stop)
- Trade Journey: MAE, MFE, capture ratio, stop pressure, entry delay, path labels — computed at close, backfillable
- Coach review: short Claude AI review per closed signal, includes journey facts
- Outcomes checker endpoint: `/api/outcomes/check` — fixed Hyperliquid millisecond timestamp bug (year 58326 crash), now working

### Dashboard (6 Tabs)
1. **Signals** — strategy pills, exchange selector (MEXC/HL), filter bar, ranked signal cards, detail panel with TradingView chart, Trade Readiness checklist (P9), EXECUTE ON HYPERLIQUID button (P11)
2. **Market** — all 800+ tickers paginated, sortable, searchable, exchange-keyed cache
3. **Tools** — risk calculator, compound planner
4. **Strategies** — analytics page: strategy comparison table, equity curves, P&L distribution, regime breakdown, best/worst symbols, Bot Readiness panel, MEXC/HL account connection status, strategy explainer + editor
5. **History** — open positions (live P&L via SSE), closed signals (equity curve, outcome tagging, coach reviews). Both panels have exchange filter. Full-dataset stats via `/api/signals/stats` (no 100-row limit)
6. **Intelligence** — Shadow Validation status, Agent Findings, Strategy Proposals, Research Firm hypothesis briefs

### External Learner (VPS Only, `/opt/mt-learner/`)
Standalone Python service reads `signals.db` read-only. Four core jobs: feature weights (30 min), threshold optimization (2 hr), regime performance (6 hr), strategy proposals (24 hr). Two research jobs: journey hypothesis generation (6 hr), brief re-evaluation (24 hr). Outputs `pending.json` suggestions. MT7 reads output via `GET /api/intelligence/suggestions` — no direct import or HTTP dependency.

### Execution Layer (P8–P11 Shipped)
- **P8**: MEXC read-only account status (blocked by Akamai CDN from VPS, fails-closed gracefully; works from Mac)
- **P9**: Trade Readiness Panel — 5-item checklist (signal age, trend score, ATR, volatility regime, daily P&L) + READY/REVIEW/PASS verdict + 1%-risk position size recommendation. `lib/risk_controls.py` pure functions
- **P11**: Full Hyperliquid execution stack — `lib/hl_execution.py`: `place_limit_order`, `cancel_all_orders`, `close_all_positions`, `kill_switch`. EIP-712 signing via `eth_account`. Kill switch always available with keys. Order placement gated by `LIVE_TRADING_ENABLED=true`. Confirmation modal required before every order. KILL SWITCH button in dashboard header

### Paper Trading
Paper bot background thread (`_paper_bot_loop`) runs every 5 minutes: fetches tickers, runs all enabled strategies, deduplicates by symbol (highest conviction wins), checks flow confirmation, inserts to `paper_trades` table. Config managed via `data/paper_config.json`. `/api/paper/*` routes for trades list, stats, config PATCH, reset. `min_conviction` lowered to 55 on 2026-05-15 — trades should start logging.

### Edge Lab Research (Standalone, No Live Wiring)
Universal candle labeling engine: 5.7M rows × 4 path templates in `data/edge_lab.db`. Factor engine (`edge_lab/factor_engine.py`): analyzes 8 factor groups (volatility regime, trend state, compression, regime×trend cross, RSI decile, volume decile, tag presence, ATR decile). Wilson confidence intervals. Key finding: **market mean-reverts more than trends on 15m resolution** — counter-trend setups in aligned EMA structure outperform baseline by +3.9–5.6 edge_delta.

---

## Key Analytics Findings (1,148 signals)

From most recent `analyze.py` run (VPS):

| Finding | Detail |
|---|---|
| Total P&L | +1,182 (leveraged %) |
| Best strategy | Funding Arb (+971) |
| Worst strategy | Momentum Breakout (anti-correlated, paused) |
| Extreme volatility | Consistent money loser (-19.6 avg) |
| Conviction sweet spot | 65–74 (not higher) |
| Laddering vs TP1-only | Laddering beats TP1-only by +4,337 P&L |
| Top outcome predictor | `trend_score` (winners avg 9.3 vs losers 14.1 — lower is better) |
| Second predictor | `atr_pct` (winners 3.2% vs losers 5.5%) |
| Conviction as predictor | Barely separates outcomes — only 0.56 point divergence |

---

## Current Gaps and Issues

### Operational Issues (Active)

| Issue | Status | Detail |
|---|---|---|
| Anthropic API credits depleted on VPS | ⚠️ User action needed | Coach reviews / AI briefs falling back to Gemini/Groq. Top up at console.anthropic.com |
| HL_PRIVATE_KEY not in VPS .env | ⚠️ User action needed | Kill switch and order placement require it. P11 is built but not activatable |
| MEXC private API blocked on VPS | 🔴 External blocker | Akamai CDN blocks all requests from Hetzner IP. Bot Readiness panel stays grayed out. Works from Mac |
| Paper bot min_conviction was 65 | ✅ Fixed 2026-05-15 | Lowered to 55 — trades should start logging on next 5-min scan cycle |

### Signal Quality Gaps

| Gap | Detail |
|---|---|
| Conviction barely predicts outcomes | Learner confirmed only 0.56-point divergence between winners and losers. The scoring function needs richer inputs, not just a higher threshold |
| Stage 1 is kline-free | `conviction_base` derived only from ticker snapshot — no price structure, no trend. The ceiling is ~59 in current market conditions |
| Agent Phase 2 unverified | `agent_shadow_delta` now applied to conviction but no outcome data yet to confirm it helps. Need 50+ closed signals with agent data before trusting it |
| `regime_counter_long/short` tags unverified | Just deployed. Need 20–30 trades to confirm edge holds in live signals |
| Momentum Breakout paused | Anti-correlated direction calls. No current plan to fix — needs new directional logic |

### Architecture / Tech Debt

| Item | Detail |
|---|---|
| `app.py` at 6,376 lines | Still flat by design. Will need modularization before P12 (micro-live automation) to keep it maintainable |
| Agent LLM calls inside scan path | Each enriched signal triggers up to 8 LLM calls in Phase 2 agents. Scan time acceptable now (~7s on VPS) but will degrade as signal volume grows |
| `flow_confirm()` relies on tape/depth APIs | When MEXC tape or depth returns empty, defaults to score=50 (neutral). Flow confirmation is effectively disabled for low-liquidity altcoins |
| WebSocket client built but unused | `lib/mexc_stream.py` exists but SSE route uses a poll loop. WS would reduce latency and server load |
| No `paper_trades` auto-evaluation | Paper bot logs entries/exits but doesn't auto-evaluate outcomes against klines the way the main signal tracker does |
| VPS mt-learner: no TYPE 4/5 briefs yet | Journey data just became available (backfilled May 7). Need 10+ journey-aware trades before hypothesis generation fires |

### Execution Readiness Gaps (P11 Activation)

| Gate | State |
|---|---|
| `LIVE_TRADING_ENABLED=true` | NOT SET — user must add to VPS .env |
| `HL_PRIVATE_KEY` | NOT SET — user must add to VPS .env |
| Kill switch tested | NOT YET — can't test without private key |
| Paper simulation run | Paper bot just fixed 2026-05-15, hasn't logged trades yet |
| 50+ closed signals with agent data | 0 currently — Phase 2 agents deployed 2026-05-13 |

### Missing Features (Not Yet Built)

| Feature | Phase | Notes |
|---|---|---|
| True paper simulation (fill simulation, fee/funding modeling) | P10 | Current paper bot is entry/exit tracking without simulated order fill latency or fee drag |
| Micro-live automation | P12 | Requires P10 validation + proven strategy + consecutive loss shutdown |
| Execution audit log table (`execution_events`) | P11 | DB table planned but not yet created |
| `orders` table (actual order lifecycle + MEXC order IDs) | P11 | Planned but not yet created |
| `account_snapshots` / `live_positions` tables | P8+ | Planned for balance/margin tracking |
| Multi-exchange execution | Out of scope | Hyperliquid only for now; MEXC execution blocked by Akamai |
| TradingView Hyperliquid altcoin coverage | Partial | Many HL altcoins don't have TV charts — shows blank |
| New agent tags in TAG_META/TAG_TIPS | Minor | `squeeze_unconfirmed`, `long_momentum_reduced`, `short_bias_applied` added to scoring but UI tooltips not yet added |

---

## What's Working Well

- The **two-stage pipeline** (kline-free Stage 1 on all 800+ tickers, enrichment on top 30) is fast and robust — 7s scan on VPS
- **Laddering beats single TP by +4,337** on historical data — the ATR-based 3-tier exit is generating real edge
- **Funding Arb is the strongest strategy** (+971 P&L, most consistent)
- **Factor engine finding** (counter-trend > trend-following at 15m) is now wired into live signals via `regime_counter_long/short` tags
- **Outcome tracking and coach review pipeline** is solid — 1,148 signals, leveraged pnl_pct, blended PARTIAL, trade journey metrics
- **VPS deployment is stable** — matrix-trader + mt-learner both running as systemd services
- **Mobile-first UI** — all features work on iPhone Safari (no framework, no build step)
- **Execution safety rules are hardcoded** — kill switch, confirmation modal, `LIVE_TRADING_ENABLED` gate are immutable

---

## Immediate Next Actions (Priority Order)

1. **User: add `HL_PRIVATE_KEY` + `LIVE_TRADING_ENABLED=true` to VPS .env** — unlocks kill switch and P11 execution
2. **User: top up Anthropic credits** — restores Claude briefs and coach reviews from fallback chain
3. **Monitor paper bot** — should start logging trades at `min_conviction=55` (fixed 2026-05-15); confirm entries appear in `/api/paper/trades`
4. **Monitor Phase 2 agent delta** — do `agent_confirmed` signals win more? If win rate degrades in 30 trades, revert to shadow mode
5. **Monitor `regime_counter_long/short`** tags over next 20–30 trades
6. **Run `analyze.py` weekly** on VPS — watch Funding Arb edge, monitor agent impact

---

## Files an AI Co-Pilot Needs to Read First

```
HANDOFF.md                          ← authoritative phase/task state (always read this first)
CLAUDE.md                           ← rules, architecture, what NOT to do
app.py                              ← entire backend (6,376 lines) — read relevant section before touching
templates/index.html                ← entire frontend (8,023 lines) — read relevant section before touching
lib/                                ← pure utility functions; no Flask; no app.py imports
data/strategy_overrides.json        ← learner conviction overrides (if present)
data/risk_gates.json                ← live risk gate config
```
