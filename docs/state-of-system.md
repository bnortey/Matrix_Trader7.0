# Matrix Trader 7.0 — State of the System Report
*Updated 2026-05-23 after Vultr migration and MEXC private API repair*

---

## What It Is

Matrix Trader 7.0 is a personal, high-leverage crypto trading dashboard for MEXC perpetual swap markets, with Hyperliquid integrated as an explicit secondary venue. It is a Python/Flask web app with a single-file dark dashboard, running locally on the Mac and in production on a Vultr Singapore VPS.

The user scans all MEXC perps, receives ranked LONG/SHORT signals with conviction scores and ATR-based ladders, reviews AI/Cipher intelligence, tracks paper trades and outcomes, and still keeps live execution gated behind explicit safety controls.

It is not a blind execution bot, not a price forecasting engine, and not a SaaS product.

---

## Production Runtime

| Item | Current value |
|---|---|
| Production host | Vultr Singapore |
| Production IP | `207.148.66.39` |
| App URL | `http://207.148.66.39:8080` |
| App directory | `/opt/matrix-trader` |
| Learner directory | `/opt/mt-learner` |
| SSH | `root@207.148.66.39`, key auth only |
| Old host | `62.238.15.113` Hetzner, legacy only |

Services active on Vultr:

| Service | Purpose |
|---|---|
| `matrix-trader.service` | Flask dashboard/API on port 8080 |
| `mt-learner.service` | External learner reading `signals.db` read-only |
| `edge-lab-lite.timer` | Weekly bounded Edge Lab refresh |

---

## Architecture At A Glance

| Layer | What | Current size/state |
|---|---|---|
| `app.py` | Entire Flask backend | 8,503 lines |
| `templates/index.html` | Entire frontend: HTML + CSS + JS | 8,796 lines |
| `lib/` | Pure helpers: indicators, agents, AI, exchange adapters, execution clients | Utility-only |
| `data/signals.db` | Runtime signal/outcome/paper data | Server-side, gitignored |
| `/opt/mt-learner/` | External learner | VPS-only service |
| Edge Lab Lite | Research-only candle/factor engine | Weekly bounded systemd timer |

Tech stack: Python 3.11 / Flask / SQLite3 / vanilla JS / SSE / TradingView embeds. AI provider chain uses the configured fallback providers through `lib/ai_client.py`.

---

## Current Capabilities

### Signal Pipeline
- Stage 1 scans the full MEXC universe without klines.
- Stage 2 enriches top candidates with RSI, EMA, ATR, trend score, order book depth, funding, optional derivatives context, and ATR ladders.
- Kline-depth gates skip thin history pairs.
- MEXC is primary. Hyperliquid scans and read-only account status remain integrated separately.

### Intelligence And Learner Loop
- Cipher Research Group generates daily/weekly market-facing intelligence.
- The AI narrative prompt was fixed so free-tier models receive empty JSON values plus analyst/domain guidance, preventing voice-template echo.
- The self-improving loop A+B work is implemented: goals, suggestions API, suggestion review flow, and Strategy/Intelligence UI integration.
- mt-learner runs as a separate service and reads `signals.db` read-only.

### Paper Trading
- Paper bot is running and writing `paper_trades`.
- `atr_pct` and `trend_score` are now stored on new paper entries, making volatility-gate audits possible.
- Stale `disabled_strategies: ["0"]` artifact was cleared on Vultr.
- Paper stats after migration showed 5 open trades, 34 closed trades, and roughly +43 P&L.

### MEXC Private Read-Only
- MEXC private API works from Vultr after moving off Hetzner and using a subaccount key whitelisted to `207.148.66.39`.
- `lib/mexc_private.py` uses the working contract private endpoints:
  - `/private/account/assets`
  - `/private/position/open_positions`
- MT7 wrappers are connected:
  - `/api/account/status`
  - `/api/account/balance`
  - `/api/account/positions`
- Current account status is connected but unfunded: USDT equity/balance `0.0`, open positions `0`.

### Edge Lab Lite
- Edge Lab is research-only market memory. It is not Hermes, not live signal scoring, and not execution logic.
- A bounded weekly systemd timer now keeps a lighter refresh path alive:
  - `edge-lab-lite.timer`
  - Sundays at `03:15 UTC` plus up to 20 minutes randomized delay
  - runner: `/opt/matrix-trader/scripts/run_edge_lab_lite.sh`
  - log: `/opt/matrix-trader/logs/edge_lab_lite.log`
  - report: `/opt/matrix-trader/data/factor_report.json`
  - DB: `/opt/matrix-trader/data/edge_lab.db`
- Initial smoke run succeeded: labels/materialization updated, `edge_lab.db` present, factor report regenerated.

---

## Current Gaps And Watch Items

| Item | Status | Notes |
|---|---|---|
| MEXC private API | ✅ Fixed on Vultr | Old Hetzner block is no longer production-relevant |
| MEXC subaccount funding | ⚠️ User action | Private read-only works, but account equity is currently `0.0` |
| Live trading | ⚠️ Gated | Keep `LIVE_TRADING_ENABLED=false` until the paper/live readiness thresholds justify activation |
| HL private execution key | ⚠️ Optional/user action | Hyperliquid execution stack exists, but private key is not required for MEXC read-only status |
| Anthropic credits | ⚠️ User action if depleted | Cipher/coach can fall back, but Claude-quality reports require credits |
| Paper/live data separation | Watch | Paper trades and live-tagged signals must stay analytically separate when judging EV |
| Edge Lab output | Watch | Research output should inform human/system review, not directly mutate live scoring |

---

## Immediate Next Actions

1. Fund the MEXC subaccount futures wallet when ready to begin live-readiness testing.
2. Keep MEXC key/secret only in `/opt/matrix-trader/.env`; never commit or print them.
3. Monitor `/api/account/status`, `/api/account/balance`, and `/api/account/positions` after funding.
4. Let paper bot run with the new ATR/trend fields and review whether high-volatility junk is being filtered.
5. Let Edge Lab Lite run weekly; check `edge_lab_lite.log` and `factor_report.json` after the first scheduled Sunday run.
6. Continue validating mt-learner suggestions through the review UI before any threshold changes become trusted.

---

## Files An AI Co-Pilot Needs First

```
AGENTS.md                  ← operating rules for Codex
HANDOFF.md                 ← authoritative phase/task state
SERVER_GUIDE.md            ← production server access/deploy guide
app.py                     ← backend
templates/index.html       ← frontend
lib/mexc_private.py        ← MEXC private account client
docs/state-of-system.md    ← this high-level snapshot
```
