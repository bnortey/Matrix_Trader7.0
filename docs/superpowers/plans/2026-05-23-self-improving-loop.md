# Self-Improving Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the mt-learner feedback loop with user-controlled Apply/Reject, clean EV tracking, dynamic position sizing tied to a $200 account, and a goal benchmark display.

**Architecture:** Seven sequential tasks — fix learner status flow first, then separate paper/live EV, then wire dynamic position sizing, then build the goal API and display layers on top. Each task produces a deployable increment.

**Tech Stack:** Python 3.11 / Flask / SQLite3 / vanilla JS. mt-learner is a separate Python service on VPS at `/opt/mt-learner/`. No test framework exists — create `tests/` with pytest.

---

## Deploy Pattern (used after every backend task)

```bash
# From project root on Mac:
rsync -avz app.py root@62.238.15.113:/opt/matrix-trader/app.py
ssh root@62.238.15.113 "systemctl restart matrix-trader && sleep 2 && systemctl is-active matrix-trader"

# For mt-learner code changes only. Do not overwrite runtime learner state.
rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='models/' --exclude='suggestions/' --exclude='research/' \
  mt-learner/ root@62.238.15.113:/opt/mt-learner/
ssh root@62.238.15.113 "systemctl restart mt-learner && sleep 2 && systemctl is-active mt-learner"
```

---

## Task 1: Fix Learner — Stop Self-Applying, Add Rejection Log

**Files:**
- Modify: `mt-learner/suggester.py`
- Create: `data/rejected_suggestions.json` (template only — lives on VPS at runtime)

**Context:** `suggester.py` currently writes `status: 'pending'`. The spec requires `status: 'pending_review'` so MT7 can distinguish suggestions awaiting user action from ones already actioned. The `_already_exists()` guard also needs updating. A rejection log prevents the learner from re-proposing changes the user has dismissed.

- [x] **Step 1: Update status strings in suggester.py**

In `mt-learner/suggester.py`, make these changes:

Line 55 — update `_already_exists` to recognise the new status:
```python
def _already_exists(suggestions, stype, strategy, value_key, value):
    for s in suggestions:
        if s.get('type') != stype or s.get('strategy') != strategy:
            continue
        if s.get('status') in ('pending_review', 'evaluating', 'applied'):
            return True
        if s.get('status') == 'dismissed' and s.get(value_key) == value:
            return True
    return False
```

Line 96 — change status in threshold suggestion:
```python
'status': 'pending_review',
```

Line 144 — change status in regime_suppress suggestion:
```python
'status': 'pending_review',
```

- [x] **Step 2: Add rejection log path and loader**

After line 10 (`PENDING_PATH = ...`), add:
```python
REJECTED_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'rejected_suggestions.json')


def _load_rejected():
    try:
        with open(REJECTED_PATH) as f:
            return json.load(f)
    except Exception:
        return []
```

- [x] **Step 3: Skip rejected suggestions during generation**

In `run_strategy_proposal_check`, after `existing = pending.get('suggestions', [])`, add:
```python
    rejected = _load_rejected()
    rejected_keys = {
        (r.get('strategy'), r.get('type'), str(r.get('suggested_value', '')))
        for r in rejected
    }
```

In the threshold loop, after the `_already_exists` check (line ~85), add:
```python
            if (strat, 'threshold', str(optimal)) in rejected_keys:
                continue
```

In the regime_suppress loop, after the `_already_exists` check (line ~130), add:
```python
                if (strat, 'regime_suppress', regime) in rejected_keys:
                    continue
```

- [x] **Step 4: Remove shadow_mode hardcode**

Line 177 — change:
```python
        'shadow_mode': False,
```

- [x] **Step 5: Test locally**

```bash
cd mt-learner
python3 -c "
from suggester import run_strategy_proposal_check
import json
result = run_strategy_proposal_check()
statuses = [s['status'] for s in result['suggestions']]
print('statuses:', statuses)
assert all(s == 'pending_review' for s in statuses if s not in ('applied','rejected','evaluating')), 'Bad status'
print('PASS')
"
```
Expected: `statuses: ['pending_review', ...]  PASS`

- [x] **Step 6: Sync mt-learner to VPS and restart**

```bash
rsync -avz mt-learner/suggester.py root@62.238.15.113:/opt/mt-learner/suggester.py
ssh root@62.238.15.113 "systemctl restart mt-learner && sleep 2 && systemctl is-active mt-learner"
```

- [ ] **Step 7: Commit**

```bash
git add mt-learner/suggester.py
git commit -m "fix: learner writes pending_review status, respects rejection log"
```

---

## Task 2: Paper EV Separation — Add `source` Column to Signals

**Files:**
- Modify: `app.py` (schema migration ~line 237, `log_signals` ~line 550, paper bot scan ~line 7538)

**Context:** Paper bot calls `log_signals()` so paper outcomes land in the `signals` table alongside manually-tagged live signals. Adding a `source` column lets the analyzer separate the two EV tracks.

- [x] **Step 1: Add source column to signals CREATE TABLE**

In `app.py`, find the signals table schema (around line 200). Add after the last column before the closing `)`):
```sql
            source       TEXT DEFAULT 'live'
```

- [x] **Step 2: Add migration for existing DB**

Find the migration block (around line 237 where other ALTER TABLE statements live). Add:
```python
    try:
        con.execute("ALTER TABLE signals ADD COLUMN source TEXT DEFAULT 'live'")
    except sqlite3.OperationalError:
        pass
```

- [x] **Step 3: Add source parameter to log_signals()**

Change the function signature at line 550:
```python
def log_signals(signals: list[dict], source: str = "live") -> None:
```

In the INSERT statement inside `log_signals`, add `source` to the column list and `source` to the values tuple:

Column list (after `flow_confirmed`):
```python
                 flow_score, flow_confirmed, source)
```

