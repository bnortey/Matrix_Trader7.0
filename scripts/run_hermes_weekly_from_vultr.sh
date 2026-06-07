#!/usr/bin/env bash
set -euo pipefail

OLD_VPS="${OLD_VPS:-62.238.15.113}"
OLD_USER="${OLD_USER:-root}"
OLD_RUNNER="${OLD_RUNNER:-/opt/mt7-hermes/run_consultancy.sh}"
SSH_KEY="${SSH_KEY:-/root/.ssh/mt7_hermes_vultr_to_old}"
MT7_DIR="${MT7_DIR:-/opt/matrix-trader}"
HERMES_DATA="$MT7_DIR/data/hermes"
HERMES_RESEARCH="$HERMES_DATA/research"
LOG_DIR="$MT7_DIR/logs"
SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20)

mkdir -p "$HERMES_DATA/archive" "$HERMES_RESEARCH/archive" "$LOG_DIR"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] starting Hermes weekly consultancy"
ssh "${SSH_OPTS[@]}" "$OLD_USER@$OLD_VPS" "$OLD_RUNNER"

rsync -az -e "ssh -i $SSH_KEY -o BatchMode=yes -o ConnectTimeout=20" \
  "$OLD_USER@$OLD_VPS:/opt/mt7-hermes/out/latest_memo.json" "$HERMES_DATA/latest_memo.json"
rsync -az -e "ssh -i $SSH_KEY -o BatchMode=yes -o ConnectTimeout=20" \
  "$OLD_USER@$OLD_VPS:/opt/mt7-hermes/out/archive/" "$HERMES_DATA/archive/"
rsync -az -e "ssh -i $SSH_KEY -o BatchMode=yes -o ConnectTimeout=20" \
  "$OLD_USER@$OLD_VPS:/opt/mt7-hermes/out/research/" "$HERMES_RESEARCH/"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Hermes memo synced to $HERMES_DATA"
