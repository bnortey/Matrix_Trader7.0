# Matrix Trader 7.0

Matrix Trader 7.0 is a local web dashboard for scanning MEXC and Hyperliquid perpetual swap markets, ranking high-conviction LONG/SHORT setups, running an automated paper bot, and reviewing trade outcomes with AI coaching.

It is built for manual traders who want research-grade tooling without giving up execution control.

## What It Does

- Scans 800+ MEXC and 190+ Hyperliquid perpetual futures pairs using public API data.
- Ranks signals by conviction score using funding rate, momentum, basis spread, RSI, ATR, trend, orderbook depth, and CoinGlass derivatives context.
- Generates ATR-based entry, take-profit, and stop-loss ladders (3-tier).
- Shows a live open-position monitor with current price, leveraged P&L, stop/TP distance, funding rate, and settlement countdown via SSE stream.
- Logs signals to local SQLite history for paper-trading analysis.
- **Paper bot**: automated simulated trading with pending-entry wait, max-hold expiry, configurable fee/slippage deduction, and chunked Min1 evaluator parity. Hard-dollar P&L tracking.
- **Pair Workspace**: deep-dive panel for any paper trade — native MT7 chart with order-flow event markers (Absorb / Δ Div / Sweep / Exhaust), trade lifecycle markers, chart toggle controls, strategy context card.
- Strategy Lab: four built-in scoring strategies plus custom strategy clones with adjustable weights, filters, leverage cap, and conviction floor.
- Risk Gates: live block/shadow/off control for high-volatility circuit breakers.
- **mt-learner**: external VPS service — analyzes closed trade outcomes, proposes threshold and regime suppression changes, tracks applied experiments in a ledger.
- **Cipher Research Group**: 12 named analysts producing daily and weekly intelligence reports. First-person narratives with domain-specific data. Clickable analyst org-chart with profile modals.
- **Hermes Advisory Group**: external consultancy bridge — weekly memo sync, on-demand run button, context packet with health score, recommendation, and blindspots.
- **Edge Lab**: standalone factor research pipeline — fetches candles, labels price paths, computes features, materializes to columnar SQLite, runs factor group analysis (ATR / RSI / regime / trend / volume / tags).
- Agent layer: 8-analyst AI pipeline runs on every enriched signal. Phase 2 live — `agent_shadow_delta` applied to conviction. Regime labels feed learner suppressions.
- AI trade briefs, closed-trade coach reviews (Thomas Chen persona), and strategy reviews via provider fallback chain.
- Per-feature AI model pinning: global model + coach review model independently selectable.
- Execution layer built (Hyperliquid order placement, kill switch, EIP-712 signing) — gated behind `LIVE_TRADING_ENABLED=false`.

## What It Is Not

- Not an auto-trading bot (execution layer built but not activated).
- Not financial advice.
- Not a price prediction engine.
- Not a SaaS app.

You still make the final trading decision and manually execute any trade.

## Stack

- Backend: Python 3.11+, Flask (~9,400 lines, single file)
- Frontend: single-file vanilla HTML/CSS/JS dashboard (~9,200 lines, no build step)
- Data: MEXC public contract APIs, Hyperliquid public API, OKX sentiment APIs, optional CoinGlass V4
- History: local SQLite (`data/signals.db`); Edge Lab uses `data/edge_lab.db`
- AI: provider fallback chain — Claude → Gemini → DeepSeek → Groq — through `lib/ai_client.py`

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

Open `http://localhost:8080`. For iPhone access on the same WiFi, use the LAN URL printed on startup.

## Environment Variables

Copy `.env.example` to `.env` and fill only what you need.

Required for market scanning: **none**. MEXC and Hyperliquid public data require no API key.