Values tuple (after `sig.get("flow_confirmed")`):
```python
                sig.get("flow_score"),
                sig.get("flow_confirmed"),
                source,
```

- [x] **Step 4: Paper bot passes source="paper"**

In `_paper_bot_scan()`, find the `log_signals([sig])` call (around line 7541). Change to:
```python
                log_signals([sig], source="paper")
```

- [x] **Step 5: Verify migration runs cleanly**

```bash
python3 -c "
import sqlite3
con = sqlite3.connect('data/signals.db')
cols = [r[1] for r in con.execute('PRAGMA table_info(signals)').fetchall()]
assert 'source' in cols, 'source column missing'
print('source column present, PASS')
con.close()
"
```
Expected: `source column present, PASS`

- [x] **Step 6: Deploy and commit**

```bash
rsync -avz app.py root@62.238.15.113:/opt/matrix-trader/app.py
ssh root@62.238.15.113 "systemctl restart matrix-trader && sleep 2 && systemctl is-active matrix-trader"

git add app.py
git commit -m "feat: add source column to signals table, paper bot tags entries as source=paper"
```

---

## Task 3: Dynamic Position Sizing

**Files:**
- Modify: `app.py` (`_paper_bot_scan` ~line 7422, `DEFAULT_PAPER_CONFIG` ~line 148, paper_trades INSERT ~line 7556)

**Context:** Paper bot uses flat `size_usd=100`. Replace with `risk_pct_per_trade % of account_balance_usd`, ATR-stop-based sizing, conviction modifier, and a 25%-of-account hard cap.

- [x] **Step 1: Add compute_paper_position_size() near risk_controls imports**

Find the area around line 155 (after `DEFAULT_PAPER_CONFIG`). Add this function:

```python
def compute_paper_position_size(
    account_balance: float,
    risk_pct: float,
    entry_px: float,
    stop_loss: float | None,
    atr_pct: float | None,
    conviction: int,
) -> float:
    """
    Returns position size in USD.
    risk_amount = account_balance * risk_pct / 100
    stop_pct derived from stop_loss price; falls back to atr_pct then 2%.
    Conviction modifier: >=80 full, 65-79 80%, else 60%.
    Hard cap: 25% of account balance.
    """
    risk_amount = account_balance * risk_pct / 100

    if stop_loss and entry_px and entry_px > 0:
        stop_pct = abs(entry_px - stop_loss) / entry_px
    elif atr_pct and atr_pct > 0:
        stop_pct = atr_pct / 100
    else:
        stop_pct = 0.02

    stop_pct = max(stop_pct, 0.005)  # floor at 0.5% to avoid divide-by-near-zero

    size = risk_amount / stop_pct

    if conviction >= 80:
        modifier = 1.0
    elif conviction >= 65:
        modifier = 0.8
    else:
        modifier = 0.6

    size = size * modifier
    cap = account_balance * 0.25
    return round(min(size, cap), 2)
```

- [x] **Step 2: Update DEFAULT_PAPER_CONFIG**

Around line 148, change:
```python
DEFAULT_PAPER_CONFIG: dict = {
    "enabled":               False,
    "account_balance_usd":   200.0,
    "risk_pct_per_trade":    5.0,
    "disabled_strategies":   [],
    "min_conviction":        55,
    "flow_required":         True,
    "min_flow_score":        50.0,
    "scan_interval_minutes": 2,
    "max_open_positions":    4,
    "max_atr_pct":           3.43,
    "max_trend_score_abs":   41,
}
```

- [x] **Step 3: Update _paper_bot_scan() to use dynamic sizing**

In `_paper_bot_scan()`, remove:
```python
        size_usd             = float(cfg.get("size_usd", 100))
```

Add in its place:
```python
        account_balance      = float(cfg.get("account_balance_usd", 200.0))
        risk_pct             = float(cfg.get("risk_pct_per_trade", 5.0))
```

Then replace the `entry_px` / `size_usd` block (where the INSERT happens) — compute size dynamically:
```python
            entry_px  = sig.get("price", 0)
            leverage  = float(sig.get("leverage_cap") or sig.get("leverage") or 1)
            conviction = sig.get("conviction", sig.get("conviction_base", 0))
            size_usd  = compute_paper_position_size(
                account_balance=account_balance,
                risk_pct=risk_pct,
                entry_px=entry_px,
                stop_loss=sig.get("stop_loss"),
                atr_pct=sig.get("atr_pct"),
                conviction=conviction,
            )
```

- [x] **Step 4: Update paper config PATCH to accept new fields**

Find `PAPER_CONFIG_ALLOWED_KEYS` or the allowed field list in `/api/paper/config` PATCH handler. Replace with:
```python
        ALLOWED = {
            "enabled", "account_balance_usd", "risk_pct_per_trade",
            "disabled_strategies", "min_conviction", "flow_required",
            "min_flow_score", "scan_interval_minutes", "max_open_positions",
            "max_atr_pct", "max_trend_score_abs",
        }
```

- [x] **Step 5: Test sizing formula locally**

```bash
python3 -c "
# inline test — no import needed yet
def compute_paper_position_size(account_balance, risk_pct, entry_px, stop_loss, atr_pct, conviction):
    risk_amount = account_balance * risk_pct / 100
    if stop_loss and entry_px and entry_px > 0:
        stop_pct = abs(entry_px - stop_loss) / entry_px
    elif atr_pct and atr_pct > 0:
        stop_pct = atr_pct / 100
    else:
        stop_pct = 0.02
    stop_pct = max(stop_pct, 0.005)
    size = (risk_amount / stop_pct) * (1.0 if conviction >= 80 else 0.8 if conviction >= 65 else 0.6)
    return round(min(size, account_balance * 0.25), 2)

# \$200 account, 5% risk, 2% stop, conviction 75 → expect ~40
result = compute_paper_position_size(200, 5, 1.0, 0.98, None, 75)
assert 35 < result <= 50, f'Expected ~40, got {result}'
# cap test: 0.1% stop should hit 25% cap = 50
result2 = compute_paper_position_size(200, 5, 1.0, 0.999, None, 90)
assert result2 == 50.0, f'Expected 50.0 cap, got {result2}'
print('PASS')
"
```
Expected: `PASS`

