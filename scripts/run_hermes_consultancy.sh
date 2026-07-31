#!/usr/bin/env bash
set -euo pipefail

MT7_URL="${MT7_URL:-http://207.148.66.39:8080}"
WORKDIR="${WORKDIR:-/opt/mt7-hermes}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
PACKET="$WORKDIR/out/context_$TS.json"
RESEARCH_DIR="$WORKDIR/out/research"
RESEARCH_PACKET="$RESEARCH_DIR/research_packet_$TS.json"
RESEARCH_LATEST="$RESEARCH_DIR/latest_weekly_memo.json"
RESEARCH_SOURCES="$RESEARCH_DIR/sources.json"
RESEARCH_ARCHIVE_DIR="$RESEARCH_DIR/archive"
RESEARCH_ARCHIVE="$RESEARCH_ARCHIVE_DIR/weekly_research_$TS.json"
PROMPT="$WORKDIR/out/prompt_$TS.txt"
MEMO="$WORKDIR/out/latest_memo.md"
CANDIDATE_MEMO="$WORKDIR/out/candidate_memo_$TS.md"
JSON_MEMO="$WORKDIR/out/latest_memo.json"
ARCHIVE_DIR="$WORKDIR/out/archive"
ARCHIVE_MEMO="$ARCHIVE_DIR/hermes_memo_$TS.md"
ARCHIVE_JSON="$ARCHIVE_DIR/hermes_memo_$TS.json"

mkdir -p "$WORKDIR/out" "$ARCHIVE_DIR" "$RESEARCH_DIR" "$RESEARCH_ARCHIVE_DIR"
curl -fsS "$MT7_URL/api/intelligence/hermes" -o "$PACKET"
if ! curl -fsS "$MT7_URL/api/intelligence/research/library" -o "$RESEARCH_PACKET"; then
  printf '{"success":false,"error":"research library fetch failed","generated_at":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RESEARCH_PACKET"
fi

python3 - "$PACKET" "$RESEARCH_PACKET" "$PROMPT" <<'PY'
import json
import pathlib
import sys

packet_path = pathlib.Path(sys.argv[1])
research_packet_path = pathlib.Path(sys.argv[2])
prompt_path = pathlib.Path(sys.argv[3])

def load_json(path, fallback):
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception as exc:
        return {"error": str(exc)}

def take_list(value, limit):
    return value[:limit] if isinstance(value, list) else []

def clip(value, chars=1200):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:chars] + ("..." if len(text) > chars else "")

def pick(obj, keys):
    return {k: obj.get(k) for k in keys if isinstance(obj, dict) and k in obj}

packet_raw = load_json(packet_path, {})
audit = packet_raw.get("audit") if isinstance(packet_raw.get("audit"), dict) else packet_raw
research_raw = load_json(research_packet_path, {})

signals = audit.get("signals") or {}
paper = audit.get("paper") or {}
suggestions = audit.get("suggestions") or {}
research = audit.get("research") or {}
library = research_raw.get("library") or research_raw
weekly_memo = research_raw.get("weekly_memo") or library.get("weekly_memo") or {}
weekly_sections = weekly_memo.get("sections") if isinstance(weekly_memo, dict) else {}
weekly_sections = weekly_sections if isinstance(weekly_sections, dict) else {}
active_suggestions = suggestions.get("active") if isinstance(suggestions.get("active"), list) else []
baseline_conflict_ids = set(suggestions.get("baseline_conflict_ids") or [])
baseline_conflicts = []
for suggestion in active_suggestions:
    if not isinstance(suggestion, dict) or suggestion.get("id") not in baseline_conflict_ids:
        continue
    authority = suggestion.get("control_authority") or {}
    baseline_conflicts.append({
        "id": suggestion.get("id"),
        "strategy": suggestion.get("strategy"),
        "proposal_current": suggestion.get("current_value"),
        "runtime_actual": authority.get("runtime_actual"),
        "suggested": suggestion.get("suggested_value"),
        "required_action": (
            "Do not apply this proposal. Recompute it from runtime_actual "
            "and collect a fresh forward cohort before review."
        ),
    })