Optional:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | AI trade briefs, coach reviews, strategy analysis |
| `GEMINI_API_KEY` | AI fallback (provider 2) |
| `DEEPSEEK_API_KEY` | AI fallback (provider 3) |
| `GROQ_API_KEY` | AI fallback (provider 4) |
| `MEXC_API_KEY` + `MEXC_API_SECRET` | MEXC read-only account status in Bot Readiness panel |
| `COINGLASS_API_KEY` | CoinGlass V4 derivatives context (OI, liquidations, funding) |
| `HL_WALLET_ADDRESS` | Hyperliquid read-only account status |
| `HL_PRIVATE_KEY` | Hyperliquid execution (P11 — not yet activated) |
| `LIVE_TRADING_ENABLED` | Master gate for live order placement — must be `true` to enable |
| `MT7_API_TOKEN` | Optional bearer token required for mutating routes on exposed installs |
| `ALLOW_PAPER_RESET` | Emergency maintenance gate for `/api/paper/reset`; keep `false` unless deliberately resetting with backup |
| `MATRIX_PORT` | Override default port 8080 |
| `SCORE_VERSION` | `v1` (step) or `v2` (saturating ramp) scoring |
| `REPORT_NARRATIVE_MODE` | `deterministic` / `free` / `auto` for Cipher report AI polish |

AI providers fall through automatically. App runs without any AI key — AI sections degrade gracefully.

## Security

Never commit real API keys. The repo ignores `.env`, `data/`, and Python caches. Only `.env.example` is public. If a key was ever pasted into chat or logs, rotate it immediately.

If the app is reachable beyond localhost/LAN, set `MT7_API_TOKEN` so mutating routes require `Authorization: Bearer <token>`. Paper trade reset is disabled by default; only enable `ALLOW_PAPER_RESET=true` during a deliberate maintenance window, and turn it off again afterward.

## Workflow

1. Open the dashboard.
2. Select an **exchange** (MEXC or Hyperliquid) and a **strategy**.
3. Click **Scan** — scores 800+ pairs, enriches top 30 with klines, agents, and AI.
4. Review ranked LONG/SHORT signals with conviction scores, tags, and why-lines.
5. Click a signal for the full trade plan — chart, AI report, entry/TP/SL ladders, order book context, liquidation price.
6. Use the **Risk Calculator** (Tools tab) to size the position manually.
7. Track paper-trade outcomes in **History** — leveraged P&L auto-computed.
8. Open any paper trade's **Pair Workspace** for the chart, order-flow markers, and strategy context.
9. Read **Cipher daily/weekly reports** and **Hermes memos** in the Intelligence tab.

## Paper Trading

The paper bot runs autonomously on a configurable scan interval. Trades lifecycle:

1. **Pending** — waiting for price to touch entry1
2. **Open** — position active, monitored against Min1 klines
3. **Closed** — TP / stop hit, or max-hold expired (no loss assigned on expiry)

P&L is net after configurable maker/taker fee and slippage. Hard-dollar stats (total P&L $, avg $ per trade, profit factor, best/worst trade) are shown in the Paper tab alongside gross and cost breakdowns.

Closed paper trades are clickable — opening a detail panel with full lifecycle, entry/exit/stop/TP, flow score, duration, journey metrics, and linked coach review.

## Strategy Lab

Four built-in scoring strategies:

| Strategy | Focus | Default Leverage |
|---|---|---|
| Balanced | General-purpose | 20× |
| Funding Arb | Funding rate extremes | 10× |
| Momentum Breakout | Strong directional moves | 25× |
| Mean Reversion | Overextended RSI setups | 15× |

Clone any built-in to adjust weights, filters, conviction floor, and leverage cap. See [STRATEGIES.md](STRATEGIES.md).

## Self-Improving Loop

MT7 tracks its own performance and proposes improvements:

- **Goals file** (`data/goals.json`) — defines account balance targets and benchmark metrics.
- **mt-learner** (external VPS service) — analyzes feature importance, conviction thresholds, and regime performance from closed trades. Proposes threshold raises/lowers and regime suppressions optimized for net EV.
- **Suggestions sub-tab** — review, apply, or reject proposed changes. Applied changes write to `data/strategy_overrides.json` and log to `data/experiment_ledger.json` (append-only, reversible).
- **Learner overlays** are tracked as named experiments with baseline snapshots so their impact can be measured.