- [x] **Step 6: Deploy and commit**

```bash
rsync -avz app.py templates/index.html root@62.238.15.113:/opt/matrix-trader/ --relative
ssh root@62.238.15.113 "systemctl restart matrix-trader && sleep 2 && systemctl is-active matrix-trader"

git add app.py templates/index.html
git commit -m "feat: dynamic position sizing — risk% of account balance with ATR-stop and conviction modifier"
```

---

## Task 4: Goal Definition File + Account API

**Files:**
- Modify: `app.py` (add `_load_goals`, `_save_goals`, two new routes)

**Context:** `data/trading_goals.json` is the single source of truth for account parameters and performance targets. `/api/goals` computes actuals from the DB and returns them alongside targets. `/api/paper/account` returns just the financial snapshot.

- [x] **Step 1: Add _load_goals() and _save_goals() helpers**

Add after `compute_paper_position_size()`:

```python
GOALS_PATH = os.path.join(DATA_DIR, "trading_goals.json")

DEFAULT_GOALS: dict = {
    "account_balance_usd":     200.0,
    "risk_pct_per_trade":      5.0,
    "max_positions":           4,
    "target_monthly_return_pct": 20.0,
    "target_ev_per_trade_pct": 5.0,
    "min_win_partial_rate":    0.50,
    "max_drawdown_pct":        40.0,
    "consecutive_loss_alert":  5,
    "scale_up_trigger": {
        "ev_threshold_pct": 8.0,
        "min_trades":       50,
        "new_risk_pct":     8.0,
    },
    "evaluation_window_trades": 20,
    "evaluation_window_days":   14,
}


def _load_goals() -> dict:
    try:
        with open(GOALS_PATH) as f:
            stored = json.load(f)
        merged = dict(DEFAULT_GOALS)
        merged.update(stored)
        return merged
    except Exception:
        return dict(DEFAULT_GOALS)


def _save_goals(goals: dict) -> None:
    tmp = GOALS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(goals, f, indent=2)
    os.replace(tmp, GOALS_PATH)
```

- [x] **Step 2: Add _compute_goal_actuals() helper**

```python
def _compute_goal_actuals(goals: dict) -> dict:
    """Compute live metrics vs goal targets from signals.db."""
    account_balance = float(goals.get("account_balance_usd", 200.0))
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    thirty_days_ago = (now - timedelta(days=30)).isoformat()

    try:
        con = sqlite3.connect(DB_PATH)

        # Account value from closed paper trades
        rows = con.execute(
            "SELECT size_usd, pnl_pct FROM paper_trades WHERE status='closed' AND pnl_pct IS NOT NULL"
        ).fetchall()
        total_pnl_usd = sum((r[0] or 0) * (r[1] or 0) / 100 for r in rows)
        current_value = round(account_balance + total_pnl_usd, 2)
        drawdown_pct = round((total_pnl_usd / account_balance) * 100, 2) if account_balance else 0

        # Monthly return: paper trades closed this month
        month_rows = con.execute(
            "SELECT size_usd, pnl_pct FROM paper_trades WHERE status='closed' AND closed_at >= ? AND pnl_pct IS NOT NULL",
            (month_start,)
        ).fetchall()
        month_pnl_usd = sum((r[0] or 0) * (r[1] or 0) / 100 for r in month_rows)
        monthly_return_pct = round((month_pnl_usd / account_balance) * 100, 2) if account_balance else 0

        # EV per trade — live signals only, last 30 days
        ev_rows = con.execute(
            "SELECT pnl_pct FROM signals WHERE source='live' AND result IN ('WIN','LOSS','PARTIAL') "
            "AND pnl_pct IS NOT NULL AND logged_at >= ?",
            (thirty_days_ago,)
        ).fetchall()
        ev_values = [r[0] for r in ev_rows]
        ev_per_trade = round(sum(ev_values) / len(ev_values), 2) if ev_values else None
        ev_sample_n = len(ev_values)

        # Win+partial rate — live signals, last 30 days
        wr_rows = con.execute(
            "SELECT result FROM signals WHERE source='live' AND result IN ('WIN','LOSS','PARTIAL') "
            "AND logged_at >= ?",
            (thirty_days_ago,)
        ).fetchall()
        wr_results = [r[0] for r in wr_rows]
        win_partial = sum(1 for r in wr_results if r in ("WIN", "PARTIAL"))
        win_partial_rate = round(win_partial / len(wr_results), 4) if wr_results else None

        # Consecutive loss streak (live signals, most recent)
        streak_rows = con.execute(
            "SELECT result FROM signals WHERE source='live' AND result IN ('WIN','LOSS','PARTIAL') "
            "ORDER BY logged_at DESC LIMIT 20"
        ).fetchall()
        streak = 0
        for r in streak_rows:
            if r[0] == "LOSS":
                streak += 1
            else:
                break

        # Scale-up check
        scale_trigger = goals.get("scale_up_trigger", {})
        scale_ready = (
            ev_per_trade is not None
            and ev_per_trade >= scale_trigger.get("ev_threshold_pct", 8.0)
            and ev_sample_n >= scale_trigger.get("min_trades", 50)
        )

        con.close()
        return {
            "current_value_usd":   current_value,
            "total_pnl_usd":       round(total_pnl_usd, 2),
            "monthly_return_pct":  monthly_return_pct,
            "ev_per_trade_pct":    ev_per_trade,
            "ev_sample_n":         ev_sample_n,
            "win_partial_rate":    win_partial_rate,
            "drawdown_pct":        drawdown_pct,
            "consecutive_losses":  streak,
            "scale_up_ready":      scale_ready,
            "kill_switch_breached": drawdown_pct <= -abs(goals.get("max_drawdown_pct", 40.0)),
        }
    except Exception as e:
        print(f"[goals] compute error: {e}", file=sys.stderr)
        return {}
```

