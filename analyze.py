#!/usr/bin/env python3
"""
Standalone read-only audit of closed signals in data/signals.db.

Usage:  python3 analyze.py
Output: formatted terminal report + data/audit_report.json

Does NOT import from app.py. Does NOT make API calls.
Does NOT write to data/signals.db (read-only).

Schema notes (verified against live schema):
  - Volatility stored as column "volatility" (not "volatility_regime")
  - Tags stored as comma-separated string (not JSON)
  - tp1/entry1/leverage are direct columns; signal_json used only as
    fallback source for leverage when the column is NULL
"""

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DB_PATH     = Path(__file__).parent / "data" / "signals.db"
OUTPUT_PATH = Path(__file__).parent / "data" / "audit_report.json"

REGIMES = ["low", "medium", "high", "extreme", "unknown"]
CONV_BANDS = [
    ("55-64",  55, 65),
    ("65-74",  65, 75),
    ("75-84",  75, 85),
    ("85+",    85, 999),
]

W = 76  # report width


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sep() -> None:
    print("─" * W)


def pct_str(n: int, d: int) -> str:
    return f"{n / d * 100:5.1f}%" if d > 0 else "  n/a "


def fmt_pnl(val) -> str:
    if val is None:
        return "     n/a"
    return f"{val:+8.1f}"


def parse_tags(tags_str) -> list:
    if not tags_str:
        return []
    return [t.strip() for t in str(tags_str).split(",") if t.strip()]


def agg(rows: list) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "win_rate": None, "partial_rate": None,
                "avg_pnl": None, "total_pnl": None}
    wins     = sum(1 for r in rows if r["result"] == "WIN")
    partials = sum(1 for r in rows if r["result"] == "PARTIAL")
    pnl_vals = [r["pnl_pct"] for r in rows if r["pnl_pct"] is not None]
    return {
        "n":            n,
        "win_rate":     wins / n * 100,
        "partial_rate": partials / n * 100,
        "avg_pnl":      sum(pnl_vals) / len(pnl_vals) if pnl_vals else None,
        "total_pnl":    sum(pnl_vals) if pnl_vals else None,
    }


def print_agg_header(col1: str = "label", col1_w: int = 20) -> None:
    print(f"  {col1:<{col1_w}} {'n':>5}  {'win%':>6}  {'par%':>6}  {'avg_pnl':>8}  {'total_pnl':>10}")
    sep()


def print_agg_row(label: str, a: dict, col1_w: int = 20) -> None:
    if a["n"] == 0:
        return
    wr = f"{a['win_rate']:5.1f}%" if a["win_rate"] is not None else "  n/a "
    pr = f"{a['partial_rate']:5.1f}%" if a["partial_rate"] is not None else "  n/a "
    ap = fmt_pnl(a["avg_pnl"])
    tp = fmt_pnl(a["total_pnl"])
    print(f"  {label:<{col1_w}} {a['n']:>5}  {wr}  {pr}  {ap}  {tp}")


# ---------------------------------------------------------------------------
# Data load
# ---------------------------------------------------------------------------

def load_signals() -> tuple:
    """
    Returns (rows_with_pnl, excluded_count).
    Opens DB read-only via URI mode.
    """
    uri = f"file:{DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row

    rows = [dict(r) for r in con.execute(
        "SELECT * FROM signals WHERE result IS NOT NULL AND pnl_pct IS NOT NULL"
    ).fetchall()]

    excluded = con.execute(
        "SELECT COUNT(*) FROM signals WHERE result IS NOT NULL AND pnl_pct IS NULL"
    ).fetchone()[0]

    con.close()

    # Pre-parse tags list once so we never re-split in inner loops
    for r in rows:
        r["_tags"] = parse_tags(r.get("tags"))
        # Normalise strategy_key: fall back to 'balanced' if NULL
        if not r.get("strategy_key"):
            r["strategy_key"] = "balanced"

    return rows, excluded


# ---------------------------------------------------------------------------
# Section 1: Volatility regime breakdown
# ---------------------------------------------------------------------------

def section_regime(rows: list, strategy_key=None) -> dict:
    subset = rows if strategy_key is None else [r for r in rows if r["strategy_key"] == strategy_key]
    result = {}
    for regime in REGIMES:
        if regime == "unknown":
            group = [r for r in subset if not r.get("volatility")]
        else:
            group = [r for r in subset if r.get("volatility") == regime]
        result[regime] = agg(group)
    return result


