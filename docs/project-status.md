# Matrix Trader 7.0 — Project Status
*Last updated: 2026-04-23*

---

## What This Is

Matrix Trader 7.0 is a **local web app** for high-leverage crypto trading on MEXC perpetual swap markets. Python Flask backend + single-file dark-theme dashboard. You scan 800+ MEXC perp tickers, get ranked LONG/SHORT signals with entry/TP/SL ladders, view a 4-section AI trade brief, and execute manually. It is **not** an auto-trading bot, forecasting engine, or SaaS product.

**Stack:** Python 3.11 / Flask · Vanilla JS / HTML · SQLite · Anthropic Claude API · MEXC public API

**Run it:**
```bash
python3 app.py   # opens at http://localhost:8080
```

---

## Codebase Snapshot

| File | Lines | Role |
|---|---|---|
| `app.py` | ~1,576 | Entire Flask backend — routes, scoring, DB, AI |
| `templates/index.html` | ~3,464 | Entire frontend — HTML + CSS + JS (no framework) |
| `lib/indicators.py` | — | RSI, EMA, VWAP, ATR (pure functions) |
| `lib/laddering.py` | — | generate_ladders() — entry/TP/SL tiers |
| `lib/mexc_stream.py` | — | WebSocket client (built, not yet wired to UI) |
| `backtest.py` | — | Standalone backtest: 14 symbols × 4 strategies |
| `data/signals.db` | — | SQLite — auto-created at runtime, gitignored |

---

## What's Been Built (Complete)

### P0 — Foundation
- Flask app serving a dark-themed dashboard
- MEXC `/contract/ticker` scan: fetches and scores 800+ tickers
- Basic conviction scoring (0–100) with LONG/SHORT direction
- Web dashboard rendering ranked signal cards

### P1 — Enrichment & Tools
- Technical indicators: RSI (1h), ATR%, volatility regime, EMA trend score
- Entry/TP/SL laddering: 3-tier ATR-based ladders for both LONG and SHORT
- Market browser tab: paginated view of all 800+ tickers with sorting/search
- TradingView chart integration (15m / 1h / 4h timeframe toggle)
- Risk calculator: position sizer based on account size + leverage
- Compound planner: multi-trade compounding projections

### P2a — Strategy Registry
- 4 strategy profiles: **Balanced**, **Funding Arb**, **Momentum Breakout**, **Mean Reversion**
- Each strategy has unique scoring weights, conviction thresholds, and leverage caps
- Strategy pill UI in the signals tab; label updates on selection
- Strategy persists across scans via `S.strategy` state

### P2b — Signal Quality
- **Why-line**: one-sentence plain-English reason for every signal
- **Freshness dot**: visual age indicator (green → amber → red as signal ages)
- **Invalidation condition**: explicit "trade breaks if..." text per signal

### P2c — AI Trade Brief
- 4-section structured AI report via Claude API: Setup · Structure · Invalidation · Risk
- Strategy-aware prompt: different framing per strategy
- Report cached on signal; re-fetched only on demand
- Cost note shown in UI

### P2d — Market Sentiment
- OKX long/short ratio (live) for major pairs
- OKX open interest
- Binance/Bybit: gracefully fails on geo-blocked US IPs (returns None, no retry)
- `sentiment_tracked` boolean distinguishes tracked pairs from untracked altcoins

### P2e — Hardening & UX Polish
- 3-attempt retry with exponential backoff on all MEXC API calls
- Specific error messages when scan fails (not just "error")
- Volatility filter (any / low / medium / high+extreme)
- Min-volume filter (USD threshold input)
- Filter state persisted to localStorage (`mt7_filters`)
- First-run guide overlay (localStorage `mt7_guide_seen` hides on return)
- Strategy pill native tooltips
- Tag hover tooltips (27 tags with plain-English explanations)

### Backtest
- `backtest.py` — standalone script (not imported by app.py)
- 14 symbols × 4 strategies
- Sliding 100-candle window on real MEXC kline history
- Real MEXC funding rate history
- Conviction band breakdown (high/med/low signal zones)
- Results saved to `data/backtest_results.json`

### P3a — Signal History Storage
- SQLite DB auto-created at `data/signals.db` on startup (`init_db()`)
- Every scan auto-logs top signals (`log_signals()`)
- 30-minute duplicate guard (same symbol + strategy + direction)
- `PATCH /api/signal/result` — tag outcome: WIN / LOSS / PARTIAL / EXPIRED / SKIPPED
- `GET /api/signals/history` — returns history with filters: strategy, result, symbol, limit

### P3b — History Tab UI
- Summary stats bar: Logged · Tagged · Win Rate · W/L/P counts · Avg Conviction (wins vs losses)
- Full signal history table with outcome buttons (WIN / LOSS / PAR / EXP / SKP)
- Three filter selects: Strategy · Direction (client-side) · Result
- Auto-loads on tab switch; full reload after tagging to keep stats in sync

