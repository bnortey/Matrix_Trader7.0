# Matrix Trader Strategy Guide

This guide explains how strategies work in Matrix Trader 7.0, how they affect paper trading, and how the data should be interpreted before any future bot-trading integration.

Matrix Trader strategies are not prediction models. They are scoring profiles that decide which market setups deserve attention.

The practical question each strategy tries to answer is:

> Given the current market data, is this symbol showing a tradable LONG or SHORT setup that fits this strategy's thesis?

---

## Strategy Pipeline

Every scan follows the same core pipeline:

1. Fetch all MEXC perpetual futures tickers.
2. Score each symbol with the active strategy's weights and filters.
3. Enrich the top candidates with candles, indicators, orderbook data, funding, and market context.
4. Build entries, take-profit levels, and stop loss from ATR and direction.
5. Return ranked signals to the dashboard.
6. Log each signal into SQLite for paper-trading analysis.

Built-in and custom strategies all use the same pipeline. What changes is the weighting and filtering logic.

---

## Built-In Strategies

| Strategy | Thesis | Default Leverage | Conviction Floor |
|---|---|---:|---:|
| Balanced | Broad all-market setup using momentum, funding, basis, and volume | 20x | 55 |
| Funding Arb | Funding extremes may signal crowded positioning and squeeze risk | 10x | 60 |
| Momentum Breakout | Strong directional moves with volume can continue | 25x | 55 |
| Mean Reversion | Stretched RSI and overextended moves may snap back | 15x | 65 |

### Balanced

Balanced is the general-purpose scanner. It blends momentum, funding, basis, and volume so one input does not dominate the whole decision.

Use it when:

- Market conditions are mixed.
- You want a broad scan before narrowing down.
- You are not sure which specialized strategy fits the current regime.

Watch for:

- Average-looking setups where only one input is doing most of the work.
- Low-conviction signals near the threshold.
- Weak performance in specific regimes or symbols.

### Funding Arb

Funding Arb focuses on extreme funding rates. The thesis is that one side of the market may be overcrowded and paying heavily, creating conditions for squeeze or reversal.

Use it when:

- Funding is unusually positive or negative.
- Price looks overextended.
- The setup is not in a clean runaway trend.

Watch for:

- Extreme funding staying extreme during strong trends.
- Counter-trend trades that look cheap but keep moving against the signal.
- Signals where funding is the only strong input.

### Momentum Breakout

Momentum Breakout focuses on strong directional movement and volume expansion. The thesis is that some moves continue when participation is high enough.

Use it when:

- Multiple timeframes are moving in the same direction.
- Volume supports the move.
- Trend alignment is clean.

Watch for:

- Thin-liquidity pumps.
- Late entries after the move is already exhausted.
- Large losses when momentum reverses sharply.

### Mean Reversion

Mean Reversion fades overextended RSI conditions. The thesis is that very stretched moves can snap back toward a fairer zone.

Use it when:

- RSI is meaningfully overbought or oversold.
- Momentum is showing exhaustion.
- Price is near a range extreme rather than breaking into a new trend.

Watch for:

- Catching a trend that is still accelerating.
- Stops getting hit before the reversal begins.
- Signals fighting strong daily trend alignment.

---

## Strategy Inputs

Strategies use the same market inputs but weight them differently.

| Input | Meaning |
|---|---|
| Momentum | 24h, 4h, and 1h directional movement |
| Funding rate | Whether longs or shorts are paying heavily |
| Basis / fair-price spread | Difference between market price and fair/reference price |
| Volume | Participation and liquidity context |
| RSI | Overbought/oversold context |
| ATR / volatility | How wide price is moving relative to current price |
| Orderbook imbalance | Bid/ask pressure from depth |
| Daily trend | Higher-timeframe directional context |

Weights do not guarantee profitability. They only decide what kind of setup the strategy prefers.

---

## Strategy Keys And Tracking

Every strategy has a stable `strategy_key`.

Examples:

```text
Balanced            -> balanced
Funding Arb         -> funding_arb
Momentum Breakout   -> momentum_breakout
Mean Reversion      -> mean_reversion
```

This matters because Matrix Trader tracks paper-trading outcomes per strategy. Two strategies can open the same symbol and direction at the same time, and those are intentionally treated as separate paper positions.

Example:

```text
AIA_USDT SHORT Balanced
AIA_USDT SHORT Funding Arb
```

Those are different strategy decisions, even though the symbol and direction are the same.

---

## Custom Strategies

Custom strategies are cloned from built-ins. They can adjust:

- Momentum weight
- Funding weight
- Basis weight
- Volume multiplier
- Minimum conviction
- Leverage cap
- Strategy filters