# ---------------------------------------------------------------------------
# Section 2: Conviction band breakdown
# ---------------------------------------------------------------------------

def section_conviction(rows: list, strategy_key=None) -> dict:
    subset = rows if strategy_key is None else [r for r in rows if r["strategy_key"] == strategy_key]
    result = {}
    for label, lo, hi in CONV_BANDS:
        group = [r for r in subset if lo <= (r.get("conviction") or 0) < hi]
        result[label] = agg(group)
    return result


# ---------------------------------------------------------------------------
# Section 3: Tag breakdown
# ---------------------------------------------------------------------------

def section_tags(rows: list, strategy_key=None) -> list:
    subset = rows if strategy_key is None else [r for r in rows if r["strategy_key"] == strategy_key]

    all_tags = set()
    for r in subset:
        all_tags.update(r["_tags"])

    if not all_tags:
        return []

    # Build per-tag membership once (set of ids that have each tag)
    tag_ids: dict = defaultdict(set)
    for r in subset:
        for t in r["_tags"]:
            tag_ids[t].add(r["id"])

    tag_results = []
    for tag in sorted(all_tags):
        ids_with = tag_ids[tag]
        with_rows    = [r for r in subset if r["id"] in ids_with]
        without_rows = [r for r in subset if r["id"] not in ids_with]
        wa = agg(with_rows)
        wo = agg(without_rows)
        delta = (wa["total_pnl"] or 0.0) - (wo["total_pnl"] or 0.0)
        tag_results.append({
            "tag":        tag,
            "with":       wa,
            "without":    wo,
            "edge_delta": delta,
        })

    tag_results.sort(key=lambda x: x["edge_delta"], reverse=True)
    return tag_results


# ---------------------------------------------------------------------------
# Section 4: Symbol blacklist candidates
# ---------------------------------------------------------------------------

def section_blacklist(rows: list) -> list:
    groups: dict = defaultdict(list)
    for r in rows:
        groups[(r["symbol"], r["strategy_key"])].append(r)

    candidates = []
    for (symbol, strat_key), group in groups.items():
        n = len(group)
        if n < 3:
            continue
        a = agg(group)
        win_rate  = a["win_rate"] or 0.0
        total_pnl = a["total_pnl"] or 0.0
        if win_rate < 25.0 or total_pnl < -100.0:
            candidates.append({
                "symbol":       symbol,
                "strategy_key": strat_key,
                "n_closed":     n,
                "win_rate":     win_rate,
                "total_pnl":    total_pnl,
            })

    candidates.sort(key=lambda x: x["total_pnl"])
    return candidates


# ---------------------------------------------------------------------------
# Section 5: TP1-only counterfactual (confirmed TP1 hits only)
# ---------------------------------------------------------------------------

def compute_tp1_pnl(row: dict):
    """
    Compute pnl_pct if exit had been 100% at TP1 instead of laddered exits.
    Returns float or None if required fields are missing.
    Uses direct columns: entry1, tp1, direction, leverage.
    Falls back to signal_json.leverage_cap when leverage column is NULL.
    """
    entry1    = row.get("entry1")
    tp1       = row.get("tp1")
    direction = row.get("direction")
    leverage  = row.get("leverage")

    if leverage is None:
        sig_json = row.get("signal_json")
        if sig_json:
            try:
                leverage = json.loads(sig_json).get("leverage_cap")
            except Exception:
                pass

    if not entry1 or not tp1 or not direction or not leverage:
        return None
    if entry1 <= 0:
        return None

    if direction == "LONG":
        raw_pct = (tp1 - entry1) / entry1 * 100.0
    else:
        raw_pct = (entry1 - tp1) / entry1 * 100.0

    return raw_pct * float(leverage)


