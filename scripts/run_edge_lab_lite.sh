#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/matrix-trader
LOG_DIR="$APP_DIR/logs"
RUN_NAME=${EDGE_LAB_RUN_NAME:-"Edge Lab Lite"}
LOG_FILE=${EDGE_LAB_LOG_FILE:-"$LOG_DIR/edge_lab_lite.log"}
LOCK_FILE=/tmp/mt7-edge-lab-lite.lock
MIN_FREE_GB=${EDGE_LAB_MIN_FREE_GB:-12}
TOP_N=${EDGE_LAB_TOP_N:-200}
MAX_RUNTIME=${EDGE_LAB_MAX_RUNTIME_MINUTES:-75}
BATCH_SIZE=${EDGE_LAB_BATCH_SIZE:-50}
MATERIALIZE_BATCH_SIZE=${EDGE_LAB_MATERIALIZE_BATCH_SIZE:-20000}
FACTOR_TOP_N=${EDGE_LAB_FACTOR_TOP_N:-10}
META_LABEL_SINCE=${EDGE_META_LABEL_SINCE:-"2026-07-08T16:41:37.802459"}
RESEARCH_NICE=${EDGE_LAB_NICE_LEVEL:-10}
UPGRADE_SYMBOLS=${EDGE_LAB_UPGRADE_SYMBOLS_PER_RUN:-5}
UPGRADE_RUNTIME=${EDGE_LAB_UPGRADE_MAX_RUNTIME_MINUTES:-45}

mkdir -p "$LOG_DIR" "$APP_DIR/data"
cd "$APP_DIR"

{
  echo
  echo "============================================================"
  echo "$RUN_NAME start: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  echo "top_n=$TOP_N max_runtime_minutes=$MAX_RUNTIME batch_size=$BATCH_SIZE materialize_batch_size=$MATERIALIZE_BATCH_SIZE factor_top_n=$FACTOR_TOP_N meta_label_since=$META_LABEL_SINCE upgrade_symbols=$UPGRADE_SYMBOLS"

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

  research_run=(nice -n "$RESEARCH_NICE")
  if command -v ionice >/dev/null 2>&1; then
    research_run+=(ionice -c2 -n7)
  fi

  "${research_run[@]}" python3 edge_lab_build.py --mode incremental --resume --top-n "$TOP_N" --batch-size "$BATCH_SIZE" --max-runtime-minutes "$MAX_RUNTIME" --skip-smoke
  "${research_run[@]}" python3 edge_lab_upgrade.py --max-symbols "$UPGRADE_SYMBOLS" --max-runtime-minutes "$UPGRADE_RUNTIME" --batch-size "$BATCH_SIZE"
  "${research_run[@]}" python3 edge_lab_materialize.py --db data/edge_lab.db --batch-size "$MATERIALIZE_BATCH_SIZE"
  "${research_run[@]}" python3 edge_lab_factors.py --db data/edge_lab.db --out data/factor_report.json --signals-db data/signals.db --top-n "$FACTOR_TOP_N" --quiet
  "${research_run[@]}" python3 edge_lab_meta.py --signals-db data/signals.db --edge-db data/edge_lab.db --since "$META_LABEL_SINCE" --quiet

  db_size=$(du -h data/edge_lab.db 2>/dev/null | awk '{print $1}')
  report_size=$(du -h data/factor_report.json 2>/dev/null | awk '{print $1}')
  echo "edge_lab_db_size=${db_size:-missing}"
  echo "factor_report_size=${report_size:-missing}"
  echo "$RUN_NAME done: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
} >> "$LOG_FILE" 2>&1
