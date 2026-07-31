#!/usr/bin/env python3
"""Deterministically bind generated Hermes prose to authoritative MT7 facts."""

from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any


SNAPSHOT_HEADING = "## Authoritative MT7 Snapshot"
CANONICAL_HEADING = "# Hermes Advisory Memo"


def normalize_memo_heading(memo_text: str) -> str:
    """Remove provider chatter and give every memo one canonical heading."""
    marker = re.search(
        r"(?im)^(?:#{1,6}\s*)?(?:\*\*)?HERMES[^\n]{0,80}\bMEMO\b"
        r"(?:\*\*)?[^\n]*$",
        memo_text,
    )
    body = memo_text[marker.end():] if marker else memo_text
    body = re.sub(r"^\s*(?:---\s*)?", "", body, count=1)
    return CANONICAL_HEADING + "\n\n" + body.strip()


def _audit_from_packet(packet: dict[str, Any]) -> dict[str, Any]:
    audit = packet.get("audit")
    return audit if isinstance(audit, dict) else packet


def _display(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def build_authoritative_snapshot(packet: dict[str, Any]) -> str:
    """Render required facts that a language model must not be able to omit."""
    audit = _audit_from_packet(packet)
    paper = audit.get("paper") if isinstance(audit.get("paper"), dict) else {}
    readiness = (
        audit.get("readiness")
        if isinstance(audit.get("readiness"), dict)
        else {}
    )
    suggestions = (
        audit.get("suggestions")
        if isinstance(audit.get("suggestions"), dict)
        else {}
    )
    goal_actuals = (
        audit.get("goal_actuals")
        if isinstance(audit.get("goal_actuals"), dict)
        else {}
    )

    lines = [
        SNAPSHOT_HEADING,
        "",
        (
            "_This block is rendered directly from the MT7 audit packet. "
            "It is evidence, not model-generated interpretation._"
        ),
        "",
    ]

    paper_closed = paper.get("closed")
    if isinstance(paper_closed, int) and not isinstance(paper_closed, bool):
        lines.append(
            f"- All-time Paper simulated sample: {paper_closed} closed trades."
        )

    cohort_n = readiness.get("current_cohort_sample_n")
    cohort_target = readiness.get("current_cohort_target_n")
    if cohort_n is not None and cohort_target is not None:
        lines.append(
            "- Current policy cohort progress: "
            f"{cohort_n}/{cohort_target} closed Paper trades."
        )

    linked_sample = goal_actuals.get("paper_ev_sample_n")
    if linked_sample is not None:
        lines.append(
            "- 30-day linked-signal Paper sample: "
            f"{linked_sample} outcomes."
        )

    current_value = goal_actuals.get("current_value_usd")
    total_pnl = goal_actuals.get("total_pnl_usd")
    if current_value is not None:
        pnl_suffix = (
            f"; net modeled P&L ${float(total_pnl):+.2f}"
            if total_pnl is not None
            else ""
        )
        lines.append(
            f"- Current simulated Paper equity: ${float(current_value):.2f}"
            f"{pnl_suffix}."
        )

    conflict_ids = set(suggestions.get("baseline_conflict_ids") or [])
    for suggestion in suggestions.get("active") or []:
        if not isinstance(suggestion, dict):
            continue
        suggestion_id = suggestion.get("id")
        if suggestion_id not in conflict_ids:
            continue
        authority = suggestion.get("control_authority") or {}
        lines.append(
            "- Stale learner proposal "
            f"`{suggestion_id}`: proposal_current="
            f"{_display(suggestion.get('current_value'))}; runtime_actual="
            f"{_display(authority.get('runtime_actual'))}; suggested="
            f"{_display(suggestion.get('suggested_value'))}. "
            "Do not apply this stale proposal; recompute it from runtime_actual "
            "and collect a fresh forward cohort."
        )

    return "\n".join(lines).rstrip() + "\n"


def inject_authoritative_snapshot(
    memo_text: str,
    packet: dict[str, Any],
) -> str:
    """Append one idempotent snapshot to generated memo prose."""
    if SNAPSHOT_HEADING in memo_text:
        memo_text = memo_text.split(SNAPSHOT_HEADING, 1)[0].rstrip()
    memo_text = normalize_memo_heading(memo_text)
    snapshot = build_authoritative_snapshot(packet)
    return memo_text.rstrip() + "\n\n" + snapshot


def inject_files(memo_path: pathlib.Path, packet_path: pathlib.Path) -> None:
    packet = json.loads(packet_path.read_text(errors="replace"))
    memo = memo_path.read_text(errors="replace")
    memo_path.write_text(inject_authoritative_snapshot(memo, packet))


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] != "inject":
        print(
            "usage: hermes_memo_integrity.py inject MEMO_PATH PACKET_PATH",
            file=sys.stderr,
        )
        return 2
    inject_files(pathlib.Path(argv[2]), pathlib.Path(argv[3]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