def section_tp1_counterfactual(rows: list, strat_groups: list) -> dict:
    # Gate on confirmed TP1_HIT events only — not all signals with a tp1 column.
    # rows is used only for the total-signal count note; actual computation uses DB.
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    confirmed_rows = con.execute("""
        SELECT s.id, s.strategy_key, s.direction, s.entry1, s.tp1,
               s.pnl_pct, s.leverage, s.signal_json
        FROM signals s
        WHERE EXISTS (
            SELECT 1 FROM position_events pe
            WHERE pe.signal_id = s.id
              AND pe.event_type = 'TP1_HIT'
        )
        AND s.entry1 IS NOT NULL AND s.tp1 IS NOT NULL
        AND s.pnl_pct IS NOT NULL
        AND s.result NOT IN ('EXPIRED', 'SKIPPED')
    """).fetchall()
    con.close()

    confirmed_rows = [dict(r) for r in confirmed_rows]
    n_all_with_tp1 = sum(1 for r in rows if r.get("tp1") is not None)
    n_confirmed    = len(confirmed_rows)

    result = {"_note": f"confirmed TP1_HIT events: {n_confirmed} of {n_all_with_tp1} signals that have a tp1 value"}

    for strat_label in strat_groups:
        subset = confirmed_rows if strat_label == "ALL_STRATEGIES" else [
            r for r in confirmed_rows if r["strategy_key"] == strat_label
        ]
        actual_total = 0.0
        cf_total     = 0.0
        computable   = 0
        skipped      = 0
        for r in subset:
            cf = compute_tp1_pnl(r)
            if cf is not None and r["pnl_pct"] is not None:
                actual_total += r["pnl_pct"]
                cf_total     += cf
                computable   += 1
            else:
                skipped += 1

        result[strat_label] = {
            "n_computable":         computable,
            "n_skipped":            skipped,
            "actual_total":         actual_total    if computable else None,
            "counterfactual_total": cf_total        if computable else None,
            "delta":                (cf_total - actual_total) if computable else None,
        }
    return result


# ---------------------------------------------------------------------------
# Section 6: Direction-flip sanity check
# ---------------------------------------------------------------------------