Custom strategies are tracked separately in history. This is important because a custom clone should be compared against its base strategy, not merged into it.

Good custom strategy practice:

- Change a small number of settings at a time.
- Let the custom strategy collect enough closed trades before judging it.
- Compare it against the base strategy over the same market period.
- Do not assume a custom strategy is better because it had one good day.

---

## Paper Trading Lifecycle

Paper trading is the feedback loop that tells you whether a strategy is working.

When a scan finds a signal:

1. The signal is logged to `data/signals.db`.
2. It becomes an open paper position.
3. Matrix Trader waits for entry1 to be touched.
4. After entry, the system watches Min15 candles for TP and stop hits.
5. The trade is tagged as WIN, LOSS, PARTIAL, EXPIRED, or SKIPPED.
6. Actual leveraged `pnl_pct` is persisted.
7. The outcome is used by History, Strategies, Coach Review, and future optimization analysis.

The system does not place live orders. It simulates the signal as if it were a trade plan being followed.

---

## Deduplication Rules

The `signals` table is a strategy evaluation log, so multiple rows per symbol are normal.

However, repeated scans should not repeatedly open the same paper position for the same strategy. Matrix Trader now deduplicates at log time:

```text
same symbol + same direction + same strategy_key + still open
```

If that combination already exists, the new signal is skipped and the original open paper trade stays untouched.

This means:

- Re-scanning does not create duplicate open rows for one strategy.
- Different strategies can still hold the same symbol simultaneously.
- Original entries, targets, stops, and logged time are preserved until the trade closes.

---

## Outcomes

Matrix Trader uses these outcome labels:

| Result | Meaning |
|---|---|
| WIN | TP3 was hit |
| LOSS | Stop was hit before any TP |
| PARTIAL | TP1 or TP2 was hit, or TP was hit before later stop pressure |
| EXPIRED | Signal stayed open too long without resolving |
| SKIPPED | User manually skipped or excluded it |

Partial outcomes use blended exit logic where appropriate. For example, if TP1 hits and then stop hits, the system assumes part of the trade took profit and the remainder stopped.

---

## Actual P&L Accounting

The current dashboard uses actual persisted `pnl_pct` as the source of truth.

`pnl_pct` is the leveraged percentage return of the trade:

```text
raw price move % x strategy leverage
```

The History tab's Actual P&L and Sim Account use:

```text
account balance x 1% account slice x (pnl_pct / 100)
```

Example:

```text
Account: $1,000
Trade pnl_pct: +20%
Account slice: $10
Estimated account P&L: $10 x 20% = +$2
```

This is deliberately conservative. It does not mean the whole account was placed into the trade. It means the app is simulating a small fixed account slice per signal so strategies can be compared consistently.

Important:

- Actual P&L uses persisted `pnl_pct`.
- The old R-multiple model is no longer used for headline performance.
- Strategy Analytics and History should tell the same performance story.

---

## Open Positions Panel

The Open Positions panel shows untagged paper trades that are still live.

It displays:

- Symbol and direction
- Entry and current price
- Live P&L
- Distance to stop
- Distance to TP1
- Notional estimate
- Liquidation estimate
- Signal age
- Strategy grouping

Historical duplicate open rows are collapsed by symbol, direction, and strategy, with a badge showing how many were collapsed.

This panel is important for future bot integration because it behaves like a live position monitor, even though no real order is placed.

---

## Closed Trade Detail

Closed trades include a detailed review panel.

The panel shows:

- Strategy
- Conviction
- Entry
- Exit
- Stop
- TP1
- Actual P&L
- Duration
- Signal reason
- Result note
- Trade Journey
- Coach Review

### Trade Journey

Trade Journey explains what happened between signal, entry, and close.

Key terms:

| Metric | Meaning |
|---|---|
| MAE | Maximum Adverse Excursion: worst move against the trade while open |
| MFE | Maximum Favorable Excursion: best move in favor while open |
| Capture | How much of the favorable move the final exit kept |
| Stop pressure | How much of the planned stop distance was used |
| Signal -> Entry | How long it took for entry1 to be touched |
| Entry -> Close | How long the trade lasted after entry |

Example:

```text
MAE: 1.2%
MFE: 6.4%
Capture: 78%
Stop pressure: 24%
```

Plain English:

- The trade only moved 1.2% against entry.
- At best, it moved 6.4% in favor.
- The final exit captured 78% of the best available move.
- It did not come close to the stop.

This is a clean trade path.

### Coach Review

Coach Review uses the deterministic Trade Journey stats as context. It should describe:

- How price moved from signal to close
- Whether the entry was clean or stressful
- Whether the signal thesis followed through
- What to watch next time

It should not recommend changing a strategy based on one trade. Single trades are evidence, not proof.

---

## Strategies Page

The Strategies page compares strategy performance.

It includes:

- Strategy comparison table
- Total and average P&L bars
- Equity curve
- Outcome breakdown
- P&L distribution
- Best/worst symbols
- Volatility-regime performance
- Learn section for each strategy

Use this page to answer:

- Which strategies are producing positive expectancy?
- Which strategies are dragging performance?
- Which symbols work best or worst for each strategy?
- Which volatility regimes are favorable?
- Are results broad-based or dependent on a few trades?

---

## Reading Profitability Correctly

Do not judge Matrix Trader as one single strategy.

Judge each strategy separately:

```text
Funding Arb may be promising.
Momentum Breakout may be weak.
Balanced may need stricter filters.
Mean Reversion may need more data.
```

Key metrics:

| Metric | What To Look For |
|---|---|
| Expectancy | Average actual `pnl_pct` should be positive |
| Profit factor | Gross wins / gross losses should be above 1.3 before automation |
| Max drawdown | Must be tolerable under real sizing |
| Avg win vs avg loss | Losses should not dominate wins |
| Regime performance | Strategy should work in the regimes it claims to target |
| Symbol concentration | Profit should not come from one symbol only |
| Sample size | Results should survive enough closed trades |

---

## How Much Data Is Enough?

Use both sample size and calendar time.

For any strategy:

| Closed Trades | Interpretation |
|---:|---|
| < 30 | Noise only |
| 30-100 | Early signal, not reliable |
| 100-300 | Useful read, still cautious |
| 300+ | Enough to start trusting broad behavior |
| 500-1000+ | Stronger evidence for automation discussions |

Calendar time also matters:

| Strategy Type | Suggested Forward-Test Window |
|---|---|
| Short timeframe | 2-4 weeks minimum |
| Intraday | 4-8 weeks minimum |
| Longer timeframe | 2-3 months minimum |
| Bot candidate | 8-12 weeks minimum |

The goal is to capture different market regimes:

- Trending days
- Choppy days
- High-volatility liquidation days
- Low-volume sessions
- Weekend liquidity
- Funding flips
- BTC-led moves
- Altcoin rotations

---

## Bot Trading Readiness

Matrix Trader is not a bot today. Paper trading exists so future bot integration can be approached carefully.

A strategy should not be considered for bot execution until it has:

- 300+ closed trades for that exact strategy
- Positive expectancy using actual `pnl_pct`
- Profit factor above 1.3
- Drawdown within acceptable limits
- Performance across multiple market regimes
- Performance not dependent on one symbol or one day
- Fees, slippage, funding, and missed fills accounted for
- A paper-bot simulation period
- Kill switch rules
- Max daily loss rules
- Max open position rules
- Exchange/API failure handling

Suggested readiness ladder:

| Stage | Description |
|---|---|
| Research | New strategy, under 100 closed trades, no live trading |
| Watchlist | 100-300 trades, early positive expectancy, manual only |
| Paper Bot Candidate | 300+ trades, stable metrics, simulated automation |
| Micro-Live Candidate | 500+ trades or 8+ weeks, tiny size only |
| Bot Integration | 1000+ trades or 12+ weeks, full risk controls |

Bot integration should start with one proven strategy, not the whole system.

---

## Optimization Philosophy

Strategy optimization should be transparent and sample-size aware.

Good optimization:

- "Funding Arb performs better in medium volatility."
- "Momentum Breakout loses heavily in low-volume spikes."
- "Balanced signals below conviction 62 have negative expectancy."
- "This custom strategy beats its base over 300 comparable trades."

Bad optimization:

- Changing strategy weights because of one trade.
- Treating a short winning streak as proof.
- Ignoring fees and slippage.
- Optimizing on one day or one symbol.
- Silently changing live behavior.

The safest path is:

1. Collect descriptive evidence.
2. Show diagnostics.
3. Suggest changes.
4. Let the user clone or approve changes.
5. Track the custom strategy separately.

---

## Practical Usage

When reviewing a strategy, ask:

1. Does it have enough closed trades?
2. Is actual P&L positive?
3. Is profit factor healthy?
4. Is drawdown acceptable?
5. Does it work in the market regime it targets?
6. Does it work across more than one symbol?
7. Are wins clean or messy according to Trade Journey?
8. Are losses failing fast or bleeding slowly?
9. Would fees/slippage erase the edge?
10. Would I trust this with tiny real size after a paper-bot simulation?

If the answer is not clearly yes, keep collecting data.