### P3c — AI Strategy Review
- "Strategy Review" button in History tab
- Sends last N tagged outcomes to Claude API (`POST /api/analysis`)
- Requires minimum 10 tagged outcomes to run
- Returns markdown analysis: which setups are working, which aren't, what to adjust
- Cost note shown (~$0.50–2.00/call)

### P3d — Open Positions Panel (Auto-Evaluation)
- Open positions section in History tab (untagged signals = still "open")
- `evaluate_outcome()` — checks Min15 kline history, auto-tags when TP or SL is hit
- `POST /api/outcomes/check` — background evaluation route
- Background thread (`_outcome_loop`) runs evaluation every 5 minutes automatically
- `GET /api/prices` — live price fetch for open positions panel (avoids full enrichment)
- Live price polling in UI: updates open positions with current price vs entry
- Auto-sync on history tab load picks up server-tagged positions

---

## What's Left to Build

### P3e — WebSocket Live Price Refresh *(next up)*
`lib/mexc_stream.py` exists and is complete but **not connected to the UI or any Flask route**.

What needs to happen:
- Wire `MexcStreamAPI` to a Flask route (SSE or WebSocket endpoint)
- Frontend subscribes to price updates for "watched" pairs (open positions + current signals)
- Prices update in real-time without polling
- This replaces/augments the current `GET /api/prices` polling approach

Effort estimate: medium — the library is done, needs plumbing + frontend event handling.

---

### P4 — Public Release
| Task | Notes |
|---|---|
| README.md (external-facing) | Currently minimal; needs full setup instructions for new users |
| GitHub publish | Repo is local; needs public push |
| 5 external beta testers | Recruit, distribute, collect feedback |
| .env.example | Document required env vars for new users |

---

## Phase Roadmap

| Phase | What | Status |
|---|---|---|
| P0 | Flask app, MEXC scan, basic scoring, web dashboard | ✅ Done |
| P1 | Indicators, entry/TP/SL, market browser, charts, risk calc, compound planner | ✅ Done |
| P2a | Strategy registry (Balanced / Funding Arb / Momentum / Mean Rev) | ✅ Done |
| P2b | Why-line, freshness dot, invalidation condition | ✅ Done |
| P2c | Template-based AI signal report (4-section trade brief) | ✅ Done |
| P2d | Market sentiment — OKX live, Binance/Bybit graceful fallback | ✅ Done |
| P2e | Retry logic, error messages, volatility/volume filters, localStorage | ✅ Done |
| UX | First-run guide, strategy tooltips, tag hover tooltips | ✅ Done |
| Backtest | backtest.py — 14 symbols × 4 strategies, real funding history | ✅ Done |
| P3a | SQLite signal history — auto-log, PATCH outcome, GET history | ✅ Done |
| P3b | History tab UI — summary bar, outcome buttons, filters, win rate | ✅ Done |
| P3c | AI strategy review — Claude API analysis of tagged outcomes | ✅ Done |
| P3d | Open positions panel — auto-evaluation, background thread, live prices | ✅ Done |
| P3e | WebSocket live price refresh for watched pairs | 🔲 Next |
| P4 | README, GitHub publish, 5 external beta testers | 🔲 Planned |

---

## Core Rules (Don't Break These)

1. `app.py` stays flat — one file, no class hierarchy until P4+
2. `templates/index.html` is the entire frontend — no build step, no framework
3. `lib/` files are pure functions — no Flask routes, no API calls
4. Mobile (375px iPhone Safari) is non-negotiable — every UI change must work
5. No committed secrets — `.env` only, always in `.gitignore`
6. No databases for app state — SQLite for signal history only
7. No JS frameworks — vanilla JS only
8. No glassmorphism, gradients, or drop shadows — flat dark UI only
9. `S` state (signals tab) and `M` state (market tab) are completely isolated
10. All timestamps use `datetime.utcnow()` — never `datetime.now()`

---

## Key API Routes

| Route | Method | What |
|---|---|---|
| `/` | GET | Dashboard |
| `/api/scan` | GET | Full scan — scores 800+ tickers, enriches top 30 |
| `/api/market` | GET | All scored tickers for market browser |
| `/api/signal/<symbol>` | GET | On-demand enrichment of a single symbol |
| `/api/signal/result` | PATCH | Tag a signal outcome (WIN/LOSS/PARTIAL/EXPIRED/SKIPPED) |
| `/api/signals/history` | GET | Signal history with filters |
| `/api/outcomes/check` | POST | Auto-evaluate open positions against kline history |
| `/api/prices` | GET | Live prices for open positions panel |
| `/api/analysis` | POST | AI strategy review via Claude API |

---

## Dashboard Tabs

| Tab | What |
|---|---|
| **Signals** | Ranked signal cards from last scan. Click for AI trade brief + chart. |
| **Market** | All 800+ tickers paginated. Search, sort, filter. Click for detail panel. |
| **Tools** | Risk calculator + compound planner. |
| **History** | Open positions (auto-evaluated) + closed signals (tagged). Strategy review button. |
