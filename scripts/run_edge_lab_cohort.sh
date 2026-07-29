#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${MT7_APP_DIR:-/opt/matrix-trader}
LOG_DIR="$APP_DIR/logs"
LOG_FILE=${EDGE_LAB_COHORT_LOG_FILE:-"$LOG_DIR/edge_lab_cohort.log"}
LOCK_FILE=/tmp/mt7-edge-lab-lite.lock
MIN_FREE_GB=${EDGE_LAB_MIN_FREE_GB:-12}
SYMBOL_LIMIT=${EDGE_LAB_COHORT_SYMBOL_LIMIT:-200}
MIN_TRADES=${EDGE_LAB_COHORT_MIN_TRADES:-1}
MAX_RUNTIME=${EDGE_LAB_COHORT_MAX_RUNTIME_MINUTES:-40}
BATCH_SIZE=${EDGE_LAB_COHORT_BATCH_SIZE:-40}
MATERIALIZE_BATCH_SIZE=${EDGE_LAB_MATERIALIZE_BATCH_SIZE:-20000}
FACTOR_TOP_N=${EDGE_LAB_FACTOR_TOP_N:-10}
RUN_ANALYSIS=${EDGE_LAB_COHORT_RUN_ANALYSIS:-true}
RESEARCH_NICE=${EDGE_LAB_NICE_LEVEL:-10}

mkdir -p "$LOG_DIR" "$APP_DIR/data"
cd "$APP_DIR"

{
  echo
  echo "============================================================"
  echo "Edge Lab Cohort start: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  echo "symbol_limit=$SYMBOL_LIMIT min_trades=$MIN_TRADES max_runtime_minutes=$MAX_RUNTIME batch_size=$BATCH_SIZE run_analysis=$RUN_ANALYSIS"

  free_gb=$(df -BG --output=avail "$APP_DIR/data" | tail -1 | tr -dc '0-9')
  echo "free_gb=$free_gb"
  if [ "${free_gb:-0}" -lt "$MIN_FREE_GB" ]; then
    echo "ABORT: less than ${MIN_FREE_GB}GB free on data volume"
    exit 1
  fi

  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    flock -n 9 || { echo "ABORT: another Edge Lab run is active"; exit 1; }
  fi

  symbols=$(python3 -c '
import json, os, sqlite3
limit = int(os.getenv("EDGE_LAB_COHORT_SYMBOL_LIMIT", "200"))
min_trades = int(os.getenv("EDGE_LAB_COHORT_MIN_TRADES", "1"))
cfg = {}
try:
    with open("data/paper_config.json", encoding="utf-8") as f:
        cfg = json.load(f)
except Exception:
    cfg = {}
since = str(os.getenv("EDGE_LAB_COHORT_SINCE") or cfg.get("current_cohort_started_at") or "1970-01-01T00:00:00")
con = sqlite3.connect("data/signals.db")
rows = con.execute("""
    SELECT symbol, COUNT(*) AS n
    FROM paper_trades
    WHERE status IN (?,?,?)
      AND COALESCE(filled_at, opened_at, queued_at, closed_at, ?) >= ?
    GROUP BY symbol
    HAVING COUNT(*) >= ?
    ORDER BY n DESC, symbol ASC
    LIMIT ?
""", ("closed", "open", "pending", "", since, min_trades, limit)).fetchall()
con.close()
print(",".join(str(r[0]).upper() for r in rows if r[0]))
')

  if [ -z "$symbols" ]; then
    echo "ABORT: no paper cohort symbols found"
    exit 1
  fi
  echo "symbols=$symbols"

  research_run=(nice -n "$RESEARCH_NICE")
  if command -v ionice >/dev/null 2>&1; then
    research_run+=(ionice -c2 -n7)
  fi
  "${research_run[@]}" python3 edge_lab_build.py --mode incremental --symbols "$symbols" --batch-size "$BATCH_SIZE" --max-runtime-minutes "$MAX_RUNTIME" --skip-smoke
  if [ "$RUN_ANALYSIS" = "true" ]; then
    "${research_run[@]}" python3 edge_lab_materialize.py --db data/edge_lab.db --batch-size "$MATERIALIZE_BATCH_SIZE"
    "${research_run[@]}" python3 edge_lab_factors.py --db data/edge_lab.db --out data/factor_report.json --signals-db data/signals.db --top-n "$FACTOR_TOP_N" --quiet
  fi

  db_size=$(du -h data/edge_lab.db 2>/dev/null | awk '{print $1}')
  report_size=$(du -h data/factor_report.json 2>/dev/null | awk '{print $1}')
  echo "edge_lab_db_size=${db_size:-missing}"
  echo "factor_report_size=${report_size:-missing}"
  echo "Edge Lab Cohort done: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
} >> "$LOG_FILE" 2>&1
