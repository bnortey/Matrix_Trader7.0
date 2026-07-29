#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from edge_lab.meta_labeler import MetaLabelConfig, run_meta_labeler
from edge_lab.meta_labeler_v2 import run_meta_labeler_v2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the MT7 leakage-safe shadow statistical meta-labeler."
    )
    parser.add_argument("--signals-db", default="data/signals.db")
    parser.add_argument("--edge-db", default="data/edge_lab.db")
    parser.add_argument("--since")
    parser.add_argument("--min-training-rows", type=int, default=70)
    parser.add_argument("--min-test-rows", type=int, default=40)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--allow-threshold", type=float, default=0.60)
    parser.add_argument("--block-threshold", type=float, default=0.40)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.5 < args.allow_threshold < 1.0:
        raise SystemExit("--allow-threshold must be between 0.5 and 1.0")
    if not 0.0 < args.block_threshold < 0.5:
        raise SystemExit("--block-threshold must be between 0.0 and 0.5")
    result = run_meta_labeler(MetaLabelConfig(
        signals_db=Path(args.signals_db),
        edge_db=Path(args.edge_db),
        since=args.since,
        min_training_rows=max(30, args.min_training_rows),
        min_test_rows=max(10, args.min_test_rows),
        folds=max(1, args.folds),
        allow_threshold=args.allow_threshold,
        block_threshold=args.block_threshold,
    ))
    try:
        challenger = run_meta_labeler_v2(
            Path(args.signals_db),
            Path(args.edge_db),
            since=args.since,
            min_train=max(40, args.min_training_rows),
            min_test=max(20, args.min_test_rows),
        )
    except Exception as exc:
        challenger = {
            "success": False,
            "version": "mt7_meta_label_v2_challenger",
            "authority_mode": "shadow_read_only",
            "authority_eligible": False,
            "error": str(exc),
        }
    result["challenger_v2"] = challenger
    output = result
    if args.quiet:
        model = (result.get("metrics") or {}).get("model") or {}
        decisions = (result.get("metrics") or {}).get("decisions") or {}
        output = {
            "success": result.get("success"),
            "run_id": result.get("run_id"),
            "status": result.get("status"),
            "authority_mode": result.get("authority_mode"),
            "coverage": result.get("coverage"),
            "test_count": model.get("count"),
            "brier": model.get("brier"),
            "ece": model.get("ece"),
            "shadow_allow": decisions.get("shadow_allow"),
            "gates": result.get("gates"),
            "active_scores": result.get("active_scores"),
            "newly_evaluated_forward_scores": result.get(
                "newly_evaluated_forward_scores"
            ),
            "forward_shadow": result.get("forward_shadow"),
            "error": result.get("error"),
            "challenger_v2": challenger,
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
