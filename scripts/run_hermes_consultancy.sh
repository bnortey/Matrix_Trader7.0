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
latest_memo = audit.get("latest_memo") or {}

compact = {
    "health_score": audit.get("health_score"),
    "goals": audit.get("goals"),
    "goal_actuals": audit.get("goal_actuals"),
    "recommendation": audit.get("recommendation"),
    "risk_warning": audit.get("risk_warning"),
    "blindspots": take_list(audit.get("blindspots"), 8),
    "signals": {
        "recent_30d": signals.get("recent_30d"),
        "by_strategy": take_list(signals.get("strategies"), 12),
        "worst_symbols": take_list(signals.get("worst_symbols"), 8),
        "coach_summary": signals.get("coach_summary"),
    },
    "paper": {
        **pick(paper, ["open_count", "closed_count", "win_rate", "win_partial_rate", "ev_per_trade_pct", "avg_pnl_pct", "max_drawdown_pct", "profit_factor"]),
        "strategies": take_list(paper.get("strategies"), 12),
        "worst_symbols": take_list(paper.get("worst_symbols"), 8),
    },
    "suggestions": {
        **pick(suggestions, ["learner_running", "active_count", "evaluation_window_trades"]),
        "active": take_list(suggestions.get("active"), 8),
    },
    "cipher_reports": audit.get("cipher_reports"),
    "edge_lab": audit.get("edge_lab"),
    "research_pipeline": {
        "status": research.get("status"),
        "count": research.get("count"),
        "source_count": library.get("source_count"),
        "shadow_experiment_count": library.get("shadow_experiment_count") or research_raw.get("shadow_experiment_count"),
        "sources": [
            pick(src, ["id", "title", "type", "url", "tags", "summary"])
            for src in take_list(library.get("sources"), 10)
            if isinstance(src, dict)
        ],
        "cipher_reviews": take_list(research_raw.get("cipher_reviews") or library.get("cipher_reviews"), 10),
        "weekly_memo_sections": weekly_memo.get("sections") if isinstance(weekly_memo, dict) else None,
    },
    "previous_external_memo_excerpt": clip(latest_memo.get("body"), 1600) if isinstance(latest_memo, dict) else None,
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
- Separate paper outcomes from live/manual-tagged signal outcomes.
- Name sample-size limits explicitly.
- Isolate outlier risk.
- Recommend only one experiment.
- Any external research idea must become a shadow experiment first.
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
  python3 "$WORKDIR/run_hermes_pty.py" "$PROMPT" "$MEMO"
else
  hermes chat -Q -q "$(cat "$PROMPT")" > "$MEMO"
fi
HERMES_RC=$?
set -e

if [ "$HERMES_RC" -ne 0 ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Hermes CLI failed with exit $HERMES_RC; trying bounded Gemini/OpenRouter fallback" >&2
  python3 - "$PROMPT" "$MEMO" <<'PY'
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