research_sources = []
for source in take_list(library.get("sources"), 10):
    if not isinstance(source, dict):
        continue
    item = pick(source, ["id", "title", "type", "url", "tags"])
    item["summary"] = clip(source.get("summary") or "", 500)
    research_sources.append(item)

cipher_reviews = []
for review in take_list(research_raw.get("cipher_reviews") or library.get("cipher_reviews"), 8):
    if not isinstance(review, dict):
        continue
    cipher_reviews.append(pick(review, [
        "id", "title", "target_strategy", "shadow_tag", "status",
        "cipher_score", "cipher_verdict", "thesis", "gate_impact",
        "overfitting_risk", "promotion_criteria", "rollback_condition",
    ]))

weekly_priorities = []
for priority in take_list(weekly_sections.get("priority_queue"), 6):
    if not isinstance(priority, dict):
        continue
    weekly_priorities.append(pick(priority, [
        "id", "target_strategy", "shadow_tag", "thesis", "registered",
        "priority_score", "evidence_count", "evidence_grade",
        "testability_score", "gate_impact", "overfitting_risk",
        "promotion_criteria", "rollback_condition", "next_action",
    ]))

compact = {
    "data_contract_version": audit.get("data_contract_version"),
    "health_score": audit.get("health_score"),
    "health_score_contract": audit.get("health_score_contract"),
    "goals": audit.get("goals"),
    "goal_actuals": audit.get("goal_actuals"),
    "metric_provenance": audit.get("metric_provenance"),
    "decision_authority": audit.get("decision_authority"),
    "capability_contract": audit.get("capability_contract"),
    "paper_policy": audit.get("paper_policy"),
    "readiness": audit.get("readiness"),
    "recommendation": audit.get("recommendation"),
    "risk_warning": audit.get("risk_warning"),
    "blindspots": take_list(audit.get("blindspots"), 8),
    "signals": {
        "dataset": signals.get("dataset"),
        "recent_30d": signals.get("recent_30d"),
        "by_strategy": take_list(signals.get("strategies"), 12),
        "worst_symbols": take_list(signals.get("worst_symbols"), 8),
        "disabled_shadow": {
            "dataset": (signals.get("disabled_shadow") or {}).get("dataset"),
            "recent_30d": (signals.get("disabled_shadow") or {}).get("recent_30d"),
            "by_strategy": take_list((signals.get("disabled_shadow") or {}).get("strategies"), 12),
            "worst_symbols": take_list((signals.get("disabled_shadow") or {}).get("worst_symbols"), 8),
        },
        "coach_summary": signals.get("coach_summary"),
    },
    "paper": {
        **pick(paper, [
            "dataset", "open", "pending", "closed", "wins", "partials",
            "losses", "win_partial_rate_pct", "ev_per_trade_pct",
            "total_pnl_pct", "avg_pnl_usd", "total_pnl_usd",
            "gross_ev_per_trade_pct", "total_cost_usd", "profit_factor",
            "profit_factor_usd", "sample_warning", "metric_note", "lifecycle",
        ]),
        "current_policy_cohort": paper.get("current_policy_cohort"),
        "worst_symbols_by_usd": take_list(paper.get("worst_symbols_by_usd"), 8),
        "worst_trades": take_list(paper.get("worst_trades"), 8),
    },
    "suggestions": {
        **pick(suggestions, [
            "learner_running", "counts", "applied_evaluating_count",
            "parallel_shadow_count", "authority_conflict_count",
            "authority_conflict_ids", "baseline_conflict_count",
            "baseline_conflict_ids", "warnings", "total",
        ]),
        "baseline_conflicts": baseline_conflicts,
        "active": take_list(active_suggestions, 8),
    },
    "cipher_reports": audit.get("cipher_reports"),
    "edge_lab": audit.get("edge_lab"),
    "research_pipeline": {
        "status": research.get("status"),
        "count": research.get("count"),
        "source_count": library.get("source_count"),
        "shadow_experiment_count": library.get("shadow_experiment_count") or research_raw.get("shadow_experiment_count"),
        "sources": research_sources,
        "cipher_reviews": cipher_reviews,
        "weekly_summary": weekly_sections.get("executive_summary"),
        "anti_overfit_rules": take_list(weekly_sections.get("anti_overfit_rules"), 8),
        "weekly_priorities": weekly_priorities,
    },
}

