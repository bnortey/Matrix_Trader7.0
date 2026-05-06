# Matrix Trader 7.0

Matrix Trader 7.0 is a local web dashboard for scanning MEXC and Hyperliquid perpetual swap markets, ranking high-conviction LONG/SHORT setups, tracking paper trades, and reviewing trade outcomes.

It is built for manual traders. It does not place orders, manage exchange accounts, or auto-trade.

## What It Does

- Scans 800+ MEXC and 190+ Hyperliquid perpetual futures pairs using public API data.
- Ranks signals by conviction score using funding rate, momentum, basis spread, RSI, ATR, trend, orderbook depth, and CoinGlass derivatives context.
- Generates ATR-based entry, take-profit, and stop-loss ladders (3-tier).
- Shows a live open-position monitor with current price, leveraged P&L, stop/TP distance, funding rate, and settlement countdown via SSE stream.
- Logs signals to local SQLite history for paper-trading analysis.
- Auto-evaluates open paper trades against klines every 15 minutes; records leveraged P&L and blended exit prices for partial outcomes.
- Strategy Lab: four built-in scoring strategies plus custom strategy clones with adjustable weights, filters, leverage cap, and conviction floor.
- Risk Gates: live block/shadow/off control for high-volatility circuit breakers and symbol conviction penalties.
- Bot Readiness panel: per-strategy readiness score based on trade count, win rate, and profit factor.
- Agent shadow layer: 8-analyst AI pipeline runs on every signal in Phase 1 shadow mode — recording conviction deltas for forward-testing without affecting live scores.
- AI trade briefs, closed-trade coach reviews, and strategy reviews via provider fallback chain.

## What It Is Not

- Not an auto-trading bot.
- Not financial advice.
- Not a price prediction engine.
- Not a SaaS app.

You still make the final trading decision and manually execute any trade.

## Stack

- Backend: Python 3.11+, Flask
- Frontend: single-file vanilla HTML/CSS/JS dashboard (no build step)
- Data: MEXC public contract APIs, Hyperliquid public API, OKX sentiment APIs, optional CoinGlass V4
- History: local SQLite (`data/signals.db`)
- AI: optional provider fallback through `lib/ai_client.py`

## Quick Start

```bash
git clone https://github.com/bnortey/Matrix_Trader7.0.git
cd Matrix_Trader7.0

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
python3 app.py
```

Open:

```text
http://localhost:8080
```

For iPhone access on the same WiFi, use the LAN URL printed by `python3 app.py`.

## Environment Variables

Copy `.env.example` to `.env` and fill only what you need.

```bash
cp .env.example .env
```

Required for market scanning:

- None. MEXC and Hyperliquid public market data require no API key.

Optional:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | AI trade briefs, coach reviews, strategy analysis |
| `GEMINI_API_KEY` | AI fallback (provider 2) |
| `OPENAI_API_KEY` | AI fallback (provider 3) |
| `GROQ_API_KEY` | AI fallback (provider 4) |
| `MEXC_API_KEY` + `MEXC_API_SECRET` | MEXC read-only account status in Bot Readiness panel |
| `COINGLASS_API_KEY` | CoinGlass V4 derivatives context (OI, liquidations, funding) |
| `HL_WALLET_ADDRESS` | Hyperliquid read-only account status (0x... wallet address) |
| `MATRIX_PORT` | Override default port 8080 |

AI provider order: Claude → Gemini → OpenAI → Groq. If one provider is missing or errors, the next is tried automatically. The app runs without any AI provider — AI sections show unavailable states.

## Security

Never commit real API keys. The repo ignores `.env`, `data/`, and Python caches. Only `.env.example` should be public.

If a key was ever pasted into chat, logs, or a public place, rotate it immediately.

## Running The App

```bash
python3 app.py
```

Default port is `8080`. Override:

```bash
MATRIX_PORT=5000 python3 app.py
```

## Workflow

1. Open the dashboard.
2. Select an **exchange** (MEXC or Hyperliquid) and a **strategy**.
3. Click **Scan**.
4. Review ranked LONG/SHORT signals with conviction scores, tags, and why-lines.
5. Click a signal for the full trade plan — chart, AI report, entry/TP/SL ladders, orderbook context.
6. Use the risk calculator to size the position manually.
7. Track paper-trade outcomes in History — leveraged P&L is computed automatically when a TP or stop is hit.
8. Use **Strategy Review** for an AI analysis of your recent outcomes.

