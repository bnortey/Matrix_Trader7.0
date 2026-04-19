# Matrix Trader 7.0 — Claude Code Context

> Read this file at the start of every session. It is the source of truth for what this project is, where it stands, and how to work on it.

---

## What This Is

**Matrix Trader 7.0** is a high-leverage crypto trading assistant for perpetual swap markets on MEXC.

It is a **local web application**: a Python Flask backend that serves a dark-themed HTML dashboard. You run it on your Mac with `python3 app.py` and open it in any browser — including on iPhone over local WiFi.

**It is not:**
- An auto-trading bot (no order execution)
- A prediction engine (no ARIMA, no price forecasting)
- A SaaS product (local only for now)
- A multi-exchange aggregator (MEXC-first; other exchanges are context only)

**The core loop:**
1. User opens the dashboard
2. Hits "Scan All Perps" — fetches all 800+ MEXC futures tickers via public API
3. Sees a ranked signal table (LONG/SHORT, conviction score, entry/TP/SL)
4. Clicks a signal → requests an AI-generated trade brief (Claude API)
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

---

## Current Phase: P1 — Signal Quality

**Phase 0 is complete.** The app runs, fetches real MEXC data, and renders a signal table.

**P1 exit criterion:** Every signal card shows a specific entry zone, TP levels, and stop loss derived from ATR. The risk calculator is functional. The compound planner renders Monte Carlo output.

### P1 Tasks (work through these in order)

- [ ] Port `lib/indicators.py` — RSI, EMA, VWAP, ATR from MT6 (clean pandas, no ARIMA)
- [ ] Port `lib/laddering.py` — ATR-derived tiered entry/TP/SL generator
- [ ] Wire indicators into per-signal scoring in `app.py`
- [ ] Add entry/TP/SL to every signal card in the dashboard
- [x] Complete risk calculator (position sizing, max leverage, drawdown analysis)
- [x] Complete compound planner (Monte Carlo, milestone projections)
- [ ] Port `lib/mexc_stream.py` — WebSocket for live price refresh on watched pairs

---

## File Structure

```
Matrix_Trader_7.0/
├── CLAUDE.md              ← this file
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
    ├── indicators.py      ← RSI, EMA, VWAP, ATR
    ├── laddering.py       ← generate_ladders(price, atr, tiers, direction)
    └── mexc_stream.py     ← WebSocket wrapper
```

**Rules:**
- `app.py` is the backend. Everything lives here until Phase 2.
- `lib/` files are utilities only — no Flask routes, no API calls, pure functions.
- `templates/index.html` is the entire frontend. One file.
- No new files or folders without a specific reason.

---

## Tech Stack

- **Backend:** Python 3.11+ / Flask
- **Frontend:** Single HTML file, vanilla JS, dark theme
- **Data:** MEXC public contract API — no auth required for market data
- **AI layer:** Anthropic Claude API (for signal reports) — key in `.env`
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
ANTHROPIC_API_KEY=sk-ant-...     # required for AI signal reports
MEXC_API_KEY=                    # optional — only needed for private endpoints
MEXC_API_SECRET=                 # optional
```

---

## Running the App

```bash
pip install -r requirements.txt
python3 app.py
```

Opens at `http://localhost:5000` on Mac.
Opens at `http://192.168.x.x:5000` on iPhone (same WiFi).

---

## Phase Roadmap (Summary)

| Phase | What | Status |
|---|---|---|
| P0 | Flask app running, MEXC ticker scan, basic scoring, web dashboard | ✅ Done |
| P1 | Indicators integrated, entry/TP/SL on signals, risk calc, compound planner | 🔄 Current |
| P2 | AI signal report, filtering, one-command installer, error hardening | Planned |
| P3 | 2-week internal alpha in live trading, signal history, outcome tracking | Planned |
| P4 | README, GitHub publish, 5 external beta testers | Planned |

---

## When Starting a New Session

1. Read this file (you just did)
2. Check which P1 tasks are unchecked above
3. Look at the current state of `app.py` and `templates/index.html`
4. Pick the next unchecked task and complete it fully before moving to the next
5. Update the checkbox in this file when a task is done

Do not start a new task until the previous one works end-to-end.