prompt = """You are Hermes Advisory Group, the outside consultancy for Matrix Trader 7.0.

Your role:
- You are not Cipher Research Group. Cipher is market-facing intelligence.
- You are not mt-learner. mt-learner proposes threshold/config ideas from outcome data.
- You are not an execution bot. You never place trades, never request secrets, and never mutate MT7 config directly.
- You are the outside systems auditor plus research institute.

Write a concise but rigorous advisory memo with these sections:
1. Executive Verdict
2. Performance Audit Desk
3. Quant Validation Desk
4. Risk & Capital Committee
5. Execution Quality Desk
6. Cipher Oversight Desk
7. Research Institute — external research, source-library themes, and shadow-only strategy hypotheses
8. Red Team
9. One Recommended Controlled Experiment
10. What Not To Change Yet

Rules:
- Use only the current compact packet. No prior Hermes memo is included because prior prose is not evidence.
- Treat decision_authority, metric_provenance, capability_contract, paper_policy, and readiness as binding interpretation rules.
- Separate Paper simulation, History/live signal-evaluator outcomes, and disabled_shadow counterfactual outcomes.
- Never call History/live signal outcomes executed trades, realized account P&L, or brokerage fills.
- Never format total_pnl_pct as USD or add percentage-point totals to Paper equity.
- Use only explicitly named *_usd fields for dollar claims.
- Use paper.closed for the all-time simulated Paper trade count. goal_actuals.paper_ev_sample_n is a separate 30-day linked-signal sample and must never be called the all-time Paper count.
- Never label goal_actuals.paper_ev_sample_n as "all-time," even when immediately qualifying it; call it the "30-day linked-signal Paper sample."
- Never blend disabled_shadow into the live 30-day strategy or symbol tables.
- Do not call an all-time or all-lanes statistic a live 30-day cohort.
- Describe current_value_usd as simulated Paper equity.
- Use paper_policy.max_open_positions and paper_policy.sizing_balance_usd for sizing discussion; do not infer sizing from simulated equity.
- Treat risk_pct_per_trade as a target sizing input, not a guaranteed maximum realized loss. Never call account_balance_usd * risk_pct_per_trade a maximum, max loss, hard cap, or loss limit. A max_position_size_usd value is a notional/position-size cap, not a loss cap. Use observed pnl_usd fields for loss claims.
- Flow and conviction gates are signal-selection controls, not capital-loss caps or primary defenses against realized loss.
- MT7 readiness is authoritative: do not lower its 50-trade current-policy minimum.
- The 20- and 50-trade rolling windows are nested and may overlap by design. Do not require non-overlapping rolling windows or invent statistical-independence requirements.
- Do not recommend implementing an existing capability. If capability_contract says it exists, recommend evaluating its recorded cohort instead.
- Do not invent reasons for cooldowns or safety controls that are not present in the packet.
- If suggestions.baseline_conflicts is non-empty, explicitly state each proposal_current, runtime_actual, and suggested value; call the proposal stale; and state that it must be recomputed from runtime_actual before application.
- Name sample-size limits explicitly.
- Isolate outlier risk.
- Recommend only one experiment.
- Any external research idea must become a shadow experiment first.
- Evaluate each shadow experiment on its own forward cohort. Do not claim all registered shadow experiments must complete before an individual candidate can be reviewed.
- Use paper_policy.active_strategies, disabled_strategies, and strategy_cooldowns as the strategy-state authority. Do not describe funding_arb as disabled or in cooldown unless the packet explicitly says so.
- Name likely gate interactions and overfitting risks for every research-derived idea.
- Do not recommend live automation unless paper/live evidence clears the stated gates.
- If no new web research was performed this run, say so plainly and distinguish that from stored/library research.
- Treat the compact packet as authoritative. Do not complain that full raw data was omitted.

Return plain Markdown. Be specific and practical.

COMPACT MT7 + HERMES RESEARCH PACKET:
""" + json.dumps(compact, indent=2, ensure_ascii=False, default=str)

