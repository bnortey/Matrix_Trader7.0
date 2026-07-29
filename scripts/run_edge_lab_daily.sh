#!/usr/bin/env bash
set -euo pipefail

export EDGE_LAB_RUN_NAME=${EDGE_LAB_RUN_NAME:-"Edge Lab Daily"}
export EDGE_LAB_LOG_FILE=${EDGE_LAB_LOG_FILE:-"/opt/matrix-trader/logs/edge_lab_daily.log"}
export EDGE_LAB_TOP_N=${EDGE_LAB_TOP_N:-120}
export EDGE_LAB_MAX_RUNTIME_MINUTES=${EDGE_LAB_MAX_RUNTIME_MINUTES:-35}
export EDGE_LAB_BATCH_SIZE=${EDGE_LAB_BATCH_SIZE:-40}
export EDGE_LAB_MATERIALIZE_BATCH_SIZE=${EDGE_LAB_MATERIALIZE_BATCH_SIZE:-20000}
export EDGE_LAB_FACTOR_TOP_N=${EDGE_LAB_FACTOR_TOP_N:-10}
export EDGE_LAB_COHORT_RUN_ANALYSIS=false
export EDGE_LAB_COHORT_SYMBOL_LIMIT=${EDGE_LAB_COHORT_SYMBOL_LIMIT:-200}
export EDGE_LAB_COHORT_MAX_RUNTIME_MINUTES=${EDGE_LAB_COHORT_MAX_RUNTIME_MINUTES:-30}

# Refresh every symbol that actually appears in the active cohort before the
# general top-volume pass. A newly reset cohort can be empty, which is harmless.
if ! /opt/matrix-trader/scripts/run_edge_lab_cohort.sh; then
  echo "Edge Lab cohort refresh skipped or failed; continuing daily universe refresh." >> "$EDGE_LAB_LOG_FILE"
fi
exec /opt/matrix-trader/scripts/run_edge_lab_lite.sh
