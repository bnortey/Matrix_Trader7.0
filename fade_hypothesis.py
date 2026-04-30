#!/usr/bin/env python3
"""
Standalone read-only fade-hypothesis analysis on closed signals in data/signals.db.

Tests whether inverting the direction of high-fragility signals would have
produced positive cumulative P&L instead of the observed losses. This is a
Phase 1 validation script — it does NOT build a strategy, it tells you whether
the strategy is worth building.

Usage:  python3 fade_hypothesis.py [--db-path PATH] [--out-path PATH]
Output: formatted terminal report + data/fade_hypothesis_report.json

ASSUMPTION (printed at top of report):
  Inverse trade P&L is approximated as -(original P&L) * (1 - haircut).
  This is approximate because the actual inverse trade would have hit
  different SL/TP levels than the original. We test multiple haircut levels
  to gauge robustness — a finding that holds at 40% haircut is far more
  trustworthy than one that only works at 0% haircut.

Does NOT import from app.py. Does NOT make API calls. Read-only on the DB.
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = "data/signals.db"
DEFAULT_OUT_PATH = "data/fade_hypothesis_report.json"

# Haircut sensitivity levels — multiplicative reduction on inverse P&L
# 0.0 = no haircut (most optimistic), 0.4 = 40% haircut (conservative)
HAIRCUT_LEVELS = [0.0, 0.2, 0.4]

# Symbol fade-list thresholds — a symbol qualifies if it has enough trades to
# be statistically meaningful AND its cumulative P&L is materially negative.
# Win rate alone is misleading because partials can keep a low-WR symbol
# profitable. P&L-based filtering is the correct criterion.
FADE_LIST_MIN_TRADES = 5
FADE_LIST_MAX_PNL = -50.0  # cumulative leveraged % P&L must be at most this

# Minimum sample size for an inverse-edge finding to be statistically meaningful
MIN_SAMPLE_FOR_TRUST = 20


# ---------------------------------------------------------------------------
# Args + DB
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--db-path", default=DEFAULT_DB_PATH,
                   help=f"Path to signals.db (default: {DEFAULT_DB_PATH})")
    p.add_argument("--out-path", default=DEFAULT_OUT_PATH,
                   help=f"Path to JSON report (default: {DEFAULT_OUT_PATH})")
    p.add_argument("--strategy", default="balanced",
                   help="Strategy to fade (default: balanced — set to 'all' for all strategies)")
    return p.parse_args()


def open_db_readonly(db_path: str) -> sqlite3.Connection:
    abs_path = os.path.abspath(db_path)
    if not os.path.exists(abs_path):
        sys.exit(f"ERROR: DB not found at {abs_path}")
    uri = f"file:{abs_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def fetch_closed_signals(con: sqlite3.Connection, strategy: str) -> list[dict]:
    """Pull all closed signals with valid pnl_pct, optionally scoped to one strategy."""
    base_query = """
        SELECT id, logged_at, symbol, strategy_key, conviction, volatility,
               tags, pnl_pct, leverage, result, direction,
               entry1, tp1, exit_price, funding_rate, atr_pct, rsi_1h
        FROM signals
        WHERE result IS NOT NULL AND pnl_pct IS NOT NULL
    """
    if strategy != "all":
        base_query += " AND strategy_key=?"
        rows = con.execute(base_query + " ORDER BY logged_at ASC", (strategy,)).fetchall()
    else:
        rows = con.execute(base_query + " ORDER BY logged_at ASC").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_tags(tag_str) -> set[str]:
    if not tag_str:
        return set()
    return {t.strip() for t in str(tag_str).split(",") if t.strip()}


def build_fade_list(signals: list[dict]) -> set[str]:
    """Symbols where strategy has >= FADE_LIST_MIN_TRADES trades AND
    cumulative leveraged P&L <= FADE_LIST_MAX_PNL. P&L-based, not WR-based —
    win rate alone is misleading because partials can keep low-WR profitable."""
    by_symbol = defaultdict(list)
    for s in signals:
        by_symbol[s["symbol"]].append(s)

    fade_list = set()
    for symbol, trades in by_symbol.items():
        if len(trades) < FADE_LIST_MIN_TRADES:
            continue
        cum_pnl = sum(float(t["pnl_pct"]) for t in trades)
        if cum_pnl <= FADE_LIST_MAX_PNL:
            fade_list.add(symbol)
    return fade_list


def compute_inverse_pnl(original_pnl: float, haircut: float) -> float:
    """Approximate the inverse trade's leveraged P&L.

    Assumption: a SHORT version of a LONG that lost X% would have gained ~X%.
    Approximate because the inverse trade's actual SL/TP path differs.
    Haircut accounts for: (a) slippage on inverse fills,
    (b) asymmetric volatility (rallies blow off harder than dumps),
    (c) path-dependent execution differences.
    """
    return -original_pnl * (1.0 - haircut)


# ---------------------------------------------------------------------------
# Fragility signatures — return True if the signal matches
# ---------------------------------------------------------------------------

def sig_a_extreme_vol(s, fade_list):
    return s.get("volatility") == "extreme"


def sig_b_extreme_vol_plus_tags(s, fade_list):
    if s.get("volatility") != "extreme":
        return False
    tags = parse_tags(s.get("tags"))
    return bool(tags & {"strong_momentum", "short_squeeze", "extreme_vol"})


def sig_c_extreme_vol_plus_funding(s, fade_list):
    if s.get("volatility") != "extreme":
        return False
    fr = s.get("funding_rate")
    if fr is None:
        return False
    try:
        return abs(float(fr)) >= 0.001
    except (TypeError, ValueError):
        return False


def sig_d_symbol_fade_list(s, fade_list):
    return s.get("symbol") in fade_list


def sig_e_combined(s, fade_list):
    return sig_a_extreme_vol(s, fade_list) or sig_d_symbol_fade_list(s, fade_list)


def sig_f_high_or_extreme_vol(s, fade_list):
    return s.get("volatility") in {"high", "extreme"}


SIGNATURES = [
    ("A: Extreme volatility only",              sig_a_extreme_vol),
    ("B: Extreme vol + momentum/squeeze tags",  sig_b_extreme_vol_plus_tags),
    ("C: Extreme vol + |funding| >= 0.1%",      sig_c_extreme_vol_plus_funding),
    ("D: Symbol fade list (historical)",        sig_d_symbol_fade_list),
    ("E: Extreme vol OR fade list (combined)",  sig_e_combined),
    ("F: High OR extreme volatility (broader)", sig_f_high_or_extreme_vol),
]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_signature(name, signature_fn, signals, fade_list, haircut):
    matched = [s for s in signals if signature_fn(s, fade_list)]

    if not matched:
        return {
            "name": name, "haircut": haircut,
            "matched_count": 0, "matched_pct_of_total": 0.0,
            "original_total_pnl": 0.0, "inverse_total_pnl": 0.0,
            "edge_gained": 0.0,
            "original_win_rate": 0.0, "inverse_win_rate": 0.0,
            "long_count": 0, "short_count": 0,
            "long_pnl_original": 0.0, "short_pnl_original": 0.0,
            "top_symbols": [],
        }

    original_pnls = [float(s["pnl_pct"]) for s in matched]
    inverse_pnls = [compute_inverse_pnl(p, haircut) for p in original_pnls]

    original_total = sum(original_pnls)
    inverse_total = sum(inverse_pnls)

    original_wins = sum(1 for p in original_pnls if p > 0)
    inverse_wins = sum(1 for p in inverse_pnls if p > 0)

    long_signals = [s for s in matched if s.get("direction") == "LONG"]
    short_signals = [s for s in matched if s.get("direction") == "SHORT"]
    long_pnl_orig = sum(float(s["pnl_pct"]) for s in long_signals)
    short_pnl_orig = sum(float(s["pnl_pct"]) for s in short_signals)

    by_symbol = defaultdict(lambda: {"count": 0, "inv_pnl": 0.0, "orig_pnl": 0.0})
    for s, inv in zip(matched, inverse_pnls):
        sym = s["symbol"]
        by_symbol[sym]["count"] += 1
        by_symbol[sym]["inv_pnl"] += inv
        by_symbol[sym]["orig_pnl"] += float(s["pnl_pct"])
    top_symbols = sorted(by_symbol.items(), key=lambda kv: kv[1]["inv_pnl"], reverse=True)[:5]

    return {
        "name": name,
        "haircut": haircut,
        "matched_count": len(matched),
        "matched_pct_of_total": round(len(matched) / len(signals) * 100, 1),
        "original_total_pnl": round(original_total, 1),
        "inverse_total_pnl": round(inverse_total, 1),
        "edge_gained": round(inverse_total - original_total, 1),
        "original_win_rate": round(original_wins / len(matched) * 100, 1),
        "inverse_win_rate": round(inverse_wins / len(matched) * 100, 1),
        "long_count": len(long_signals),
        "short_count": len(short_signals),
        "long_pnl_original": round(long_pnl_orig, 1),
        "short_pnl_original": round(short_pnl_orig, 1),
        "top_symbols": [
            {"symbol": sym, "count": d["count"],
             "original_pnl": round(d["orig_pnl"], 1),
             "inverse_pnl": round(d["inv_pnl"], 1)}
            for sym, d in top_symbols
        ],
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_header(strategy_filter: str):
    print("=" * 78)
    print("  Matrix Trader 7.0 — Fade Hypothesis Analysis")
    print(f"  Generated: {datetime.now(timezone.utc).isoformat()}")
    print(f"  Strategy scope: {strategy_filter}")
    print("=" * 78)
    print()
    print("ASSUMPTION (read carefully):")
    print("  Inverse trade P&L is approximated as -(original P&L) * (1 - haircut).")
    print("  Approximate because the actual inverse trade would have hit different")
    print("  SL/TP levels. Multiple haircut levels test robustness — a finding that")
    print("  holds at 40% haircut is more trustworthy than one only at 0% haircut.")
    print()
    print(f"  Sample-size threshold for trust: >= {MIN_SAMPLE_FOR_TRUST} matched signals.")
    print(f"  Findings on smaller samples are flagged but not recommended.")
    print()


def print_signature_block(results):
    name = results[0]["name"]
    print("-" * 78)
    print(f"  {name}")
    print("-" * 78)

    if results[0]["matched_count"] == 0:
        print(f"  No signals matched this signature.")
        print()
        return

    flag = "" if results[0]["matched_count"] >= MIN_SAMPLE_FOR_TRUST else "  ⚠ small sample"
    print(f"  Matched signals:    {results[0]['matched_count']} "
          f"({results[0]['matched_pct_of_total']:.1f}% of all closed signals){flag}")
    print(f"  Direction split:    LONG={results[0]['long_count']} "
          f"(orig P&L {results[0]['long_pnl_original']:+.1f}%)  /  "
          f"SHORT={results[0]['short_count']} "
          f"(orig P&L {results[0]['short_pnl_original']:+.1f}%)")
    print(f"  Original cum P&L:   {results[0]['original_total_pnl']:+.1f}%")
    print(f"  Original win rate:  {results[0]['original_win_rate']:.1f}%")
    print()
    print(f"  {'Haircut':>10}  {'Inverse P&L':>14}  {'Edge gained':>14}  {'Inverse WR':>12}")
    for r in results:
        print(f"  {r['haircut']*100:>8.0f}%   "
              f"{r['inverse_total_pnl']:>+12.1f}%   "
              f"{r['edge_gained']:>+12.1f}%   "
              f"{r['inverse_win_rate']:>11.1f}%")
    print()
    print(f"  Top 5 symbols by inverse P&L (at 0% haircut):")
    print(f"    {'Symbol':<15} {'Trades':>7}  {'Original P&L':>14}  {'Inverse P&L':>14}")
    for ts in results[0]["top_symbols"]:
        print(f"    {ts['symbol']:<15} {ts['count']:>7}  "
              f"{ts['original_pnl']:>+13.1f}%  {ts['inverse_pnl']:>+13.1f}%")
    print()


def print_recommendation(all_results):
    """Pick the best signature at the most conservative haircut and verdict on it."""
    print("=" * 78)
    print("  RECOMMENDATION")
    print("=" * 78)

    # Filter to results with adequate sample size at 40% haircut
    candidates = [
        r for sig in all_results for r in sig
        if r["haircut"] == 0.4 and r["matched_count"] >= MIN_SAMPLE_FOR_TRUST
    ]
    if not candidates:
        print("  No signature has >= {} matched signals at the conservative haircut.".format(
            MIN_SAMPLE_FOR_TRUST))
        print("  VERDICT: Sample too small to draw conclusions. Keep accumulating signals.")
        print()
        return

    best = max(candidates, key=lambda r: r["edge_gained"])

    print(f"  Best signature at 40% haircut: {best['name']}")
    print(f"  Matched signals:   {best['matched_count']}")
    print(f"  Original P&L:      {best['original_total_pnl']:+.1f}%")
    print(f"  Inverse P&L:       {best['inverse_total_pnl']:+.1f}%")
    print(f"  Edge gained:       {best['edge_gained']:+.1f}%")
    print(f"  Inverse win rate:  {best['inverse_win_rate']:.1f}%")
    print()

    if best["edge_gained"] > 500 and best["inverse_win_rate"] > 50:
        verdict = "STRONG"
        action = "Worth building as a paper strategy. Phase 2 from prior plan."
    elif best["edge_gained"] > 100 and best["inverse_win_rate"] > 45:
        verdict = "MODERATE"
        action = "Worth a smaller-scope test. Build with tighter risk controls."
    elif best["edge_gained"] > 0:
        verdict = "WEAK"
        action = "Edge exists but is fragile. Do NOT build as primary strategy."
    else:
        verdict = "NEGATIVE"
        action = "Hypothesis fails conservative testing. Do not build."

    print(f"  VERDICT ({verdict}): {action}")
    print()


def main():
    args = parse_args()
    print_header(args.strategy)

    print(f"Reading: {os.path.abspath(args.db_path)}")
    con = open_db_readonly(args.db_path)

    sample = con.execute("""
        SELECT symbol, strategy_key, volatility, tags, pnl_pct, direction
        FROM signals
        WHERE result IS NOT NULL AND pnl_pct IS NOT NULL
        LIMIT 3
    """).fetchall()
    print("\nSchema sight-check (first 3 closed signals):")
    if sample:
        for r in sample:
            d = dict(r)
            print(f"  {d['symbol']:<14} {d['strategy_key']:<22} vol={d['volatility']:<8} "
                  f"dir={d['direction']:<6} pnl={d['pnl_pct']:+.1f}%")
    else:
        print("  (no closed signals with pnl_pct found)")
    print()

    signals = fetch_closed_signals(con, args.strategy)
    con.close()

    if not signals:
        print(f"No closed signals with valid pnl_pct found for strategy={args.strategy!r}.")
        print("If running locally on dev DB, run backfill on the VPS first.")
        return

    total_pnl = sum(float(s["pnl_pct"]) for s in signals)
    print(f"Total closed signals (strategy={args.strategy}): {len(signals)}")
    print(f"Total cumulative P&L:                          {total_pnl:+.1f}%")
    print()

    fade_list = build_fade_list(signals)
    print(f"Historical fade list ({args.strategy} symbols with >= {FADE_LIST_MIN_TRADES} "
          f"trades and cumulative P&L <= {FADE_LIST_MAX_PNL:+.0f}%):")
    if fade_list:
        for sym in sorted(fade_list):
            sym_trades = [s for s in signals if s["symbol"] == sym]
            sym_pnl = sum(float(s["pnl_pct"]) for s in sym_trades)
            sym_wr = sum(1 for s in sym_trades if s["result"] == "WIN") / len(sym_trades) * 100
            print(f"  - {sym:<14} {len(sym_trades):>3} trades, "
                  f"{sym_wr:>4.1f}% WR, P&L {sym_pnl:+.1f}%")
    else:
        print("  (empty)")
    print()

    all_results = []
    for sig_name, sig_fn in SIGNATURES:
        sig_results = []
        for hc in HAIRCUT_LEVELS:
            result = evaluate_signature(sig_name, sig_fn, signals, fade_list, hc)
            sig_results.append(result)
        all_results.append(sig_results)
        print_signature_block(sig_results)

    print_recommendation(all_results)

    # Self-check: sum of inverse + sum of original at 0% haircut on signature A should equal 0
    sig_a_zero = next((r for sig in all_results for r in sig
                       if r["name"].startswith("A:") and r["haircut"] == 0.0), None)
    if sig_a_zero and sig_a_zero["matched_count"] > 0:
        check_sum = sig_a_zero["original_total_pnl"] + sig_a_zero["inverse_total_pnl"]
        if abs(check_sum) > 0.5:
            print(f"  ⚠ Self-check warning: at 0% haircut, original+inverse should sum to ~0, got {check_sum:+.1f}")
        else:
            print(f"  ✓ Self-check: at 0% haircut, original+inverse = {check_sum:+.1f} (ok)")
    print()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": os.path.abspath(args.db_path),
        "strategy_scope": args.strategy,
        "total_signals": len(signals),
        "total_pnl": round(total_pnl, 1),
        "fade_list": sorted(fade_list),
        "min_sample_for_trust": MIN_SAMPLE_FOR_TRUST,
        "haircut_levels_tested": HAIRCUT_LEVELS,
        "signatures": [
            {"name": sig[0]["name"], "haircuts": sig}
            for sig in all_results
        ],
    }

    out_path = Path(args.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"JSON report: {os.path.abspath(args.out_path)}")


if __name__ == "__main__":
    main()