prompt_path.write_text(prompt)
PY

set +e
if [ -x "$WORKDIR/run_hermes_pty.py" ]; then
  python3 "$WORKDIR/run_hermes_pty.py" "$PROMPT" "$CANDIDATE_MEMO"
else
  hermes chat -Q -q "$(cat "$PROMPT")" > "$CANDIDATE_MEMO"
fi
HERMES_RC=$?
set -e

if [ "$HERMES_RC" -ne 0 ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Hermes CLI failed with exit $HERMES_RC; trying bounded Gemini/OpenRouter fallback" >&2
  python3 - "$PROMPT" "$CANDIDATE_MEMO" <<'PY'
import json
import os
import pathlib
import sys
import textwrap
import urllib.error
import urllib.request

prompt_path = pathlib.Path(sys.argv[1])
memo_path = pathlib.Path(sys.argv[2])
prompt = prompt_path.read_text(errors="replace")

def load_env() -> dict[str, str]:
    env = {}
    for path in (pathlib.Path("/root/.hermes/.env"), pathlib.Path("/opt/mt7-hermes/.env")):
        if not path.exists():
            continue
        for line in path.read_text(errors="ignore").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith(("GEMINI_", "GOOGLE_", "OPENROUTER_", "HERMES_"))})
    return env

def write_failure(message: str) -> None:
    memo_path.write_text(message.strip() + "\n")

def try_gemini(env: dict[str, str]) -> bool:
    api_key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
    if not api_key:
        return False
    model = env.get("HERMES_GEMINI_MODEL", "gemini-2.5-flash")
    payload = {
        "system_instruction": {
            "parts": [
                {
                    "text": (
                        "You are Hermes Advisory Group, an external MT7 research and advisory desk. "
                        "Return a concise Markdown memo. Advisory only; never imply authority to trade or mutate config."
                    )
                }
            ]
        },
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": int(env.get("HERMES_GEMINI_MAX_OUTPUT_TOKENS", env.get("HERMES_MEMO_MAX_TOKENS", "8192"))),
            "temperature": float(env.get("HERMES_GEMINI_TEMPERATURE", "0.2")),
        },
    }
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        candidates = data.get("candidates") or []
        parts = (((candidates[0] if candidates else {}).get("content") or {}).get("parts") or [])
        text = "\n".join(str(part.get("text") or "") for part in parts).strip()
        if not text:
            text = "Gemini memo generation returned an empty response."
        memo_path.write_text(textwrap.dedent(text).strip() + "\n")
        return True
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        write_failure(
            "Hermes memo generation failed in CLI and Gemini paths.\n\n"
            f"Gemini HTTP {exc.code}: {body[:1200]}"
        )
    except Exception as exc:
        write_failure(f"Hermes memo generation failed in CLI and Gemini paths: {exc}")
    return False