## Paper Trading And History

Every scan logs enriched signals into local SQLite.

The History tab separates:

- **Open positions**: untagged paper trades being monitored, with live prices via SSE stream.
- **Closed trades**: auto-tagged or manually tagged outcomes with leveraged P&L recorded.

Auto-evaluation waits for the first ladder entry to be touched before counting TP or stop. Partial outcomes use a blended exit price. P&L is computed as `raw_move% × leverage`. Signals open longer than 80 hours are auto-expired.

Closed trades can be filtered by strategy, direction, and result, and opened for a detailed review including an AI coach comment and trade journey metrics (MAE, MFE, capture ratio, path label).

## Strategy Lab

Four built-in scoring strategies:

| Strategy | Focus | Default Leverage |
|---|---|---|
| Balanced | General-purpose | 20× |
| Funding Arb | Funding rate extremes | 10× |
| Momentum Breakout | Strong directional moves | 25× |
| Mean Reversion | Overextended RSI setups | 15× |

Clone any built-in, adjust weights (momentum, funding, basis), filters (RSI gates, min volume, volatility allowlist), conviction floor, and leverage cap. Custom strategies appear as pills in the scanner and are tracked separately in history.

For full strategy docs see [STRATEGIES.md](STRATEGIES.md).

## Risk Gates

Live circuit breakers configurable in the Strategies tab:

- **Long vol gate**: blocks or shadows high/extreme-volatility LONG signals
- **Short vol gate**: blocks or shadows extreme-volatility SHORT signals on Balanced
- **Symbol conviction penalties**: auto-docks conviction on symbols with consistently negative historical P&L (5+ closed trades)

Gates have three modes: `block` (signal dropped), `shadow` (signal passes with a warning tag), `off` (gate disabled).

## Agent Shadow Layer

An 8-analyst AI pipeline runs on every enriched signal in Phase 1 shadow mode:

- Tokenomics, sentiment, news, technical, microstructure, funding, cross-venue, and regime analysts each assess a data slice
- A bull/bear debate synthesises narrative and structural views
- A Trader produces a `conviction_delta`; a Risk Manager checks hard blocks

All outputs are stored in `signal_json` as shadow fields (`agent_shadow_delta`, `agent_regime`, etc.) for forward-testing. **In Phase 1, agent deltas do not affect live conviction scores.** Phase 2 activation requires 50+ closed signals with shadow data showing predictive correlation between delta and outcome.

## AI Reviews

AI is optional. Matrix Trader can generate:

- 4-section signal reports (Setup, Structure, Invalidation, Risk)
- Closed-trade coach reviews
- Strategy reviews across recent outcomes

All AI calls go through `lib/ai_client.py`. Do not call individual AI SDKs directly from Flask routes.

## Server Deployment

```bash
rsync -avz --exclude='.env' --exclude='data/' --exclude='__pycache__' \
  --exclude='.git' --exclude='*.pyc' \
  ./ root@YOUR_SERVER:/opt/matrix-trader/

ssh root@YOUR_SERVER "systemctl restart matrix-trader"
```

Keep server secrets in `/opt/matrix-trader/.env`. Do not sync local `.env` or `data/`.

## Project Structure

```text
Matrix_Trader_7.0/
├── app.py                  # entire Flask backend
├── backtest.py             # standalone backtest script
├── analyze.py              # strategy audit report
├── requirements.txt
├── .env.example
├── STRATEGIES.md           # strategy guide
├── HANDOFF.md              # session state and task list
├── templates/
│   └── index.html          # entire frontend
├── lib/
│   ├── ai_client.py        # AI provider fallback chain
│   ├── agents.py           # 8-analyst shadow agent pipeline
│   ├── exchange_context.py # canonical exchange-agnostic data contract
│   ├── adapters/           # MEXC and Hyperliquid normalizers
│   ├── hyperliquid_client.py
│   ├── coinglass_client.py
│   ├── indicators.py       # RSI, EMA, ATR, VWAP
│   ├── laddering.py        # ATR-based entry/TP/SL ladders
│   ├── mexc_private.py     # MEXC account read (optional)
│   └── mexc_stream.py      # WebSocket client (built, not active)
├── docs/
└── data/                   # gitignored runtime data
```

## Disclaimer

Crypto perpetual futures are high-risk products. Matrix Trader is a decision-support tool only. It does not guarantee profitable trades and does not replace your own risk management.