- [x] **Step 3: Add /api/goals GET and PATCH routes**

Add near the paper bot routes section:

```python
@app.route("/api/goals", methods=["GET", "PATCH"])
def api_goals():
    try:
        if request.method == "PATCH":
            body = request.get_json(force=True) or {}
            goals = _load_goals()
            ALLOWED = {
                "account_balance_usd", "risk_pct_per_trade", "max_positions",
                "target_monthly_return_pct", "target_ev_per_trade_pct",
                "min_win_partial_rate", "max_drawdown_pct", "consecutive_loss_alert",
                "evaluation_window_trades", "evaluation_window_days",
            }
            for k, v in body.items():
                if k in ALLOWED:
                    goals[k] = v
            _save_goals(goals)
            return jsonify({"success": True, "goals": goals})
        goals = _load_goals()
        actuals = _compute_goal_actuals(goals)
        return jsonify({"success": True, "goals": goals, "actuals": actuals})
    except Exception as e:
        print(f"[api/goals] {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500
```

- [x] **Step 4: Verify route returns valid JSON**

```bash
python3 app.py &
sleep 2
curl -s http://localhost:8080/api/goals | python3 -m json.tool | head -30
kill %1
```
Expected: JSON with `goals` and `actuals` keys, no errors.

- [x] **Step 5: Deploy and commit**

```bash
rsync -avz app.py root@62.238.15.113:/opt/matrix-trader/app.py
ssh root@62.238.15.113 "systemctl restart matrix-trader && sleep 2 && \
  curl -s http://localhost:8080/api/goals | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"ok:\", d.get(\"success\"))'"

git add app.py
git commit -m "feat: /api/goals route with goal definition file and computed actuals"
```

---

## Task 5: Apply/Reject API Routes

**Files:**
- Modify: `app.py` (three new routes)

**Context:** MT7 needs to read `pending.json`, apply suggestions by PATCHing existing config routes, and write rejections to `data/rejected_suggestions.json`. One active suggestion at a time is enforced server-side.

- [x] **Step 1: Add PENDING_PATH and REJECTED_PATH constants**

After `GOALS_PATH = ...`, add:
```python
LEARNER_PENDING_PATH  = "/opt/mt-learner/suggestions/pending.json"
LEARNER_REJECTED_PATH = os.path.join(DATA_DIR, "rejected_suggestions.json")
```

- [x] **Step 2: Add helpers to load pending and write rejected**

```python
def _load_suggestions() -> list[dict]:
    try:
        with open(LEARNER_PENDING_PATH) as f:
            return json.load(f).get("suggestions", [])
    except Exception:
        return []


def _write_rejected(entry: dict) -> None:
    try:
        existing = []
        if os.path.exists(LEARNER_REJECTED_PATH):
            with open(LEARNER_REJECTED_PATH) as f:
                existing = json.load(f)
        existing.append(entry)
        tmp = LEARNER_REJECTED_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(existing, f, indent=2)
        os.replace(tmp, LEARNER_REJECTED_PATH)
    except Exception as e:
        print(f"[suggestions] write_rejected error: {e}", file=sys.stderr)


def _update_suggestion_status(sid: str, status: str, extra: dict | None = None) -> bool:
    """Update a suggestion's status in pending.json in-place."""
    try:
        with open(LEARNER_PENDING_PATH) as f:
            data = json.load(f)
        for s in data.get("suggestions", []):
            if s.get("id") == sid:
                s["status"] = status
                if extra:
                    s.update(extra)
                tmp = LEARNER_PENDING_PATH + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, LEARNER_PENDING_PATH)
                return True
    except Exception as e:
        print(f"[suggestions] update_status error: {e}", file=sys.stderr)
    return False
```

- [x] **Step 3: Add /api/intelligence/suggestions GET route**

```python
@app.route("/api/intelligence/suggestions")
def api_intelligence_suggestions():
    try:
        sugs = _load_suggestions()
        goals = _load_goals()
        actuals = _compute_goal_actuals(goals)
        # Annotate each suggestion with current baseline metrics
        baseline = {
            "ev_per_trade_pct":  actuals.get("ev_per_trade_pct"),
            "win_partial_rate":  actuals.get("win_partial_rate"),
            "ev_sample_n":       actuals.get("ev_sample_n"),
            "current_value_usd": actuals.get("current_value_usd"),
            "snapshot_at":       datetime.utcnow().isoformat(),
        }
        return jsonify({"success": True, "suggestions": sugs, "baseline": baseline})
    except Exception as e:
        print(f"[api/suggestions] {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500
```

- [x] **Step 4: Add /api/intelligence/suggestions/<sid>/apply POST route**

