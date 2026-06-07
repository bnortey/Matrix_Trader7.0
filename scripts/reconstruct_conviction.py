"""
A/B reconstruction harness for SCORE_VERSION v1 (legacy step) vs v2 (continuous ramp).

Background
----------
The audit (§02) flagged that Stage 1 of score_ticker() awards flat-tier scores
(weak / strong / nothing) per input, making conviction a 3–4-value step function
that mathematically can't separate winners from losers by more than a fraction
of a point. The measured 0.56-point divergence between WIN and LOSS conviction
averages is, the audit argued, the structural ceiling — not a tuning problem.

This script replays both v1 and v2 scoring on every closed signal in
data/signals.db using the inputs already captured in `signal_json`, then
reports the winner/loser conviction divergence under each version side-by-side.

Goal: confirm v2 widens divergence beyond the ~0.56 point floor BEFORE flipping
SCORE_VERSION=v2 in production .env. The audit's stated success target is
≥ 3 points; I'd call ≥ 1.5 points "real signal, ship it".

Usage
-----
  python3 scripts/reconstruct_conviction.py
  python3 scripts/reconstruct_conviction.py --db data/signals.db
  python3 scripts/reconstruct_conviction.py --strategy balanced --direction LONG
  python3 scripts/reconstruct_conviction.py --json > recon.json

This file deliberately duplicates the STRATEGIES dict and the _ramp_score helper
from app.py. CLAUDE.md prohibits importing app.py from offline scripts because
that triggers Flask startup. Duplication is the price; keep this script in sync
with the source-of-truth weights in app.py if they ever change.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Mirrored from app.py — keep these in sync if STRATEGIES weights change.
# ---------------------------------------------------------------------------

STRATEGIES = {
    "balanced": {
        "weights": {"momentum": 30, "funding": 25, "basis": 15, "volume_mult": 1.1},
        "min_conviction": 65,
    },
    "funding_arb": {
        "weights": {"momentum": 10, "funding": 50, "basis": 20, "volume_mult": 1.0},
        "min_conviction": 76,
        "filters": {"min_funding_abs": 0.0003},
    },
    "momentum_breakout": {
        "weights": {"momentum": 50, "funding": 10, "basis": 5, "volume_mult": 1.2},
        "min_conviction": 55,
        "filters": {"min_24h_change_pct": 3.0},
    },
    "mean_reversion": {
        "weights": {"momentum": 5, "funding": 30, "basis": 30, "volume_mult": 1.0},
        "min_conviction": 65,
    },
}


def _ramp_score(value, weak_threshold, strong_threshold,
                weak_weight, strong_weight, saturation=1.4):
    """Mirror of _ramp_score in app.py — see that file for design notes."""
    a = abs(value)
    if a < weak_threshold:
        return 0.0
    if a < strong_threshold:
        span = strong_threshold - weak_threshold
        if span <= 0:
            return float(weak_weight)
        frac = (a - weak_threshold) / span
        return float(weak_weight + frac * (strong_weight - weak_weight))
    if strong_threshold <= 0:
        return float(strong_weight)
    excess_frac = (a - strong_threshold) / strong_threshold
    cap = strong_weight * saturation
    return float(min(cap, strong_weight + math.log1p(excess_frac) * (cap - strong_weight)))


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------

def _score_v1(change_pct, funding, basis_pct, volume, strategy_key, w):
    """v1 step function — exact mirror of app.py legacy branch."""
    mom_strong = w["momentum"]
    mom_weak = w["momentum"] // 2
    if strategy_key == "balanced":
        mom_long_strong = mom_strong // 2
        mom_long_weak = mom_weak // 2
    else:
        mom_long_strong = mom_strong
        mom_long_weak = mom_weak

    long_score = 0
    short_score = 0
    if change_pct > 5:
        long_score += mom_long_strong
    elif change_pct > 2:
        long_score += mom_long_weak
    elif change_pct < -5:
        short_score += mom_strong
    elif change_pct < -2:
        short_score += mom_weak

    fund_strong = w["funding"]
    fund_weak = int(w["funding"] * 0.4)
    if funding < -0.001:
        if strategy_key == "balanced" and change_pct <= 0:
            long_score += fund_weak
        else:
            long_score += fund_strong
    elif funding < 0:
        long_score += fund_weak
    elif funding > 0.001:
        short_score += fund_strong
    elif funding > 0:
        short_score += fund_weak

    if basis_pct > 0.1:
        short_score += w["basis"]
    elif basis_pct < -0.1:
        long_score += w["basis"]

    if strategy_key == "balanced" and short_score > 0:
        short_score = int(short_score * 1.08)

    vol_mult = w["volume_mult"] if volume > 10_000_000 else 1.0
    if long_score >= short_score:
        return min(int(long_score * vol_mult), 100), "LONG"
    return min(int(short_score * vol_mult), 100), "SHORT"


def _score_v2(change_pct, funding, basis_pct, volume, strategy_key, w):
    """v2 continuous ramp — exact mirror of app.py SCORE_VERSION=v2 branch."""
    mom_strong = w["momentum"]
    mom_weak = w["momentum"] // 2
    if strategy_key == "balanced":
        mom_long_strong = mom_strong // 2
        mom_long_weak = mom_weak // 2
    else:
        mom_long_strong = mom_strong
        mom_long_weak = mom_weak

    long_score = 0.0
    short_score = 0.0

    # Momentum
    if change_pct > 0:
        long_score += _ramp_score(change_pct, 2.0, 5.0, mom_long_weak, mom_long_strong)
    elif change_pct < 0:
        short_score += _ramp_score(change_pct, 2.0, 5.0, mom_weak, mom_strong)

    # Funding
    fund_strong = w["funding"]
    fund_weak = int(w["funding"] * 0.4)
    if funding != 0:
        fund_score = _ramp_score(funding, 0.0001, 0.001, fund_weak, fund_strong)
        if funding < 0:
            if funding < -0.001 and strategy_key == "balanced" and change_pct <= 0:
                fund_score *= 0.4
            long_score += fund_score
        else:
            short_score += fund_score

    # Basis
    basis_score = _ramp_score(basis_pct, 0.05, 0.10, w["basis"] * 0.5, w["basis"])
    if basis_pct > 0:
        short_score += basis_score
    elif basis_pct < 0:
        long_score += basis_score

    # Balanced SHORT bias multiplier (mirrors app.py line ~1449)
    if strategy_key == "balanced" and short_score > 0:
        short_score *= 1.08

    vol_mult = w["volume_mult"] if volume > 10_000_000 else 1.0
    if long_score >= short_score:
        return min(int(long_score * vol_mult), 100), "LONG"
    return min(int(short_score * vol_mult), 100), "SHORT"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _extract_inputs(row):
    """
    Pull (change_pct, funding, basis_pct, volume, strategy_key) from a signal row.

    Schema reality (verified against data/signals.db 2026-05-15):
      - change_24h_pct is NOT a table column — lives in signal_json only.
      - basis_pct IS stored in signal_json (computed at scan time from
        price/fair_price, which is itself often null in storage).
      - funding_rate is duplicated: column on signals + key in signal_json.
        We prefer the column.
      - strategy_key column exists. Older rows have only `strategy` (display name).
    """
    try:
        sj = json.loads(row["signal_json"]) if row["signal_json"] else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        sj = {}

    change_pct = sj.get("change_24h_pct")
    funding = row["funding_rate"]
    if funding is None:
        funding = sj.get("funding_rate")

    # basis_pct: stored in signal_json but null for all sampled historical rows
    # (fair_price was also null). Treat absence as 0 — that's the correct neutral
    # value because the v1 step-function awarded 0 points for |basis_pct| < 0.1
    # anyway. v2 will likewise award 0 below the weak threshold (0.05). So
    # historical reconstruction with basis_pct=0 matches what actually scored.
    basis_pct = sj.get("basis_pct")
    if basis_pct is None:
        price = sj.get("price") or row["price"]
        fair = sj.get("fair_price")
        if price and fair and fair > 0:
            basis_pct = (price - fair) / fair * 100
        else:
            basis_pct = 0.0

    volume = sj.get("volume_24h") or 0.0

    strategy_key = row["strategy_key"]
    if not strategy_key:
        strat = (sj.get("strategy") or row["strategy"] or "balanced").lower()
        strategy_key = {
            "balanced": "balanced",
            "funding arb": "funding_arb",
            "momentum breakout": "momentum_breakout",
            "mean reversion": "mean_reversion",
        }.get(strat, strat)

    # Only the truly load-bearing inputs (change_pct, funding) must be present.
    # Without those, we can't reproduce the score at all.
    if change_pct is None or funding is None:
        return None

    return {
        "change_pct": float(change_pct),
        "funding": float(funding),
        "basis_pct": float(basis_pct),
        "volume": float(volume),
        "strategy_key": strategy_key,
    }


def load_closed_signals(db_path, strategy_filter=None, direction_filter=None):
    """Yield rows for signals with terminal outcomes."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    sql = """
        SELECT id, symbol, strategy, strategy_key, direction, conviction,
               price, funding_rate, result, pnl_pct,
               signal_json
        FROM signals
        WHERE result IN ('WIN', 'LOSS', 'PARTIAL')
    """
    params = []
    if strategy_filter:
        sql += " AND (strategy = ? OR strategy_key = ?)"
        params.extend([strategy_filter, strategy_filter])
    if direction_filter:
        sql += " AND direction = ?"
        params.append(direction_filter.upper())
    sql += " ORDER BY id ASC"
    for row in con.execute(sql, params):
        yield row
    con.close()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _classify_outcome(result):
    if result in ("WIN", "PARTIAL"):
        return "winner"
    if result == "LOSS":
        return "loser"
    return "other"


def summarize(divergences):
    out = {}
    for version, by_outcome in divergences.items():
        winners = by_outcome.get("winner", [])
        losers = by_outcome.get("loser", [])
        w_mean = statistics.mean(winners) if winners else float("nan")
        l_mean = statistics.mean(losers) if losers else float("nan")
        w_sd = statistics.stdev(winners) if len(winners) > 1 else 0.0
        l_sd = statistics.stdev(losers) if len(losers) > 1 else 0.0
        divergence = w_mean - l_mean
        out[version] = {
            "n_winners": len(winners),
            "n_losers": len(losers),
            "winner_mean": round(w_mean, 3),
            "winner_sd": round(w_sd, 3),
            "loser_mean": round(l_mean, 3),
            "loser_sd": round(l_sd, 3),
            "divergence": round(divergence, 3),
        }
    return out


def print_table(summary):
    print(f"{'version':>10} {'n_win':>6} {'n_loss':>7} "
          f"{'win_mean':>10} {'loss_mean':>10} {'divergence':>12}")
    print("-" * 64)
    for ver, s in summary.items():
        print(
            f"{ver:>10} {s['n_winners']:>6} {s['n_losers']:>7} "
            f"{s['winner_mean']:>10.3f} {s['loser_mean']:>10.3f} "
            f"{s['divergence']:>+12.3f}"
        )
    v1 = summary.get("v1", {})
    v2 = summary.get("v2", {})
    if v1 and v2:
        delta = v2.get("divergence", 0) - v1.get("divergence", 0)
        print()
        print(f"v2 - v1 divergence delta: {delta:+.3f} points")
        if delta >= 3.0:
            print("→ STRONG signal: v2 meaningfully separates winners/losers. Ship it.")
        elif delta >= 1.5:
            print("→ MODEST signal: v2 widens divergence. Consider shadow rollout.")
        elif delta > 0:
            print("→ MARGINAL: v2 helps but not decisively. More data needed.")
        else:
            print("→ NEGATIVE: v2 does NOT improve separation. Do not enable.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/signals.db")
    parser.add_argument("--strategy", default=None,
                        help="Filter to one strategy (e.g. balanced, funding_arb)")
    parser.add_argument("--direction", default=None, choices=[None, "LONG", "SHORT"])
    parser.add_argument("--json", action="store_true",
                        help="Print machine-readable JSON instead of a table")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-strategy breakdown too")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: {db} does not exist", file=sys.stderr)
        return 2

    # Per-strategy divergence buckets so we can see if v2 helps Balanced
    # specifically (where the audit said the ceiling is most binding).
    per_strategy_divs = defaultdict(
        lambda: {"v1": defaultdict(list), "v2": defaultdict(list)}
    )
    overall = {"v1": defaultdict(list), "v2": defaultdict(list)}

    n_scanned = 0
    n_used = 0
    for row in load_closed_signals(db, args.strategy, args.direction):
        n_scanned += 1
        inputs = _extract_inputs(row)
        if not inputs:
            continue
        skey = inputs["strategy_key"]
        if skey not in STRATEGIES:
            continue
        w = STRATEGIES[skey]["weights"]
        v1_conv, _ = _score_v1(
            inputs["change_pct"], inputs["funding"], inputs["basis_pct"],
            inputs["volume"], skey, w,
        )
        v2_conv, _ = _score_v2(
            inputs["change_pct"], inputs["funding"], inputs["basis_pct"],
            inputs["volume"], skey, w,
        )
        outcome = _classify_outcome(row["result"])
        if outcome in ("winner", "loser"):
            overall["v1"][outcome].append(v1_conv)
            overall["v2"][outcome].append(v2_conv)
            per_strategy_divs[skey]["v1"][outcome].append(v1_conv)
            per_strategy_divs[skey]["v2"][outcome].append(v2_conv)
            n_used += 1

    summary = summarize(overall)

    if args.json:
        payload = {
            "scanned": n_scanned,
            "used": n_used,
            "overall": summary,
            "per_strategy": {
                k: summarize(v) for k, v in per_strategy_divs.items()
            },
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(f"# A/B reconstruction — {db}")
    print(f"# Scanned: {n_scanned} closed signals")
    print(f"# Usable:  {n_used} (had reconstructible inputs in signal_json)")
    print()
    print("## Overall")
    print_table(summary)

    if args.verbose:
        print()
        print("## Per-strategy")
        for skey in sorted(per_strategy_divs):
            print(f"\n### {skey}")
            print_table(summarize(per_strategy_divs[skey]))

    return 0


if __name__ == "__main__":
    sys.exit(main())
