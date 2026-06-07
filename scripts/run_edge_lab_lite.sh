#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/matrix-trader
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/edge_lab_lite.log"
LOCK_FILE=/tmp/mt7-edge-lab-lite.lock
MIN_FREE_GB=12
TOP_N=${EDGE_LAB_TOP_N:-200}
MAX_RUNTIME=${EDGE_LAB_MAX_RUNTIME_MINUTES:-75}
BATCH_SIZE=${EDGE_LAB_BATCH_SIZE:-50}

mkdir -p "$LOG_DIR" "$APP_DIR/data"
cd "$APP_DIR"

{
  echo
  echo "============================================================"
  echo "Edge Lab Lite start: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  echo "top_n=$TOP_N max_runtime_minutes=$MAX_RUNTIME batch_size=$BATCH_SIZE"

  free_gb=$(df -BG --output=avail "$APP_DIR/data" | tail -1 | tr -dc '0-9')
  echo "free_gb=$free_gb"
  if [ "${free_gb:-0}" -lt "$MIN_FREE_GB" ]; then
    echo "ABORT: less than ${MIN_FREE_GB}GB free on data volume"
    exit 1
  fi

  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    flock -n 9 || { echo "ABORT: another Edge Lab Lite run is active"; exit 1; }
  fi

  python3 edge_lab_build.py --mode incremental --resume --top-n "$TOP_N" --batch-size "$BATCH_SIZE" --max-runtime-minutes "$MAX_RUNTIME"
  python3 edge_lab_materialize.py --db data/edge_lab.db --batch-size 20000
  python3 edge_lab_factors.py --db data/edge_lab.db --out data/factor_report.json --top-n 10 --quiet

  db_size=$(du -h data/edge_lab.db 2>/dev/null | awk '{print $1}')
  report_size=$(du -h data/factor_report.json 2>/dev/null | awk '{print $1}')
  echo "edge_lab_db_size=${db_size:-missing}"
  echo "factor_report_size=${report_size:-missing}"
  echo "Edge Lab Lite done: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
} >> "$LOG_FILE" 2>&1
