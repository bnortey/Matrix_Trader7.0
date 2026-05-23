# Self-Improving Loop — Design Spec
**Date:** 2026-05-23
**Scope:** A+B — Goal Definition + Learner Apply/Reject Loop
**Status:** Approved for implementation planning

---

## What This Builds

A closed feedback loop that turns the existing mt-learner output into actionable, user-approved system improvements tied to explicit trading goals. The full scope includes five components, built in the order listed to address gaps discovered during design.

---

## Context and Motivation

Matrix Trader 7.0 has 1,399 closed signals and an external learner (mt-learner) running on the VPS that already analyzes outcomes and generates improvement suggestions. However three things are broken:

1. The learner auto-applies its own suggestions — no user gate exists.
2. Paper bot outcomes and manually-tagged live signals share the `signals` table, polluting EV calculations.
3. There is no formal goal definition — no target win rate, EV, or P&L — so the learner has nothing to optimize toward.

This spec fixes all three and adds the UI layer to make the loop visible and actionable.

---

## Account Parameters

| Parameter | Value |
|---|---|
| Starting capital | $200 |
| Risk per trade | 5–8% of current account balance |
| Max concurrent positions | 4 |
| Monthly return target | 20% (compounding — applies to current balance each month) |
| Kill switch drawdown | -40% of starting capital ($80) |
| Scale-up trigger | EV > 8% sustained over 50+ trades → increase risk% to 8% |

---

## Component 1: Learner Fix — Stop Self-Applying

**Problem:** Both suggestions in `pending.json` show `status: "applied"` — the learner is applying its own changes without user approval.

**Fix:**
- Add `status: "pending_review"` as the default state for new learner suggestions.
- Learner only sets `status: "pending_review"` — never `"applied"`. The MT7 Apply/Reject API sets `"applied"` or `"rejected"`.
- Add `data/rejected_suggestions.json` — a persistent rejection log. Learner reads this before generating new suggestions and skips any that match a rejected `id` or `(strategy, type, direction)` tuple.
- Enforce one-active-at-a-time: learner checks pending.json before generating — if any suggestion has `status: "pending_review"` or `status: "evaluating"`, it skips generation for that strategy.

**Files changed:** `/opt/mt-learner/suggester.py`, `data/rejected_suggestions.json` (new)

---

## Component 2: Paper EV Separation

**Problem:** `log_signals()` is called for paper bot entries, so paper outcomes land in `signals` table alongside manually-tagged live signals. The analyzer blends them into one EV number.

**Fix:**
- Add `source` column to `signals` table (migration): values `"live"` (default) or `"paper"`.
- Paper bot sets `source = "paper"` when calling `log_signals()`.
- `analyzer.py` separates EV computation into two tracks: live signals only, and paper signals only.
- `pending.json` and goal benchmark display show both EV tracks separately.

**Files changed:** `app.py` (schema migration + log_signals call), `/opt/mt-learner/analyzer.py`

---

## Component 3: Dynamic Position Sizing

**Problem:** Paper bot uses flat `size_usd = $100` regardless of account balance, ATR, or conviction.

**New formula:**
```
risk_amount = account_balance × (risk_pct / 100)
stop_pct = (entry_px - stop_loss) / entry_px   # derived from signal ladder
position_size = risk_amount / stop_pct
```

**Fallback:** If `stop_loss` is NULL, use `atr_pct` as the stop estimate. If `atr_pct` is also NULL, use 2% as a conservative default.

**Conviction modifier:**
- Conviction ≥ 80: 100% of calculated size
- Conviction 65–79: 80% of calculated size
- Conviction 55–64: 60% of calculated size

**Paper bot config changes:**
- Keep `size_usd` column in `paper_trades` DB schema — it is written dynamically at insert time from the position sizing formula.
- Remove `size_usd` from the paper bot config JSON and UI (it is now derived, not user-set).
- Add `account_balance_usd` (default 200) and `risk_pct_per_trade` (default 5.0) to config.
- Max position size cap: 25% of account balance per trade (prevents single-trade ruin).

**Files changed:** `app.py` (_paper_bot_scan, paper_trades schema, paper config fields)

---

## Component 4: Account Balance Tracking

**Problem:** No running account balance exists. The "Account Value" display requires computing it from trade history.

**Implementation:**
- Account value = `account_balance_usd` (from goals file) + Σ(size_usd × pnl_pct / 100) for all closed paper trades.
- New route `/api/paper/account` returns: `{ account_balance, current_value, total_pnl_usd, monthly_return_pct, drawdown_pct }`.
- Baseline snapshots for Apply/Reject: at apply-time, record current EV, win rate, trade count, and account value into the suggestion record in `pending.json`.

