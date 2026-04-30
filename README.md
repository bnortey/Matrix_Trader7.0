# Matrix Trader 7.0

Matrix Trader 7.0 is a local web dashboard for scanning MEXC perpetual swap markets, ranking high-conviction LONG/SHORT setups, tracking paper trades, and reviewing trade outcomes.

It is built for manual traders. It does not place orders, manage exchange accounts, or auto-trade.

## What It Does

- Scans MEXC perpetual futures markets using public API data.
- Ranks signals by conviction score, direction, funding, momentum, volume, RSI, ATR, trend, and orderbook context.
- Generates ATR-based entry, take-profit, and stop-loss ladders.
- Shows a live open-position monitor with current price, leveraged P&L, stop/TP distance, funding rate, and funding settlement countdown.
- Logs signals to local SQLite history for paper-trading analysis.
- Auto-evaluates open paper trades against MEXC candles; records leveraged P&L, blended exit prices for partial outcomes.
- Strategy Lab: choose from four built-in scoring strategies or clone and customise your own.
- Supports AI trade briefs, closed-trade coach reviews, and strategy reviews through a provider fallback chain.

## What It Is Not

- Not an auto-trading bot.
- Not financial advice.
- Not a price prediction engine.
- Not a SaaS app.
- Not a multi-exchange execution platform.

You still make the final trading decision and manually execute any trade.

## Stack

- Backend: Python, Flask
- Frontend: single-file vanilla HTML/CSS/JS dashboard
- Data: MEXC public contract APIs
- History: local SQLite database in `data/signals.db`
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

For iPhone testing on the same WiFi, use the LAN URL printed by `python3 app.py`.

## Environment Variables

Copy `.env.example` to `.env` and fill only what you need.

```bash
cp .env.example .env
```

Required for market scanning:

- None. MEXC public market data does not require an API key.

Optional:

- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`
- `DEEPSEEK_API_KEY`
- `GROQ_API_KEY`
- `MEXC_API_KEY`
- `MEXC_API_SECRET`

AI provider order:

```text
Claude -> Gemini -> DeepSeek -> Groq
```

If one provider is missing, out of credits, or errors, Matrix Trader tries the next configured provider. If no provider works, the app still runs and AI sections show unavailable states.

## Security

Never commit real API keys.

The repo ignores:

- `.env`
- `.env.*`
- `data/`
- `logs/`
- Python caches

Only `.env.example` should be public. Before making the repo public, run a secret scan over both current files and Git history.

If a key was ever pasted into chat, logs, screenshots, or a public place, rotate it.

## Running The App

```bash
python3 app.py
```

Default port is `8080`. To override:

```bash
MATRIX_PORT=5000 python3 app.py
```

Useful routes:

- `/` dashboard
- `/api/scan` full signal scan
- `/api/market` market browser data
- `/api/signal/<symbol>` enriched symbol analysis
- `/api/signals/history` signal history
- `/api/outcomes/check` paper-trade outcome check
- `/api/prices?symbols=BTC_USDT,ETH_USDT` batch price fetch

## Workflow

1. Open the dashboard.
2. Select a **strategy** (Balanced, Funding Arb, Momentum Breakout, Mean Reversion, or a custom clone).
3. Click **Scan**.
4. Review ranked LONG/SHORT signals.
5. Click a signal or market pair for trade plan, chart, context, tags, and AI report.
6. Use the risk calculator to size manually.
7. Track paper-trade outcomes in History — leveraged P&L is computed automatically when a TP or stop is hit.
8. Use **Strategy Review** to get an AI analysis of your recent signal outcomes.

## Paper Trading And History

Every scan logs enriched signals into local SQLite.

The History tab separates:

- **Open positions**: untagged paper trades still being monitored, with live prices via SSE stream.
- **Closed trades**: auto-tagged or manually tagged outcomes, with leveraged P&L recorded.

Auto-evaluation waits for the first ladder entry to be touched before counting TP or stop. Partial outcomes use a blended exit price (position exits 1/3 at each TP level hit, remainder at stop). P&L is computed as `raw_move% × leverage` so it reflects what a leveraged trader actually sees. Signals open longer than 80 hours are auto-expired.

Closed trades can be filtered by strategy, direction, and result, and opened for a detailed review including an optional AI coach comment.

## Strategy Lab

Matrix Trader ships with four built-in scoring strategies:

| Strategy | Focus | Default Leverage |
|---|---|---|
| Balanced | General-purpose | 20× |
| Funding Arb | Funding rate extremes | 10× |
| Momentum Breakout | Strong directional moves | 25× |
| Mean Reversion | Overextended RSI setups | 15× |

You can clone any built-in, adjust weights (momentum, funding, basis), filters (RSI gates, min volume), conviction floor, and leverage cap, then save as a named custom strategy. Custom strategies appear as strategy pills in the scanner and are tracked separately in signal history.

For a full explanation of strategy scoring, paper-trading behavior, Trade Journey metrics, Strategy Analytics, and future bot-readiness criteria, see [STRATEGIES.md](STRATEGIES.md).

## AI Reviews

AI is optional. Matrix Trader can generate:

- 4-section signal reports
- Closed-trade coach reviews
- Strategy reviews across recent outcomes

All AI calls go through `lib/ai_client.py`. Do not call individual AI SDKs directly from Flask routes.

## Server Deployment Notes

One simple deployment pattern:

```bash
rsync -avz --exclude='.env' --exclude='data/' --exclude='__pycache__' \
  --exclude='.git' --exclude='*.pyc' \
  ./ root@YOUR_SERVER:/opt/matrix-trader/

ssh root@YOUR_SERVER
systemctl restart matrix-trader
sleep 8
ss -tulnp | grep python
```

Keep server secrets in `/opt/matrix-trader/.env`. Do not sync local `.env` or `data/`.

## Project Structure

```text
Matrix_Trader_7.0/
├── app.py
├── backtest.py
├── requirements.txt
├── .env.example
├── STRATEGIES.md
├── templates/
│   └── index.html
├── lib/
│   ├── ai_client.py
│   ├── indicators.py
│   ├── laddering.py
│   └── mexc_stream.py
├── docs/
├── data/          # gitignored runtime data
└── logs/          # gitignored runtime logs
```

## Roadmap

Recent completions:

- ✅ SSE live price stream for open positions
- ✅ Strategy Lab — four built-in strategies, custom strategy CRUD, per-strategy performance stats
- ✅ Leveraged P&L tracking — blended PARTIAL exits, pnl_pct persisted to DB at evaluation time
- ✅ Kline depth gate — pairs with insufficient history are filtered before enrichment
- ✅ AI provider fallback chain — Claude → Gemini → DeepSeek → Groq

Near-term priorities:

- Strategy analytics / comparison layer across built-in and custom strategies
- P4: beta tester feedback loop

## Disclaimer

Crypto perpetual futures are high-risk products. Matrix Trader is a decision-support tool only. It does not guarantee profitable trades and does not replace risk management.
