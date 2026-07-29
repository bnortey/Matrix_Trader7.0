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
- **Cipher Research Group**: 12 named analysts producing distinct daily and weekly intelligence reports with specialty-selected evidence, a compact Desk Verdict, conditional scenarios, paragraph-level provenance, repetition checks, and a descriptive forward-claims ledger. Clickable analyst profiles explain focus selection, evidence, trader use, invalidation, and data limits.
- **Hermes Advisory Group**: external consultancy bridge — weekly memo sync, on-demand run button, context packet with health score, recommendation, and blindspots.
- **Edge Lab**: standalone factor research pipeline — fetches candles, labels price paths, computes features, materializes to columnar SQLite, runs factor group analysis (ATR / RSI / regime / trend / volume / tags).
- Agent layer: 8-analyst AI pipeline runs on every enriched signal. Phase 2 live — `agent_shadow_delta` applied to conviction. Regime labels feed learner suppressions.
- AI trade briefs, closed-trade coach reviews (Thomas Chen persona), and strategy reviews via a provider-independent router.
- Super User AI Control Center: global model, per-workflow routes, free-first preset, custom OpenAI-compatible endpoint, connection test, redacted health telemetry, health-aware fallback ordering, and persistent provider circuit breakers.
- Model Benchmark Lab: versioned, shadow-only MT7 workflow cases; score-only persistence; champion/challenger recommendations; explicit gated promotion to a workflow route.
- Forward AI Scoreboard: bounded 15m/1h/4h UP/FLAT/DOWN probability collection, calibration and net-after-cost evaluation, no-change/MT7 baselines, and regime evidence. Shadow research only; it cannot change conviction, risk, paper trades, leverage, or execution.
- Execution layer built (Hyperliquid order placement, kill switch, EIP-712 signing) — gated behind `LIVE_TRADING_ENABLED=false`.

## What It Is Not

- Not an auto-trading bot (execution layer built but not activated).
- Not financial advice.
- Not an operational price prediction engine. Probabilistic forecasts are measured only in the isolated Forward AI shadow ledger.
- Not a SaaS app.

You still make the final trading decision and manually execute any trade.

## Stack

- Backend: Python 3.11+, Flask (~9,400 lines, single file)
- Frontend: single-file vanilla HTML/CSS/JS dashboard (~9,200 lines, no build step)
- Data: MEXC public contract APIs, Hyperliquid public API, OKX sentiment APIs, optional CoinGlass V4
- History: local SQLite (`data/signals.db`); Edge Lab uses `data/edge_lab.db`
- AI: Claude, OpenAI, Gemini, DeepSeek, Kimi, Z.ai, Groq, Ollama, and custom OpenAI-compatible endpoints through `lib/ai_client.py`

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
| `OPENAI_API_KEY` | GPT-5.6 Sol/Terra/Luna |
| `GEMINI_API_KEY` | Gemini hosted models, including free-tier choices |
| `DEEPSEEK_API_KEY` | DeepSeek V4 Flash/Pro |
| `KIMI_API_KEY` | Kimi K2.6/K3 |
| `ZAI_API_KEY` | Z.ai GLM models |
| `GROQ_API_KEY` | Groq free developer-tier models |
| `OLLAMA_BASE_URL` | Local Ollama endpoint, for example `http://localhost:11434` |
| `CUSTOM_AI_API_KEY` | Optional key for the custom OpenAI-compatible endpoint configured in Tools |
| `MT7_AI_SETTINGS_PATH` | Optional override for the shared AI routing settings path |
| `MT7_AI_TELEMETRY_DB_PATH` | Optional override for the redacted AI call ledger database |
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
| `SOCIAL_INTERVAL_SECONDS` | Cached public social-activity cadence; default 1800 seconds |
| `CATALYST_INTERVAL_SECONDS` | Cached official catalyst cadence; default 900 seconds |
| `TOKENOMICS_INTERVAL_SECONDS` | Cached supply/unlock/treasury cadence; default 21600 seconds |

The fallback policy is controlled in the Tools tab: selected-only, free-tier/local only, low-cost/free, or all providers. The free-first preset uses the hard free-only boundary. The app runs without an AI key—deterministic sections continue and AI-only sections degrade gracefully. AI telemetry stores route, success, latency, fallback, and redacted errors; it never stores prompts, responses, or keys.

Cipher evidence collectors run asynchronously; scans and report requests read SQLite caches and never wait on source networks. Social activity is currently Bluesky-only and is qualified for author diversity, concentration, duplication, and baseline maturity—engagement is not a trade score. Official exchange, protocol/governance, U.S. regulator, and Federal Reserve sources are source-labeled and conservatively mapped. Direct holder concentration and labeled wallet/exchange flows remain disabled until MT7 has reviewed chain contracts, label provenance, and identity-safe transfer handling.

Cipher’s report-claims ledger is accountability infrastructure, not a trading input. It records a small set of structured forward claims before their observation window, resolves mature claims in the background, preserves unscorable outcomes, and cannot change signals, conviction, strategies, risk gates, leverage, sizing, or execution. The read-only ledger is available at `GET /api/intelligence/report-claims`.

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

Paper position sizing can use either **Fixed Base** or **Compound Realized Equity**. Compound mode uses closed Paper P&L only—never unrealized gains—and includes a sizing cap, equity floor, and drawdown fallback. It requires explicit acknowledgement, cannot be switched while positions are pending/open, and starts a new validation cohort when enabled. This setting is Paper-only and does not raise leverage or grant Live authority.

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
- **Edge Lab** (`edge_lab/` package): versioned, research-only factor pipeline. V2 uses exit-aware path economics, dynamic same-window baselines, fee/slippage-adjusted expectancy, conservative symbol-day effective samples, discovery/confirmation splits, ambiguity and symbol-concentration checks, and multiple-testing control. It also validates conditions inside each MT7 strategy and measures rejected candidates from every instrumented gate with forward counterfactual paths. Outputs `data/factor_report.json`; it has no scoring, sizing, leverage, or execution authority.

Edge Lab migrations are bounded and resumable:

```bash
python3 edge_lab_upgrade.py --max-symbols 5
python3 edge_lab_materialize.py --db data/edge_lab.db
python3 edge_lab_factors.py --db data/edge_lab.db --signals-db data/signals.db
python3 edge_lab_meta.py --signals-db data/signals.db --edge-db data/edge_lab.db
```

All factor findings remain `research_only` until the rolling analysis window has at least 95% v2 label and feature coverage. Generic candle factors include a configurable round-trip fee/slippage assumption but do not invent historical funding; exact captured funding is evaluated in the strategy-conditioned Paper layer. Source-retention maintenance is dry-run by default and requires both `--apply` and `--backup-confirmed` before it deletes verified, already-materialized v2 JSON rows.

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
├── edge_lab_upgrade.py     # bounded legacy→v2 label/feature migration
├── edge_lab_maintenance.py # audited source-retention dry-run/pruner
├── edge_lab_meta.py        # frozen v1 + zero-authority v2 challenger
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
│   ├── factor_engine.py    # dependence/cost-aware factor analysis
│   ├── strategy_evaluator.py # per-strategy Paper + rejected-candidate validation
│   ├── meta_labeler.py     # frozen binary v1 model
│   └── meta_labeler_v2.py  # grouped-time net-utility challenger
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