def try_openrouter(env: dict[str, str]) -> bool:
    api_key = env.get("OPENROUTER_API_KEY")
    if not api_key:
        write_failure("Hermes memo generation failed: no Gemini or OpenRouter API key is available.")
        return False
    payload = {
        "model": env.get("HERMES_FALLBACK_MODEL", "deepseek/deepseek-v4-flash"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Hermes Advisory Group, an external MT7 research and advisory desk. "
                    "Return a concise Markdown memo. Advisory only; never imply authority to trade or mutate config."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": int(env.get("HERMES_MEMO_MAX_TOKENS", "4096")),
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://matrix-trader.local/hermes",
            "X-Title": "Matrix Trader Hermes Weekly Memo",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        if not text.strip():
            text = "OpenRouter memo generation returned an empty response."
        memo_path.write_text(textwrap.dedent(text).strip() + "\n")
        return True
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        write_failure(
            "Hermes memo generation failed in CLI, Gemini, and OpenRouter paths.\n\n"
            f"OpenRouter HTTP {exc.code}: {body[:1200]}"
        )
    except Exception as exc:
        write_failure(f"Hermes memo generation failed in CLI, Gemini, and OpenRouter paths: {exc}")
    return False

env = load_env()
if not try_gemini(env):
    try_openrouter(env)
PY
fi
python3 - "$CANDIDATE_MEMO" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
body = path.read_text(errors="replace")
marker = re.search(
    r"(?im)^(?:#{1,6}\s*)?(?:\*\*)?HERMES[^\n]{0,80}\bMEMO\b",
    body,
)
if marker and marker.start() > 0:
    body = body[marker.start():]
path.write_text(body.strip() + "\n")
PY
python3 "$WORKDIR/hermes_memo_integrity.py" inject "$CANDIDATE_MEMO" "$PACKET"
python3 - "$CANDIDATE_MEMO" "$PACKET" <<'PY'
import json
import pathlib
import re
import sys

memo_path = pathlib.Path(sys.argv[1])
packet_path = pathlib.Path(sys.argv[2])
sys.path.insert(0, str(memo_path.parent.parent))
from hermes_memo_integrity import has_linked_sample_mislabel

memo = memo_path.read_text(errors="replace")
semantic_text = re.sub(r"[*`]", "", memo)
packet_raw = json.loads(packet_path.read_text(errors="replace"))
audit = packet_raw.get("audit") if isinstance(packet_raw.get("audit"), dict) else packet_raw
paper = audit.get("paper") or {}
readiness = audit.get("readiness") or {}
suggestions = audit.get("suggestions") or {}
goal_actuals = audit.get("goal_actuals") or {}
errors = []

if not re.match(r"(?i)(?:#{1,6}\s*)?(?:\*\*)?HERMES[^\n]{0,50}ADVISORY MEMO\b", memo):
    errors.append("memo heading is missing or generation preamble was not removed")
if len(memo) < 2500:
    errors.append("memo is unexpectedly short")

paper_closed = paper.get("closed")
if isinstance(paper_closed, int):
    all_time_pair = re.search(
        rf"(?:all[- ]time[\s\S]{{0,140}}\b{paper_closed}\b|\b{paper_closed}\b[\s\S]{{0,140}}all[- ]time)",
        semantic_text,
        re.IGNORECASE,
    )
    if not all_time_pair:
        errors.append(f"all-time Paper count {paper_closed} is missing or mislabeled")

linked_sample = goal_actuals.get("paper_ev_sample_n")
if has_linked_sample_mislabel(semantic_text, linked_sample, paper_closed):
    errors.append(f"30-day linked sample {linked_sample} was mislabeled as all-time Paper")

if re.search(r"paper\.closed[^\n]{0,80}(?:not (?:explicitly )?present|missing|unavailable)", semantic_text, re.IGNORECASE):
    errors.append("memo falsely claims paper.closed is absent")

cohort_n = readiness.get("current_cohort_sample_n")
cohort_target = readiness.get("current_cohort_target_n")
if cohort_n is not None and cohort_target is not None:
    if not re.search(rf"\b{cohort_n}\b[\s\S]{{0,100}}\b{cohort_target}\b", semantic_text):
        errors.append(f"current cohort progress {cohort_n}/{cohort_target} is missing")

conflict_ids = set(suggestions.get("baseline_conflict_ids") or [])
for suggestion in suggestions.get("active") or []:
    if not isinstance(suggestion, dict) or suggestion.get("id") not in conflict_ids:
        continue
    authority = suggestion.get("control_authority") or {}
    suggestion_id = suggestion.get("id")
    position = semantic_text.find(str(suggestion_id))
    if position < 0:
        errors.append(f"stale proposal {suggestion.get('id')} is not identified")
        continue
    conflict_block = semantic_text[max(0, position - 700):position + 900]
    expected_values = {
        "proposal_current": suggestion.get("current_value"),
        "runtime_actual": authority.get("runtime_actual"),
        "suggested": suggestion.get("suggested_value"),
    }
    for label, value in expected_values.items():
        if value is None or not re.search(rf"\b{re.escape(str(value))}\b", conflict_block):
            errors.append(f"stale proposal binding {label}={value} is missing near {suggestion_id}")
    if "stale" not in conflict_block.lower():
        errors.append(f"proposal {suggestion_id} is not explicitly labeled stale")
    if not re.search(r"(?:do not|must not|should not)[^\n]{0,50}apply", conflict_block, re.IGNORECASE):
        errors.append(f"proposal {suggestion_id} lacks a local do-not-apply instruction")

for pattern, message in [
    (r"(?:require|must|should|need)[^\n]{0,50}non[- ]overlap", "invented non-overlapping rolling-window requirement"),
    (r"all\s+\d+\s+shadow experiments?[^\n]{0,60}(?:must|need|complete)", "all shadow experiments were incorrectly coupled"),
    (r"(?:flow|conviction)[^\n]{0,60}(?:primary defense|loss protection|capital protection)", "signal selection was mislabeled as capital-loss protection"),
]:
    if re.search(pattern, semantic_text, re.IGNORECASE):
        errors.append(message)

if errors:
    rejection_path = memo_path.with_suffix(".rejected.txt")
    rejection_path.write_text("\n".join(errors) + "\n")
    print("Hermes memo rejected: " + "; ".join(errors), file=sys.stderr)
    raise SystemExit(2)
PY
cp "$CANDIDATE_MEMO" "$MEMO"
cp "$MEMO" "$ARCHIVE_MEMO"

python3 - "$MEMO" "$JSON_MEMO" "$PACKET" "$ARCHIVE_JSON" "$RESEARCH_PACKET" "$RESEARCH_LATEST" "$RESEARCH_SOURCES" "$RESEARCH_ARCHIVE" <<'PY'
import datetime
import json
import pathlib
import sys

memo_path = pathlib.Path(sys.argv[1])
json_path = pathlib.Path(sys.argv[2])
packet_path = sys.argv[3]
archive_path = pathlib.Path(sys.argv[4])
research_packet_path = pathlib.Path(sys.argv[5])
research_latest_path = pathlib.Path(sys.argv[6])
research_sources_path = pathlib.Path(sys.argv[7])
research_archive_path = pathlib.Path(sys.argv[8])
memo = memo_path.read_text(errors="replace")
try:
    research_packet = json.loads(research_packet_path.read_text(errors="replace"))
except Exception as exc:
    research_packet = {"success": False, "error": str(exc)}

now = datetime.datetime.now(datetime.UTC).isoformat()
payload = {
    "generated_at": now,
    "format": "markdown",
    "source": "old-vps-hermes-cli",
    "context_packet": packet_path,
    "research_packet": str(research_packet_path),
    "body": memo,
}
body = json.dumps(payload, indent=2)
json_path.write_text(body)
archive_path.write_text(body)

library = research_packet.get("library") or {}
research_payload = {
    "generated_at": now,
    "format": "json",
    "source": "old-vps-hermes-cli",
    "mode": "old_server_research_sync",
    "mt7_research_packet": str(research_packet_path),
    "source_count": library.get("source_count", 0),
    "sources": library.get("sources", []),
    "cipher_reviews": research_packet.get("cipher_reviews", []),
    "shadow_experiment_count": research_packet.get("shadow_experiment_count", 0),
    "weekly_memo": research_packet.get("weekly_memo"),
    "hermes_research_section": memo,
    "rules": [
        "Hermes research is advisory only.",
        "External research ideas must become MT7 shadow experiments before any gate impact.",
        "Promotion requires forward-tested outcome evidence and rollback criteria.",
    ],
}
research_body = json.dumps(research_payload, indent=2)
research_latest_path.write_text(research_body)
research_archive_path.write_text(research_body)
research_sources_path.write_text(json.dumps({
    "updated_at": now,
    "source": "old-vps-hermes-cli",
    "sources": library.get("sources", []),
}, indent=2))
PY

echo "$JSON_MEMO"