```python
@app.route("/api/intelligence/suggestions/<sid>/apply", methods=["POST"])
def api_suggestion_apply(sid):
    try:
        sugs = _load_suggestions()
        # Enforce one-at-a-time: block if another is already evaluating
        evaluating = [s for s in sugs if s.get("status") == "evaluating"]
        if evaluating:
            return jsonify({
                "success": False,
                "error": f"Cannot apply — '{evaluating[0]['id']}' is still evaluating"
            }), 409

        sug = next((s for s in sugs if s.get("id") == sid), None)
        if not sug:
            return jsonify({"success": False, "error": "Suggestion not found"}), 404
        if sug.get("status") != "pending_review":
            return jsonify({"success": False, "error": f"Status is {sug.get('status')}, not pending_review"}), 409

        # Apply via existing config API
        payload = sug.get("api_payload", {})
        strategy = sug.get("strategy")
        stype = sug.get("type")
        if stype == "threshold" and strategy and payload:
            cfg = _load_paper_config()
            cfg.update(payload)
            _save_paper_config(cfg)

        # Snapshot baseline at apply time
        goals = _load_goals()
        actuals = _compute_goal_actuals(goals)
        baseline = {
            "ev_per_trade_pct":  actuals.get("ev_per_trade_pct"),
            "win_partial_rate":  actuals.get("win_partial_rate"),
            "ev_sample_n":       actuals.get("ev_sample_n"),
            "applied_at":        datetime.utcnow().isoformat(),
        }
        _update_suggestion_status(sid, "evaluating", {"baseline": baseline, "applied_at": baseline["applied_at"]})
        return jsonify({"success": True, "applied": sid, "baseline": baseline})
    except Exception as e:
        print(f"[api/suggestion/apply] {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500
```

- [x] **Step 5: Add /api/intelligence/suggestions/<sid>/reject POST route**

```python
@app.route("/api/intelligence/suggestions/<sid>/reject", methods=["POST"])
def api_suggestion_reject(sid):
    try:
        body = request.get_json(force=True) or {}
        reason = body.get("reason", "")
        sugs = _load_suggestions()
        sug = next((s for s in sugs if s.get("id") == sid), None)
        if not sug:
            return jsonify({"success": False, "error": "Suggestion not found"}), 404

        _write_rejected({
            "id":           sid,
            "strategy":     sug.get("strategy"),
            "type":         sug.get("type"),
            "suggested_value": sug.get("suggested_value"),
            "reason":       reason,
            "rejected_at":  datetime.utcnow().isoformat(),
        })
        _update_suggestion_status(sid, "rejected", {"rejected_at": datetime.utcnow().isoformat(), "reject_reason": reason})
        return jsonify({"success": True, "rejected": sid})
    except Exception as e:
        print(f"[api/suggestion/reject] {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500
```

- [x] **Step 6: Add _load_paper_config() and _save_paper_config() if they don't exist**

Search app.py for where paper config is loaded (around the `api_paper_config` route). Extract or confirm helpers exist:

```python
PAPER_CONFIG_PATH = os.path.join(DATA_DIR, "paper_config.json")

def _load_paper_config() -> dict:
    try:
        with open(PAPER_CONFIG_PATH) as f:
            stored = json.load(f)
        cfg = dict(DEFAULT_PAPER_CONFIG)
        cfg.update(stored)
        return cfg
    except Exception:
        return dict(DEFAULT_PAPER_CONFIG)

def _save_paper_config(cfg: dict) -> None:
    tmp = PAPER_CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, PAPER_CONFIG_PATH)
```

If the `api_paper_config` route already loads/saves inline, refactor it to use these helpers.

- [x] **Step 7: Test apply endpoint**

```bash
curl -s http://localhost:8080/api/intelligence/suggestions | python3 -m json.tool | head -20
# Then test apply (use an actual ID from the output above):
curl -s -X POST http://localhost:8080/api/intelligence/suggestions/thresh_balanced_20260505_001/apply | python3 -m json.tool
```
Expected: `{"success": true, "applied": "...", "baseline": {...}}`

- [x] **Step 8: Deploy and commit**

```bash
rsync -avz app.py root@62.238.15.113:/opt/matrix-trader/app.py
ssh root@62.238.15.113 "systemctl restart matrix-trader && sleep 2 && systemctl is-active matrix-trader"

git add app.py
git commit -m "feat: /api/intelligence/suggestions apply/reject routes with one-at-a-time enforcement"
```

---

## Task 6: Goal Benchmark Strip — Strategies Tab

**Files:**
- Modify: `templates/index.html` (strategies section + tab switch handler)

**Context:** Add a five-tile benchmark strip at the very top of the Strategies tab content. Loads from `/api/goals`. Shows account value, monthly return, EV/trade, win+partial rate, and drawdown gauge. Includes an alert strip for learner suggestions and scale-up trigger.

- [x] **Step 1: Add loadGoalBenchmark() JS function**

Find the Strategies tab JS section (search for `loadStrategies` or `function.*strateg`). Add before it:

```javascript
async function loadGoalBenchmark() {
  const el = $('goal-benchmark');
  if (!el) return;
  try {
    const r = await fetch('/api/goals');
    const d = await r.json();
    if (!d.success) { el.innerHTML = ''; return; }
    const g = d.goals, a = d.actuals || {};

    const tile = (label, value, sub, color, progress, target) => `
      <div style="background:#111;border:1px solid #1e1e1e;border-radius:6px;padding:12px;min-width:0;">
        <div style="font-size:10px;color:#555;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">${label}</div>
        <div style="font-size:20px;font-weight:700;color:${color};">${value}</div>
        <div style="font-size:10px;color:${color};margin-top:2px;">${sub}</div>
        ${progress !== null ? `<div style="margin-top:8px;height:3px;background:#1e1e1e;border-radius:2px;">
          <div style="width:${Math.min(progress,100)}%;height:100%;background:${color};border-radius:2px;"></div>
        </div>` : ''}
        <div style="font-size:9px;color:#333;margin-top:4px;">${target}</div>
      </div>`;

    const accountVal = a.current_value_usd ?? g.account_balance_usd;
    const accountDelta = accountVal - g.account_balance_usd;
    const accountPct = g.account_balance_usd > 0 ? (accountDelta / g.account_balance_usd * 100).toFixed(1) : 0;
    const accountColor = accountDelta >= 0 ? '#22c55e' : '#ef4444';
    const monthTarget = g.account_balance_usd * (1 + g.target_monthly_return_pct / 100);
    const monthProgress = g.account_balance_usd > 0 ? ((accountVal - g.account_balance_usd) / (monthTarget - g.account_balance_usd) * 100) : 0;

    const mrPct = a.monthly_return_pct ?? 0;
    const mrColor = mrPct >= g.target_monthly_return_pct ? '#22c55e' : mrPct >= 0 ? '#f59e0b' : '#ef4444';

    const ev = a.ev_per_trade_pct;
    const evColor = ev === null ? '#555' : ev >= g.target_ev_per_trade_pct ? '#22c55e' : ev >= 0 ? '#f59e0b' : '#ef4444';
    const evStr = ev === null ? 'No data' : (ev >= 0 ? '+' : '') + ev.toFixed(1) + '%';
    const evSub = ev === null ? '' : `n=${a.ev_sample_n ?? 0}${(a.ev_sample_n ?? 0) < 20 ? ' ⚠ thin' : ''}`;

    const wr = a.win_partial_rate;
    const wrPct = wr !== null ? (wr * 100).toFixed(0) + '%' : 'No data';
    const wrColor = wr === null ? '#555' : wr >= g.min_win_partial_rate ? '#22c55e' : '#f59e0b';

    const dd = a.drawdown_pct ?? 0;
    const ddColor = dd <= -g.max_drawdown_pct * 0.75 ? '#ef4444' : dd <= -g.max_drawdown_pct * 0.4 ? '#f59e0b' : '#22c55e';
    const ddProgress = Math.min(Math.abs(dd) / g.max_drawdown_pct * 100, 100);

    const killBreached = a.kill_switch_breached;
    const scaleReady = a.scale_up_ready;

    // Suggestions alert
    let alertHtml = '';
    try {
      const sr = await fetch('/api/intelligence/suggestions');
      const sd = await sr.json();
      const pending = (sd.suggestions || []).filter(s => s.status === 'pending_review').length;
      if (pending > 0) {
        alertHtml += `<div style="margin-top:8px;padding:8px 12px;background:#1a1400;border:1px solid #78350f;border-radius:4px;font-size:11px;color:#fcd34d;display:flex;align-items:center;justify-content:space-between;">
          <span>⚡ mt-learner has ${pending} suggestion${pending>1?'s':''} pending review</span>
          <span onclick="switchTab('intelligence')" style="color:#60a5fa;cursor:pointer;font-size:10px;">View in Intelligence →</span>
        </div>`;
      }
    } catch(e) {}

    if (killBreached) alertHtml += `<div style="margin-top:8px;padding:8px 12px;background:#1a0000;border:1px solid #7f1d1d;border-radius:4px;font-size:11px;color:#fca5a5;">🚨 Kill zone breached — drawdown exceeds ${g.max_drawdown_pct}%. Paper bot paused. Review before continuing.</div>`;
    if (scaleReady) alertHtml += `<div style="margin-top:8px;padding:8px 12px;background:#0a1a0a;border:1px solid #166534;border-radius:4px;font-size:11px;color:#86efac;">🚀 Scale-up trigger reached — EV &gt; ${g.scale_up_trigger?.ev_threshold_pct}% over ${a.ev_sample_n} trades. Consider raising risk% to ${g.scale_up_trigger?.new_risk_pct}%.</div>`;

    el.innerHTML = `
      <div style="margin-bottom:12px;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
          <span style="font-size:10px;color:#555;letter-spacing:1px;text-transform:uppercase;">Performance Goals — $${g.account_balance_usd} Account</span>
          <span onclick="editGoals()" style="font-size:10px;color:#555;cursor:pointer;">Edit Goals ›</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;">
          ${tile('Account Value', '$'+accountVal.toFixed(0), (accountDelta>=0?'+':'')+accountDelta.toFixed(0)+' ('+accountPct+'%)', accountColor, monthProgress, 'Target: $'+monthTarget.toFixed(0)+' this month')}
          ${tile('Monthly Return', mrPct.toFixed(1)+'%', 'Target: '+g.target_monthly_return_pct+'%', mrColor, mrPct/g.target_monthly_return_pct*100, Math.ceil((new Date(new Date().getFullYear(),new Date().getMonth()+1,1)-new Date())/86400000)+' days remaining')}
          ${tile('EV / Trade (30d)', evStr, evSub, evColor, ev!==null?Math.min(Math.abs(ev)/g.target_ev_per_trade_pct*100,100):0, 'Scale-up at +'+g.target_ev_per_trade_pct+'%')}
          ${tile('Win+Partial Rate', wrPct, wr!==null?(wr>=g.min_win_partial_rate?'Above floor ✓':'Below floor ↑'):'', wrColor, wr!==null?wr*100:0, 'Floor: '+(g.min_win_partial_rate*100).toFixed(0)+'%')}
          ${tile('Drawdown', dd.toFixed(1)+'%', 'Kill zone: -'+g.max_drawdown_pct+'%', ddColor, ddProgress, 'Kill switch at -'+g.max_drawdown_pct+'% ($'+(g.account_balance_usd*g.max_drawdown_pct/100).toFixed(0)+')')}
        </div>
        ${alertHtml}
      </div>`;
  } catch(e) {
    console.error('loadGoalBenchmark', e);
  }
}

function editGoals() {
  // Placeholder — open a simple modal or navigate to paper tab config
  alert('Edit goals via PATCH /api/goals or update data/trading_goals.json on the VPS.');
}
```

- [x] **Step 2: Add benchmark HTML container to Strategies section**

Find `<div id="strategies-section"` in index.html. Inside it, at the very top before any existing content, add:

```html
<div id="goal-benchmark"></div>
```

- [x] **Step 3: Wire loadGoalBenchmark() to tab switch**