**Files changed:** `app.py` (new route), `pending.json` schema (baseline snapshot fields)

---

## Component 5: Goal Definition File

**Location:** `data/trading_goals.json` (gitignored, runtime-managed)

**Schema:**
```json
{
  "account_balance_usd": 200,
  "risk_pct_per_trade": 5.0,
  "max_positions": 4,
  "target_monthly_return_pct": 20.0,
  "target_ev_per_trade_pct": 5.0,
  "min_win_partial_rate": 0.50,
  "max_drawdown_pct": 40.0,
  "consecutive_loss_alert": 5,
  "scale_up_trigger": {
    "ev_threshold_pct": 8.0,
    "min_trades": 50,
    "new_risk_pct": 8.0
  },
  "evaluation_window_trades": 20,
  "evaluation_window_days": 14
}
```

**Routes:**
- `GET /api/goals` — return current goals + computed actuals (EV, win rate, account value, drawdown, monthly return)
- `PATCH /api/goals` — update goal parameters

---

## Component 6: Goal Benchmark Display (Strategies Tab)

A five-tile strip added at the top of the Strategies tab, always visible. Loads from `/api/goals`.

**Tiles (left to right):**
1. **Account Value** — current value vs starting capital, progress bar toward monthly target
2. **Monthly Return %** — current vs 20% target, days remaining in month
3. **EV / Trade (30d)** — rolling average of live signal EV with trade count (e.g., "+4.2% n=34")
4. **Win+Partial Rate** — current vs 50% floor, colour-coded
5. **Drawdown** — current drawdown %, red marker at kill zone (-40%)

**Alert strip (below tiles):**
- Amber: mt-learner has N new suggestions → link to Intelligence tab
- Green: scale-up trigger reached → prompt to increase risk%
- Red: kill switch zone reached → halt paper bot, surface warning

---

## Component 7: Apply/Reject UI (Intelligence Tab — new "Suggestions" sub-tab)

**Sub-tab badge:** Shows count of pending_review suggestions.

**Active suggestion card:**
- One at a time enforced — queue locked until active resolves.
- Shows: proposed change, reasoning, evidence summary, sample size, confidence level.
- Baseline metrics snapshot at display time: current EV, win rate, trade count.
- Expected impact: EV delta, win rate delta, trade volume delta.
- Goal tags: which goal metrics this suggestion targets.
- Actions: **Apply**, **Reject** (with optional reason), **Snooze 7 days**.

**On Apply:**
- PATCH the relevant config via existing API (strategy override, paper bot config, risk gate).
- Record baseline snapshot in `pending.json` under the suggestion's `baseline` field.
- Set `status: "evaluating"`, record `applied_at` timestamp.
- Queue next suggestion remains locked.

**On Reject:**
- Append to `data/rejected_suggestions.json` with reason and timestamp.
- Set `status: "rejected"` in `pending.json`.
- Unlock queue — next suggestion becomes active.

**Evaluation:**
- After `evaluation_window_trades` closed trades OR `evaluation_window_days` days (whichever comes first), system compares current metrics to baseline.
- Status moves to `"evaluated"`, before/after delta displayed in history.
- Queue unlocks for next suggestion.

**History section:** All applied/rejected suggestions with before/after deltas and reject reasons.

---

## Data Flow

```
signals.db (source="live") + paper_trades → mt-learner analyzer (two EV tracks)
  → suggester writes pending.json (status: "pending_review")
  → MT7 /api/goals surfaces actuals
  → Strategies tab benchmark display
  → Intelligence Suggestions tab
  → User Apply/Reject → PATCH config API
  → next scan cycle uses new params
  → outcomes back to signals.db
  → mt-learner evaluates after window
  → history updated
```

---

## What This Does NOT Include (Deferred)

- **Hermes integration** — separate spec, built after this loop is proven
- **Paper bot data in mt-learner analyzer** — deferred to Component C spec
- **Live P11 execution connection to goals** — deferred to P12 spec
- **Automatic scale-up** — system surfaces the trigger, user manually applies

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Stop_loss NULL on some signals breaks position sizing | Fallback chain: stop_loss → atr_pct → 2% default |
| Learner generates suggestion while evaluation in progress | One-at-a-time enforcement blocks generation for active strategy |
| Paper bot size_usd column removal breaks existing queries | Keep column, set it dynamically from risk% calculation at insert time |
| `data/trading_goals.json` missing on first boot | App creates it with defaults from `DEFAULT_PAPER_CONFIG` pattern |
| EV sample too thin to be meaningful | Show trade count alongside EV; flag "thin sample" when n < 20 |