## Intelligence

- **Cipher Research Group**: 12 named analysts (trader, risk manager, funding, microstructure, cross-venue, technical, sentiment, tokenomics, narrative, structural). Daily and weekly reports cached to `data/reports/`. Clickable analyst cards with profile modals and domain evidence tables.
- **Hermes Advisory Group**: external consultancy that reviews the MT7 evidence packet and writes advisory memos. Runs weekly via systemd timer on VPS; also triggerable on demand from the UI. Advisory only — no execution authority.
- **Edge Lab** (`edge_lab/` package): standalone factor research pipeline that fetches candles, labels price paths, materializes features, and runs factor group analysis (ATR decile, RSI decile, compression, regime × trend, volume, tag presence). Outputs `data/factor_report.json`.

## Pair Workspace Chart

The pair workspace (Paper tab → click any trade) shows an MT7 native chart with:

- **Trade lifecycle markers**: Queued, Entry, TP hits, Stop
- **Order-flow event markers**: Absorption (diamond), Delta Divergence (circle), Liquidity Sweep (arrow), Exhaustion (square)
- **Large print markers**: significant buy/sell prints
- **Levels**: entry / stop / TP lines
- **Liquidation line**
- **Toggle controls** — turn any marker group on/off; preferences saved in localStorage
- **Strategy Context card** — recent 20/10 performance, symbol EV, direction fit, cold streak warnings

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
├── app.py                  # entire Flask backend (~9,400 lines)
├── backtest.py             # standalone backtest script
├── analyze.py              # standalone signal audit
├── fade_hypothesis.py      # standalone fragility fade analysis
├── edge_lab_build.py       # Edge Lab dataset builder CLI
├── edge_lab_factors.py     # Edge Lab factor analysis CLI
├── edge_lab_materialize.py # Edge Lab materializer CLI
├── requirements.txt
├── .env.example
├── STRATEGIES.md
├── HANDOFF.md              # session state and task list (authoritative)
├── templates/
│   └── index.html          # entire frontend (~9,200 lines)
├── lib/
│   ├── ai_client.py        # AI provider fallback chain
│   ├── agents.py           # 8-analyst signal pipeline + 12-analyst Cipher group
│   ├── risk_liquidation.py # exchange-aware liquidation price engine
│   ├── exchange_context.py # canonical exchange-agnostic data contract
│   ├── adapters/           # MEXC and Hyperliquid normalizers
│   ├── hyperliquid_client.py
│   ├── hl_execution.py     # Hyperliquid order placement + kill switch
│   ├── coinglass_client.py
│   ├── indicators.py
│   ├── laddering.py
│   ├── mexc_private.py
│   └── risk_controls.py
├── edge_lab/               # standalone factor research package
│   ├── storage.py          # edge_lab.db schema + helpers
│   ├── mexc_data.py        # candle/ticker fetchers
│   ├── path_labeler.py     # price path outcome labeling
│   ├── feature_engine.py   # feature computation
│   ├── materializer.py     # columnar feature materialization
│   └── factor_engine.py    # factor group analysis
├── mt-learner/             # external VPS service mirror
│   ├── learner.py          # scheduler: 4 jobs on 30min/2hr/6hr/24hr
│   ├── analyzer.py         # feature/threshold/regime analysis
│   ├── suggester.py        # generates pending.json proposals
│   ├── researcher.py       # strategy hypothesis briefs
│   └── coach_analyst.py    # coach review pattern analysis
├── scripts/                # VPS deployment and maintenance scripts
└── data/                   # gitignored runtime data
    ├── signals.db
    ├── edge_lab.db
    ├── reports/
    └── hermes/
```

## Disclaimer

Crypto perpetual futures are high-risk products. Matrix Trader is a decision-support tool only. It does not guarantee profitable trades and does not replace your own risk management.