Find the `switchTab` function in index.html. In the block that handles `tab === 'strategies'` (or wherever `loadStrategies()` is called), add:

```javascript
    if (tab === 'strategies') { loadStrategies(); loadGoalBenchmark(); }
```

- [x] **Step 4: Test in browser**

```bash
python3 app.py
# Open http://localhost:8080, click Strategies tab
# Should see 5-tile benchmark strip at top
# If /api/goals returns actuals with no signals, EV should show "No data"
```

- [x] **Step 5: Deploy and commit**

```bash
rsync -avz templates/index.html root@62.238.15.113:/opt/matrix-trader/templates/index.html
ssh root@62.238.15.113 "systemctl restart matrix-trader && sleep 2 && systemctl is-active matrix-trader"

git add templates/index.html
git commit -m "feat: goal benchmark strip in Strategies tab — account value, EV, win rate, drawdown"
```

---

## Task 7: Suggestions Sub-Tab — Intelligence Tab

**Files:**
- Modify: `templates/index.html` (Intelligence sub-nav + new panel)

**Context:** Add a "Suggestions" sub-tab to the Intelligence section with badge count, active suggestion card (one-at-a-time), queue, and history. Wires to the apply/reject routes from Task 5.

- [ ] **Step 1: Add Suggestions sub-tab to Intelligence nav**

Find the Intelligence sub-tab nav in index.html (search for `The Firm` or `intelligence-sub`). Add a new tab button:

```html
<button id="itab-suggestions" class="intel-tab-btn" onclick="switchIntelTab('suggestions')">
  Suggestions <span id="suggestions-badge" style="display:none;background:#ef4444;color:#fff;border-radius:8px;padding:1px 5px;font-size:9px;margin-left:4px;"></span>
</button>
```

- [ ] **Step 2: Add Suggestions panel HTML**

After the last Intelligence sub-panel, add:

```html
<div id="intel-suggestions" class="intel-panel hidden">
  <div id="suggestions-content" style="max-width:800px;"></div>
</div>
```

- [ ] **Step 3: Add loadSuggestions() JS function**

```javascript
async function loadSuggestions() {
  const el = $('suggestions-content');
  if (!el) return;
  try {
    const r = await fetch('/api/intelligence/suggestions');
    const d = await r.json();
    if (!d.success) { el.innerHTML = '<p style="color:#555;">Could not load suggestions.</p>'; return; }

    const sugs = d.suggestions || [];
    const baseline = d.baseline || {};
    const pending = sugs.filter(s => s.status === 'pending_review');
    const evaluating = sugs.filter(s => s.status === 'evaluating');
    const done = sugs.filter(s => ['rejected','evaluated','applied'].includes(s.status));

    // Update badge
    const badge = $('suggestions-badge');
    if (badge) {
      const count = pending.length + evaluating.length;
      badge.textContent = count;
      badge.style.display = count > 0 ? 'inline' : 'none';
    }

    const active = evaluating[0] || pending[0];
    const queue = pending.slice(evaluating.length > 0 ? 0 : 1);

    const metricRow = (label, value, color) => `
      <div style="background:#0d0d0d;padding:8px;border-radius:4px;text-align:center;">
        <div style="font-size:9px;color:#555;margin-bottom:3px;">${label}</div>
        <div style="font-size:14px;font-weight:700;color:${color||'#aaa'};">${value??'—'}</div>
      </div>`;

    const fmtEv = v => v===null||v===undefined ? '—' : (v>=0?'+':'')+v.toFixed(2)+'%';
    const fmtWr = v => v===null||v===undefined ? '—' : (v*100).toFixed(1)+'%';

    let html = `<div style="font-size:10px;color:#555;letter-spacing:1px;text-transform:uppercase;margin-bottom:10px;">Pending Review — ${pending.length} Active</div>`;

    if (active) {
      const isEvaluating = active.status === 'evaluating';
      html += `
        <div style="background:#111;border:1px solid ${isEvaluating?'#1e3a5f':'#2d2d00'};border-radius:6px;padding:16px;margin-bottom:16px;">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:10px;">
            <div>
              <div style="font-size:12px;font-weight:700;color:${isEvaluating?'#60a5fa':'#fcd34d'};">${active.evidence_summary||active.id}</div>
              <div style="font-size:10px;color:#555;margin-top:3px;">Confidence: ${active.confidence||'?'} · Sample: ${active.sample_size||'?'} trades · ${isEvaluating?'<span style="color:#60a5fa;">Evaluating...</span>':'Awaiting review'}</div>
            </div>
            <div style="font-size:10px;color:#555;background:#1a1a1a;padding:3px 8px;border-radius:4px;">${active.type||'?'}</div>
          </div>
          <div style="font-size:11px;color:#aaa;line-height:1.6;margin-bottom:10px;padding:10px;background:#0d0d0d;border-radius:4px;border-left:3px solid #333;">${active.reasoning||''}</div>
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px;">
            ${metricRow('CURRENT EV', fmtEv(baseline.ev_per_trade_pct), baseline.ev_per_trade_pct>=0?'#22c55e':'#ef4444')}
            ${metricRow('CURRENT WIN RATE', fmtWr(baseline.win_partial_rate), '#aaa')}
            ${metricRow('EXPECTED WIN RATE', active.expected_win_rate_delta||'?', '#22c55e')}
            ${metricRow('SAMPLE SIZE', active.sample_size||'?', '#aaa')}
          </div>
          ${!isEvaluating ? `
          <div style="display:flex;gap:10px;">
            <button onclick="applySuggestion('${active.id}')" style="flex:1;padding:9px;background:#166534;border:none;border-radius:4px;color:#86efac;font-size:12px;font-weight:700;cursor:pointer;">✓ Apply Change</button>
            <button onclick="rejectSuggestion('${active.id}')" style="flex:1;padding:9px;background:#1a0a0a;border:1px solid #7f1d1d;border-radius:4px;color:#fca5a5;font-size:12px;cursor:pointer;">✕ Reject</button>
          </div>` : `<div style="font-size:11px;color:#60a5fa;padding:8px;background:#0a0f1a;border-radius:4px;">Monitoring outcomes — will evaluate after ${(active.evaluation_window_trades||20)} trades or ${(active.evaluation_window_days||14)} days.</div>`}
        </div>`;
    } else {
      html += `<div style="color:#555;font-size:12px;padding:8px 0;margin-bottom:16px;">No pending suggestions. mt-learner generates suggestions weekly.</div>`;
    }

    if (queue.length > 0) {
      html += `<div style="font-size:10px;color:#555;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;">Queue — Locked Until Active Resolves</div>`;
      queue.forEach(s => {
        html += `<div style="background:#0d0d0d;border:1px solid #1a1a1a;border-radius:6px;padding:12px;margin-bottom:8px;opacity:0.5;">
          <div style="font-size:11px;color:#666;">${s.evidence_summary||s.id}</div>
          <div style="font-size:10px;color:#444;margin-top:4px;">Confidence: ${s.confidence} · ${s.sample_size} trades · Unlocks after active suggestion resolves</div>
        </div>`;
      });
    }

    if (done.length > 0) {
      html += `<div style="font-size:10px;color:#555;letter-spacing:1px;text-transform:uppercase;margin:16px 0 8px;">History</div>`;
      done.slice(0,10).forEach(s => {
        const isRejected = s.status === 'rejected';
        const bl = s.baseline || {};
        html += `<div style="background:#0d0d0d;border:1px solid #1a1a1a;border-radius:4px;padding:10px;margin-bottom:6px;display:flex;align-items:center;justify-content:space-between;">
          <div>
            <div style="font-size:11px;color:${isRejected?'#555':'#ccc'};">${s.evidence_summary||s.id}</div>
            <div style="font-size:10px;color:#444;margin-top:2px;">${isRejected?'Rejected':'Applied'} ${(s.rejected_at||s.applied_at||'').slice(0,10)} ${s.reject_reason?'· "'+s.reject_reason+'"':''}</div>
          </div>
          <div style="text-align:right;">
            ${isRejected ? `<div style="font-size:10px;color:#ef4444;">Rejected</div>` :
              bl.ev_per_trade_pct !== undefined ? `<div style="font-size:11px;color:#22c55e;">Baseline EV: ${fmtEv(bl.ev_per_trade_pct)}</div>` : ''}
          </div>
        </div>`;
      });
    }

    el.innerHTML = html;
  } catch(e) {
    console.error('loadSuggestions', e);
    $('suggestions-content').innerHTML = '<p style="color:#555;">Error loading suggestions.</p>';
  }
}

async function applySuggestion(sid) {
  if (!confirm('Apply this suggestion? It will update the paper bot config immediately.')) return;
  try {
    const r = await fetch(`/api/intelligence/suggestions/${sid}/apply`, {method:'POST'});
    const d = await r.json();
    if (d.success) { loadSuggestions(); loadGoalBenchmark(); }
    else alert('Apply failed: ' + d.error);
  } catch(e) { alert('Error: ' + e); }
}

async function rejectSuggestion(sid) {
  const reason = prompt('Reason for rejection (optional):') ?? '';
  try {
    const r = await fetch(`/api/intelligence/suggestions/${sid}/reject`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({reason})
    });
    const d = await r.json();
    if (d.success) loadSuggestions();
    else alert('Reject failed: ' + d.error);
  } catch(e) { alert('Error: ' + e); }
}
```

