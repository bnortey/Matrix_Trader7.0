"""
Pure risk math functions for P9 execution readiness layer.
No Flask, no app.py imports, no network calls.
All functions return safe defaults on any error — never raise.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Thresholds (from mt-learner findings: winners avg 9.3/3.2%, losers 14.1/5.5%)
# ---------------------------------------------------------------------------

TREND_SCORE_GREEN  = 12    # <= green
TREND_SCORE_AMBER  = 16    # <= amber, > green
ATR_GREEN          = 3.5   # % — <= green
ATR_AMBER          = 5.5   # % — <= amber, > green
SIGNAL_AGE_GREEN   = 5     # minutes
SIGNAL_AGE_AMBER   = 15    # minutes
DAILY_LOSS_AMBER   = -2.0  # % of account
DAILY_LOSS_RED     = -4.0  # % of account


def compute_daily_pnl(db_path: str) -> dict:
    """
    Sum today's realized pnl_pct for all closed signals.
    Returns {"total_pnl_pct": float, "wins": int, "losses": int, "count": int}
    Safe — returns zeros on any DB error.
    """
    try:
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute("""
            SELECT
                COUNT(*) as n,
                COALESCE(SUM(pnl_pct), 0) as total_pnl,
                SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pct <= 0 THEN 1 ELSE 0 END) as losses
            FROM signals
            WHERE result IS NOT NULL
              AND pnl_pct IS NOT NULL
              AND DATE(entry_at) = ?
        """, (today_utc,))
        row = cur.fetchone()
        con.close()
        if row:
            return {
                "total_pnl_pct": round(float(row[1] or 0), 2),
                "wins":  int(row[2] or 0),
                "losses": int(row[3] or 0),
                "count": int(row[0] or 0),
            }
    except Exception:
        pass
    return {"total_pnl_pct": 0.0, "wins": 0, "losses": 0, "count": 0}


def compute_position_size(
    account_size: float,
    risk_pct: float,
    entry: float,
    stop_loss: float,
    leverage: int,
) -> dict:
    """
    Compute recommended position size given account and signal parameters.
    Returns {"notional": float, "contracts": float, "risk_dollars": float}
    """
    try:
        if account_size <= 0 or entry <= 0 or stop_loss <= 0 or leverage <= 0:
            return {"notional": 0, "contracts": 0, "risk_dollars": 0}
        risk_dollars = account_size * (risk_pct / 100)
        stop_distance_pct = abs(entry - stop_loss) / entry
        if stop_distance_pct <= 0:
            return {"notional": 0, "contracts": 0, "risk_dollars": 0}
        # position_size = risk_dollars / stop_distance_pct / leverage_effective
        # effective leverage already baked into stop, so just: notional = risk / stop%
        notional = risk_dollars / stop_distance_pct
        contracts = notional / entry
        return {
            "notional": round(notional, 2),
            "contracts": round(contracts, 4),
            "risk_dollars": round(risk_dollars, 2),
        }
    except Exception:
        return {"notional": 0, "contracts": 0, "risk_dollars": 0}


def _check_signal_age(entry_at: str | None) -> tuple[str, str]:
    """Returns (status, label) where status is 'green'|'amber'|'red'."""
    try:
        if not entry_at:
            # Unlogged / fresh scan signal — age is seconds from now, treat as green
            return "green", "Fresh scan signal"
        # entry_at is ISO UTC without Z suffix
        ts = datetime.fromisoformat(entry_at.replace("Z", "")).replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60
        if age_min <= SIGNAL_AGE_GREEN:
            return "green", f"Signal age {age_min:.0f}m"
        if age_min <= SIGNAL_AGE_AMBER:
            return "amber", f"Signal age {age_min:.0f}m — verify still valid"
        return "red", f"Signal age {age_min:.0f}m — rescan before trading"
    except Exception:
        return "green", "Fresh scan signal"


def get_readiness_verdict(
    signal: dict,
    daily_pnl_pct: float,
) -> dict:
    """
    Run all pre-flight checks. Returns a readiness dict consumed by the frontend.

    Returns:
        {
            "verdict": "READY" | "REVIEW" | "PASS",
            "verdict_color": "#00e676" | "#ffab40" | "#ff5252",
            "checks": [{"label": str, "status": "green"|"amber"|"red", "detail": str}, ...]
        }
    """
    checks = []

    # 1. Signal age
    age_status, age_detail = _check_signal_age(signal.get("entry_at"))
    checks.append({"label": "Signal age", "status": age_status, "detail": age_detail})

    # 2. Trend score (lower = better — learner validated)
    ts = signal.get("trend_score")
    if ts is None:
        checks.append({"label": "Trend setup", "status": "amber", "detail": "No trend data"})
    else:
        ts = abs(int(ts))  # trend_score can be negative; check magnitude
        if ts <= TREND_SCORE_GREEN:
            checks.append({"label": "Trend setup", "status": "green",
                           "detail": f"trend_score {ts} ≤ {TREND_SCORE_GREEN} (winner avg 9)"})
        elif ts <= TREND_SCORE_AMBER:
            checks.append({"label": "Trend setup", "status": "amber",
                           "detail": f"trend_score {ts} — borderline"})
        else:
            checks.append({"label": "Trend setup", "status": "red",
                           "detail": f"trend_score {ts} > {TREND_SCORE_AMBER} (loser avg 14)"})

    # 3. ATR (lower = better — learner validated)
    atr = signal.get("atr_pct")
    if atr is None:
        checks.append({"label": "ATR / volatility", "status": "amber", "detail": "No ATR data"})
    else:
        atr = float(atr)
        if atr <= ATR_GREEN:
            checks.append({"label": "ATR / volatility", "status": "green",
                           "detail": f"ATR {atr:.1f}% ≤ {ATR_GREEN}% (winner avg 3.2%)"})
        elif atr <= ATR_AMBER:
            checks.append({"label": "ATR / volatility", "status": "amber",
                           "detail": f"ATR {atr:.1f}% — wider stops needed"})
        else:
            checks.append({"label": "ATR / volatility", "status": "red",
                           "detail": f"ATR {atr:.1f}% > {ATR_AMBER}% (loser avg 5.5%)"})

    # 4. Volatility regime
    vol = (signal.get("volatility") or "").lower()
    vol_map = {"low": "green", "medium": "green", "high": "amber", "extreme": "red", "choppy": "amber"}
    vol_status = vol_map.get(vol, "amber")
    vol_detail = {
        "green": f"{vol} volatility — favorable",
        "amber": f"{vol} volatility — reduce size",
        "red":   f"{vol} volatility — extreme noise risk",
    }[vol_status]
    checks.append({"label": "Volatility regime", "status": vol_status, "detail": vol_detail})

    # 5. Daily P&L gate
    if daily_pnl_pct >= 0:
        checks.append({"label": "Daily P&L", "status": "green",
                       "detail": f"Today: {daily_pnl_pct:+.1f}%"})
    elif daily_pnl_pct >= DAILY_LOSS_AMBER:
        checks.append({"label": "Daily P&L", "status": "green",
                       "detail": f"Today: {daily_pnl_pct:+.1f}%"})
    elif daily_pnl_pct >= DAILY_LOSS_RED:
        checks.append({"label": "Daily P&L", "status": "amber",
                       "detail": f"Today: {daily_pnl_pct:+.1f}% — approaching loss limit"})
    else:
        checks.append({"label": "Daily P&L", "status": "red",
                       "detail": f"Today: {daily_pnl_pct:+.1f}% — daily loss limit reached"})

    # Verdict
    reds   = sum(1 for c in checks if c["status"] == "red")
    ambers = sum(1 for c in checks if c["status"] == "amber")

    if reds >= 2 or (reds == 1 and daily_pnl_pct <= DAILY_LOSS_RED):
        verdict, color = "PASS", "#ff5252"
    elif reds == 1 or ambers >= 3:
        verdict, color = "REVIEW", "#ffab40"
    else:
        verdict, color = "READY", "#00e676"

    return {"verdict": verdict, "verdict_color": color, "checks": checks}