def section_direction_flip(rows: list, strat_groups: list) -> dict:
    result = {}
    for strat_label in strat_groups:
        subset = rows if strat_label == "ALL_STRATEGIES" else [
            r for r in rows if r["strategy_key"] == strat_label
        ]
        actual = sum(r["pnl_pct"] for r in subset if r["pnl_pct"] is not None)
        flipped = -actual
        result[strat_label] = {
            "actual_total":    actual,
            "flipped_total":   flipped,
            "delta":           flipped - actual,
            "anti_correlated": flipped > actual,
        }
    return result


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def print_report(rows: list, excluded: int, all_data: dict) -> None:
    strategy_keys = sorted(set(r["strategy_key"] for r in rows))
    strat_groups  = ["ALL_STRATEGIES"] + strategy_keys

    print(f"\n{'='*W}")
    print("  Matrix Trader 7.0 — Closed Signal Audit Report")
    print(f"  Closed signals analyzed : {len(rows)}")
    if excluded:
        print(f"  Excluded (pnl_pct NULL) : {excluded}")
    print(f"  Strategies present      : {', '.join(strategy_keys) or 'none'}")
    print(f"  Generated               : {datetime.utcnow().isoformat()}Z")
    print(f"{'='*W}")

    for strat_label in strat_groups:
        strat_rows = rows if strat_label == "ALL_STRATEGIES" else [
            r for r in rows if r["strategy_key"] == strat_label
        ]
        print(f"\n{'='*W}")
        print(f"  STRATEGY: {strat_label}  (n={len(strat_rows)})")
        print(f"{'='*W}")

        # --- Section 1 ---
        print(f"\n  SECTION 1 — Volatility Regime Breakdown")
        print_agg_header("regime")
        regime_data = all_data["regimes"][strat_label]
        any_printed = False
        for regime in REGIMES:
            a = regime_data.get(regime, {"n": 0})
            if a["n"] > 0:
                print_agg_row(regime, a)
                any_printed = True
        if not any_printed:
            print("  (no data)")

        # --- Section 2 ---
        print(f"\n  SECTION 2 — Conviction Band Breakdown")
        print_agg_header("band")
        conv_data = all_data["conviction"][strat_label]
        any_printed = False
        for label, _, _ in CONV_BANDS:
            a = conv_data.get(label, {"n": 0})
            if a["n"] > 0:
                print_agg_row(label, a)
                any_printed = True
        if not any_printed:
            print("  (no data)")

        # --- Section 3 ---
        print(f"\n  SECTION 3 — Tag Breakdown (ranked by edge delta: with - without)")
        tag_data = all_data["tags"][strat_label]
        if not tag_data:
            sep()
            print("  (no tagged signals)")
        else:
            C1 = 26
            print(f"  {'tag':<{C1}} {'n_w':>5}  {'win_w':>6}  {'tot_w':>8}  "
                  f"{'n_wo':>5}  {'win_wo':>6}  {'tot_wo':>8}  {'delta':>9}")
            sep()
            for t in tag_data:
                w  = t["with"]
                wo = t["without"]
                wr_w  = pct_str(int(w["win_rate"]  * w["n"]  / 100 + 0.5),  w["n"])  if w["n"]  else "  n/a "
                wr_wo = pct_str(int(wo["win_rate"] * wo["n"] / 100 + 0.5),  wo["n"]) if wo["n"] else "  n/a "
                tp_w  = fmt_pnl(w["total_pnl"])
                tp_wo = fmt_pnl(wo["total_pnl"])
                delta = fmt_pnl(t["edge_delta"])
                print(f"  {t['tag']:<{C1}} {w['n']:>5}  {wr_w}  {tp_w}  "
                      f"{wo['n']:>5}  {wr_wo}  {tp_wo}  {delta}")

    # --- Section 4 (global) ---
    print(f"\n{'='*W}")
    print(f"  SECTION 4 — Symbol Blacklist Candidates  (n>=3 AND win_rate<25% OR total_pnl<-100)")
    sep()
    blacklist = all_data["blacklist"]
    if not blacklist:
        print("  (none — no symbols qualify)")
    else:
        print(f"  {'symbol':<18} {'strategy':<22} {'n':>4}  {'win%':>6}  {'total_pnl':>10}")
        sep()
        for b in blacklist:
            print(f"  {b['symbol']:<18} {b['strategy_key']:<22} {b['n_closed']:>4}  "
                  f"{b['win_rate']:>5.1f}%  {b['total_pnl']:>+10.1f}")

    # --- Section 5 ---
    print(f"\n{'='*W}")
    print(f"  SECTION 5 — TP1-Only Counterfactual  (confirmed TP1 hits only; +delta = TP1 would be better than laddered)")
    tp1 = all_data["tp1_counterfactual"]
    if tp1.get("_note"):
        print(f"  NOTE: {tp1['_note']}")
    sep()
    print(f"  {'strategy':<28} {'n_calc':>6}  {'actual_tot':>11}  {'cf_tp1_tot':>11}  {'delta':>9}")
    sep()
    for sl in ["ALL_STRATEGIES"] + sorted(set(r["strategy_key"] for r in rows)):
        d = tp1.get(sl, {})
        n  = d.get("n_computable", 0)
        at = fmt_pnl(d.get("actual_total"))
        ct = fmt_pnl(d.get("counterfactual_total"))
        dl = fmt_pnl(d.get("delta"))
        sk = d.get("n_skipped", 0)
        note = f"  ({sk} rows skipped — missing entry1/tp1/leverage)" if sk else ""
        print(f"  {sl:<28} {n:>6}  {at}  {ct}  {dl}{note}")

    # --- Section 6 ---
    print(f"\n{'='*W}")
    print(f"  SECTION 6 — Direction-Flip Sanity Check  (pure sign flip, no re-simulation)")
    print(f"  If anti_corr=YES the scorer is anti-correlated with profitable outcomes.")
    sep()
    flip = all_data["direction_flip"]
    print(f"  {'strategy':<28} {'actual_tot':>11}  {'flipped_tot':>11}  {'anti_corr':>10}")
    sep()
    for sl in ["ALL_STRATEGIES"] + sorted(set(r["strategy_key"] for r in rows)):
        d = flip.get(sl, {})
        at = fmt_pnl(d.get("actual_total"))
        ft = fmt_pnl(d.get("flipped_total"))
        ac = "YES ⚠" if d.get("anti_correlated") else "no"
        print(f"  {sl:<28} {at}  {ft}  {ac:>10}")

    # --- Reconciliation ---
    print(f"\n{'='*W}")
    print(f"  RECONCILIATION — Sec1 (regime totals) vs Sec2 (conviction totals) per strategy")
    print(f"  (same trades aggregated two different ways — must match within ±0.5)")
    sep()
    all_ok = True
    strategy_keys = sorted(set(r["strategy_key"] for r in rows))
    for sl in ["ALL_STRATEGIES"] + strategy_keys:
        sl_rows = rows if sl == "ALL_STRATEGIES" else [r for r in rows if r["strategy_key"] == sl]
        sec1 = sum(
            (all_data["regimes"][sl][regime].get("total_pnl") or 0.0)
            for regime in REGIMES
        )
        sec2 = sum(
            (all_data["conviction"][sl][label].get("total_pnl") or 0.0)
            for label, _, _ in CONV_BANDS
        )
        direct = sum(r["pnl_pct"] for r in sl_rows if r["pnl_pct"] is not None)
        diff   = abs(sec1 - sec2)
        status = "OK" if diff <= 0.5 else f"MISMATCH diff={diff:.2f}"
        print(f"  {sl:<28} sec1={sec1:+9.1f}  sec2={sec2:+9.1f}  direct={direct:+9.1f}  [{status}]")
        if diff > 0.5:
            all_ok = False
    print()
    print(f"  Reconciliation: {'PASS' if all_ok else 'FAIL — check data integrity'}")
    print(f"\n{'='*W}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_audit() -> None:
    if not DB_PATH.exists():
        print(f"[ERROR] Database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    # Step 1: Schema + sample rows (user sight-check before any aggregations)
    print(f"\n{'='*W}")
    print(f"  [Step 1] Schema inspection — {DB_PATH}")
    print(f"{'='*W}")
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    schema_row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='signals'"
    ).fetchone()
    print(schema_row[0] if schema_row else "(schema not found)")
    print()

    print("  [Sample closed rows — sight-check before aggregations run]")
    con.row_factory = sqlite3.Row
    samples = con.execute(
        "SELECT id, symbol, strategy_key, conviction, volatility, tags, "
        "pnl_pct, leverage, result, direction, entry1, tp1, exit_price "
        "FROM signals WHERE result IS NOT NULL LIMIT 3"
    ).fetchall()
    for r in samples:
        print(" ", dict(r))
    con.close()
    print()

    # Step 2: Load data
    rows, excluded = load_signals()

    if len(rows) == 0:
        count_db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True).execute(
            "SELECT COUNT(*) FROM signals WHERE result IS NOT NULL AND pnl_pct IS NOT NULL"
        ).fetchone()[0]
        print(f"[INFO] No closed signals with pnl_pct found.")
        print(f"       Result-tagged rows with pnl_pct=NULL: {excluded}")
        print(f"       DB confirms count: {count_db}")
        print(f"       Tag outcomes in the app and run /api/backfill/pnl before auditing.")
        OUTPUT_PATH.parent.mkdir(exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps({
            "generated_at":    datetime.utcnow().isoformat() + "Z",
            "total_analyzed":  0,
            "excluded_no_pnl": excluded,
            "note":            "No closed signals with pnl_pct. Tag outcomes + run backfill first.",
        }, indent=2))
        print(f"\nSaved empty report → {OUTPUT_PATH}")
        return

    strategy_keys = sorted(set(r["strategy_key"] for r in rows))
    strat_groups  = ["ALL_STRATEGIES"] + strategy_keys

    # Steps 3–5: Compute all sections
    all_data: dict = {
        "regimes":            {},
        "conviction":         {},
        "tags":               {},
        "blacklist":          [],
        "tp1_counterfactual": {},
        "direction_flip":     {},
    }

    for sl in strat_groups:
        sk = None if sl == "ALL_STRATEGIES" else sl
        all_data["regimes"][sl]    = section_regime(rows, sk)
        all_data["conviction"][sl] = section_conviction(rows, sk)
        all_data["tags"][sl]       = section_tags(rows, sk)

    all_data["blacklist"]          = section_blacklist(rows)
    all_data["tp1_counterfactual"] = section_tp1_counterfactual(rows, strat_groups)
    all_data["direction_flip"]     = section_direction_flip(rows, strat_groups)

    # Step 6: Print report
    print_report(rows, excluded, all_data)

    # Step 7: Save JSON
    output = {
        "generated_at":    datetime.utcnow().isoformat() + "Z",
        "total_analyzed":  len(rows),
        "excluded_no_pnl": excluded,
        "sections":        all_data,
    }
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    print(f"Saved → {OUTPUT_PATH}")


if __name__ == "__main__":
    run_audit()
