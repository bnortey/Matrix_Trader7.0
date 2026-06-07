"""
Tag-stratified edge analysis for Matrix Trader closed signals.

Background
----------
The A/B reconstruction (scripts/reconstruct_conviction.py) showed that
overall conviction is mildly anti-predictive on the closed-signal cohort
(v1 divergence ~-0.57 to -0.71 across local + VPS DBs — losers slightly
higher conviction than winners). The audit's structural critique focused
on the conviction *function* being a step function, but the deeper question
the data raised is whether specific scoring components (tags) are
individually inverted, individually correct, or individually noise.

This script bucketizes closed signals by tag and reports per-tag:
  - n_total, n_win, n_loss, n_partial
  - win_rate (WIN + PARTIAL / total decided)
  - avg_pnl_pct, median_pnl_pct
  - Wilson 95% confidence interval on win rate
  - "edge_vs_baseline" — win_rate delta vs the all-cohort baseline

The hypothesis to test (audit §02 plus our A/B finding):
  - strong_momentum is buying tops on extended moves
  - strong_dump is selling bottoms
  - short_squeeze fires after the squeeze already ran
  - regime_counter (which we just shadow-gated) was likely in-sample noise
  - mean_reversion strategy is the only one we expect to point the right way

A tag with win_rate noticeably below baseline AND a tight Wilson CI is a
candidate for ablation or sign-flip. A tag with win_rate above baseline is
a candidate to weight more heavily. A tag with wide CI just needs more data.

Usage
-----
  python3 scripts/tag_edge_analysis.py
  python3 scripts/tag_edge_analysis.py --db data/signals.db
  python3 scripts/tag_edge_analysis.py --strategy balanced --direction LONG
  python3 scripts/tag_edge_analysis.py --min-n 20 --json > tag_edges.json
  python3 scripts/tag_edge_analysis.py --by-tier   # split by strong/weak tier
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
# Stats helpers
# ---------------------------------------------------------------------------

def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """
    Wilson score 95% confidence interval for a binomial proportion.
    Robust at small n and at p close to 0/1 (unlike normal approximation).
    Returns (lo, hi) as floats in [0, 1].
    """
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _classify_outcome(result: str) -> str:
    """WIN and PARTIAL both count as wins for win-rate purposes — see audit §04.
    PARTIAL means TP1 was hit before the stop, which is a profitable outcome
    even if not a full ride to TP3."""
    if result in ("WIN", "PARTIAL"):
        return "winner"
    if result == "LOSS":
        return "loser"
    return "other"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_closed_signals(db_path: Path, strategy: str | None = None,
                        direction: str | None = None):
    """Yield {id, tags, result, pnl_pct, strategy_key, direction} for closed signals."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    sql = """
        SELECT id, tags, result, pnl_pct, strategy, strategy_key, direction
        FROM signals
        WHERE result IN ('WIN', 'LOSS', 'PARTIAL')
    """
    params: list = []
    if strategy:
        sql += " AND (strategy = ? OR strategy_key = ?)"
        params.extend([strategy, strategy])
    if direction:
        sql += " AND direction = ?"
        params.append(direction.upper())
    for row in con.execute(sql, params):
        yield row
    con.close()


def _parse_tags(raw: str | None) -> list[str]:
    """signals.tags is stored as either JSON list or comma-separated text."""
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(t) for t in parsed if t]
        except json.JSONDecodeError:
            pass
    return [t.strip() for t in raw.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

class TagStats:
    __slots__ = ("n_total", "n_win", "n_loss", "n_partial", "pnls")

    def __init__(self):
        self.n_total = 0
        self.n_win = 0
        self.n_loss = 0
        self.n_partial = 0
        self.pnls: list[float] = []

    def update(self, result: str, pnl: float | None) -> None:
        self.n_total += 1
        if result == "WIN":
            self.n_win += 1
        elif result == "LOSS":
            self.n_loss += 1
        elif result == "PARTIAL":
            self.n_partial += 1
        if pnl is not None:
            self.pnls.append(float(pnl))

    @property
    def decided(self) -> int:
        return self.n_win + self.n_loss + self.n_partial

    @property
    def successes(self) -> int:
        """WIN + PARTIAL count as wins for win-rate purposes."""
        return self.n_win + self.n_partial

    @property
    def win_rate(self) -> float:
        return self.successes / self.decided if self.decided else 0.0

    @property
    def avg_pnl(self) -> float:
        return statistics.mean(self.pnls) if self.pnls else 0.0

    @property
    def median_pnl(self) -> float:
        return statistics.median(self.pnls) if self.pnls else 0.0

    def wilson(self) -> tuple[float, float]:
        return wilson_ci(self.successes, self.decided)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _verdict(tag_stats: TagStats, baseline_wr: float, min_n: int) -> str:
    """One-word verdict for the per-tag row."""
    if tag_stats.decided < min_n:
        return "small_n"
    lo, hi = tag_stats.wilson()
    # If the 95% CI clears the baseline cleanly, call it.
    if lo > baseline_wr:
        return "edge"
    if hi < baseline_wr:
        return "inverted"
    if hi - lo > 0.30:
        return "noisy"
    return "neutral"


def print_table(stats_by_tag: dict[str, TagStats], baseline_wr: float, min_n: int):
    rows = [
        (tag, s) for tag, s in stats_by_tag.items()
        if s.decided >= min_n
    ]
    # Sort by win-rate ascending so most-inverted appears first.
    rows.sort(key=lambda r: r[1].win_rate)

    print(f"# Baseline win rate (WIN+PARTIAL) across all closed: {baseline_wr * 100:.1f}%")
    print(f"# Showing tags with n >= {min_n}, sorted by win_rate ASC (most inverted first)")
    print()
    print(f"{'tag':<32} {'n':>5} {'win':>4} {'part':>5} {'loss':>5} "
          f"{'wr%':>6} {'CI95%':>14} {'avgpnl':>8} {'edge':>7} {'verdict':<10}")
    print("-" * 110)
    for tag, s in rows:
        lo, hi = s.wilson()
        edge = (s.win_rate - baseline_wr) * 100
        verdict = _verdict(s, baseline_wr, min_n)
        print(
            f"{tag:<32} {s.decided:>5} {s.n_win:>4} {s.n_partial:>5} {s.n_loss:>5} "
            f"{s.win_rate * 100:>6.1f} "
            f"{f'[{lo*100:>4.1f}-{hi*100:>4.1f}]':>14} "
            f"{s.avg_pnl:>+8.2f} {edge:>+7.1f} {verdict:<10}"
        )


def to_json_payload(stats_by_tag, baseline_wr, min_n, sample_size):
    return {
        "sample_size": sample_size,
        "baseline_win_rate": round(baseline_wr, 4),
        "min_n": min_n,
        "tags": [
            {
                "tag": tag,
                "n": s.decided,
                "n_win": s.n_win,
                "n_partial": s.n_partial,
                "n_loss": s.n_loss,
                "win_rate": round(s.win_rate, 4),
                "wilson_95_lo": round(s.wilson()[0], 4),
                "wilson_95_hi": round(s.wilson()[1], 4),
                "avg_pnl_pct": round(s.avg_pnl, 3),
                "median_pnl_pct": round(s.median_pnl, 3),
                "edge_vs_baseline_pp": round((s.win_rate - baseline_wr) * 100, 2),
                "verdict": _verdict(s, baseline_wr, min_n),
            }
            for tag, s in sorted(stats_by_tag.items(), key=lambda r: r[1].win_rate)
            if s.decided >= min_n
        ],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/signals.db")
    parser.add_argument("--strategy", default=None,
                        help="Filter to one strategy key (e.g. balanced, momentum_breakout)")
    parser.add_argument("--direction", default=None, choices=[None, "LONG", "SHORT"])
    parser.add_argument("--min-n", type=int, default=15,
                        help="Minimum decided signals per tag to include in report (default 15)")
    parser.add_argument("--json", action="store_true",
                        help="Print JSON instead of a table")
    parser.add_argument("--by-tier", action="store_true",
                        help="Also break out strong_/weak_/medium_ tier suffixes separately")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: {db} does not exist", file=sys.stderr)
        return 2

    stats_by_tag: dict[str, TagStats] = defaultdict(TagStats)
    overall = TagStats()
    n_scanned = 0

    for row in load_closed_signals(db, args.strategy, args.direction):
        n_scanned += 1
        result = row["result"]
        pnl = row["pnl_pct"]
        overall.update(result, pnl)
        tags = _parse_tags(row["tags"])
        for tag in tags:
            stats_by_tag[tag].update(result, pnl)
            # Optionally break out tier groups so {strong_momentum, momentum}
            # show their relationship clearly without the operator scrolling.
            if args.by_tier:
                if tag.startswith("strong_"):
                    stats_by_tag[f"_tier:strong_*"].update(result, pnl)
                elif tag in ("momentum", "dump", "discount", "premium"):
                    stats_by_tag[f"_tier:weak_*"].update(result, pnl)

    baseline_wr = overall.win_rate

    if args.json:
        payload = to_json_payload(stats_by_tag, baseline_wr, args.min_n, n_scanned)
        print(json.dumps(payload, indent=2))
        return 0

    print(f"# Tag edge analysis — {db}")
    print(f"# Closed signals scanned: {n_scanned}")
    print(f"# Decided (WIN/LOSS/PARTIAL): {overall.decided}")
    if args.strategy:
        print(f"# Filtered to strategy: {args.strategy}")
    if args.direction:
        print(f"# Filtered to direction: {args.direction}")
    print()
    print_table(stats_by_tag, baseline_wr, args.min_n)
    print()
    print("Verdict legend:")
    print("  edge      = Wilson 95% CI lower bound > baseline (significant outperformance)")
    print("  inverted  = Wilson 95% CI upper bound < baseline (significant underperformance — ablate/flip)")
    print("  noisy     = CI width > 30pp; conclusion depends on more data")
    print("  neutral   = CI brackets baseline; no signal either way")
    print("  small_n   = n < min_n; excluded from report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