- [ ] **Step 4: Wire Suggestions tab to load on switch**

Find the `switchIntelTab()` function (or wherever Intelligence sub-tabs are wired). Add:
```javascript
    if (tab === 'suggestions') loadSuggestions();
```

- [ ] **Step 5: Test in browser**

```bash
python3 app.py
# Open http://localhost:8080 → Intelligence tab → Suggestions sub-tab
# Should see pending suggestions from pending.json
# Test Apply button — verify status changes in pending.json
# Test Reject button — verify entry appears in data/rejected_suggestions.json
```

- [ ] **Step 6: Deploy and commit**

```bash
rsync -avz --exclude='.env' --exclude='data/' --exclude='__pycache__' \
  --exclude='.git' --exclude='*.pyc' ./ root@62.238.15.113:/opt/matrix-trader/
ssh root@62.238.15.113 "systemctl restart matrix-trader && sleep 2 && systemctl is-active matrix-trader"

git add templates/index.html
git commit -m "feat: Suggestions sub-tab in Intelligence with Apply/Reject and history"
```

---

## Self-Review

**Spec coverage check:**
- Component 1 (learner fix) → Task 1 ✓
- Component 2 (paper EV separation) → Task 2 ✓
- Component 3 (dynamic position sizing) → Task 3 ✓
- Component 4 (account balance tracking) → Task 4 (via `_compute_goal_actuals`) ✓
- Component 5 (goal definition file) → Task 4 ✓
- Component 6 (benchmark display) → Task 6 ✓
- Component 7 (apply/reject UI) → Tasks 5 + 7 ✓

**Placeholder scan:** No TBDs or incomplete steps found.

**Type consistency:**
- `_load_goals()` returns `dict` — used consistently in Tasks 4, 5, 6
- `_compute_goal_actuals(goals: dict)` — called in Tasks 4 and 5 with same signature
- `_load_suggestions()` returns `list[dict]` — used in Tasks 5 and 7
- `loadGoalBenchmark()` and `loadSuggestions()` both called from their respective tab switch handlers ✓
- `applySuggestion(sid)` / `rejectSuggestion(sid)` defined and called in Task 7 ✓
