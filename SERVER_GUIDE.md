# Matrix Trader — Production Server Quick Reference

## Server Details

| Item | Value |
|---|---|
| Provider / region | Vultr, Singapore |
| Production IP | `207.148.66.39` |
| User | `root` |
| SSH auth | Mac SSH key only; password login disabled |
| App URL | `http://207.148.66.39:8080` |
| App dir | `/opt/matrix-trader/` |
| Learner dir | `/opt/mt-learner/` |
| Port | `8080` |
| Services | `matrix-trader`, `mt-learner`, `edge-lab-lite.timer`, `mt7-hermes-weekly.timer` |
| Hermes workstation | Old VPS `62.238.15.113` only, isolated advisory runner |

Legacy/consultancy host: `62.238.15.113` was the old Hetzner VPS. Do not deploy production MT7 there. It now hosts the isolated Hermes consultancy runner.

---

## Access

```bash
# SSH in
ssh root@207.148.66.39

# Open the app in browser
http://207.148.66.39:8080
```

**Claude Cowork access key:** `.claude-vps-key` / `.claude-vps-key.pub` in the repo root (gitignored, rsync-excluded). Dedicated ed25519 key used by Claude sessions: `ssh -i .claude-vps-key root@207.148.66.39`. Authorized on both the Vultr production box and the Hermes host. Revoke by removing the `claude-cowork-mt7` line from `/root/.ssh/authorized_keys` on each server.

---

## Deploy Updates From Your Mac

Prefer targeted deploys when only a few files changed. This avoids copying local scratch files, research artifacts, or dirty worktree state.

```bash
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0

# Examples: deploy only the files you changed
rsync -avz app.py root@207.148.66.39:/opt/matrix-trader/app.py
rsync -avz templates/index.html root@207.148.66.39:/opt/matrix-trader/templates/index.html
rsync -avz lib/mexc_private.py root@207.148.66.39:/opt/matrix-trader/lib/mexc_private.py

# Restart and verify
ssh root@207.148.66.39 "systemctl restart matrix-trader && sleep 2 && systemctl is-active matrix-trader"
```

If a broad sync is truly needed, keep runtime state and secrets excluded:

```bash
rsync -avz \
  --exclude='.env' \
  --exclude='data/' \
  --exclude='__pycache__' \
  --exclude='.git' \
  --exclude='*.pyc' \
  --exclude='.superpowers/' \
  --exclude='.claude-vps-key*' \
  ./ root@207.148.66.39:/opt/matrix-trader/
```

Learner deploys are separate:

```bash
rsync -avz mt-learner/suggester.py root@207.148.66.39:/opt/mt-learner/suggester.py
ssh root@207.148.66.39 "systemctl restart mt-learner && sleep 2 && systemctl is-active mt-learner"
```

---

## Service Management

Run these on the server, or wrap them in `ssh root@207.148.66.39 "..."`

```bash
# Matrix Trader app
systemctl status matrix-trader --no-pager
systemctl restart matrix-trader
journalctl -u matrix-trader -f
journalctl -u matrix-trader -n 80 --no-pager

# mt-learner
systemctl status mt-learner --no-pager
systemctl restart mt-learner
journalctl -u mt-learner -f

# Edge Lab Lite weekly research job
systemctl status edge-lab-lite.timer --no-pager
systemctl list-timers edge-lab-lite.timer --no-pager
systemctl start edge-lab-lite.service
journalctl -u edge-lab-lite.service -n 80 --no-pager
```

Force-kill fallback if the app service hangs:

```bash
pkill -9 python3 2>/dev/null; sleep 3; pkill -9 python3 2>/dev/null; sleep 2
systemctl start matrix-trader
sleep 8
ss -tulnp | grep python
```

---

## Smoke Checks

Run on the server after deploy:

```bash
curl -s http://localhost:8080/ | grep "loadStrategies"
curl -s http://localhost:8080/api/goals
curl -s http://localhost:8080/api/intelligence/suggestions
curl -s http://localhost:8080/api/paper/stats
curl -s http://localhost:8080/api/account/status
curl -s http://localhost:8080/api/account/balance
curl -s http://localhost:8080/api/account/positions
curl -s http://localhost:8080/api/intelligence/hermes
```

Expected current MEXC private status: `connected: true`, USDT equity/balance may be `0.0` until the subaccount is funded.

---

## Hermes Advisory Group

Hermes is an outside consultancy layer, not a production trading service. It runs from the old VPS so it can review MT7 from outside the production box.

| Item | Value |
|---|---|
| Host | `62.238.15.113` |
| Runner | `/opt/mt7-hermes/run_consultancy.sh` |
| Output dir | `/opt/mt7-hermes/out/` |
| Latest old-VPS memo | `/opt/mt7-hermes/out/latest_memo.json` |
| Production memo path | `/opt/matrix-trader/data/hermes/latest_memo.json` |
| Production archive | `/opt/matrix-trader/data/hermes/archive/` |
| Weekly timer | `mt7-hermes-weekly.timer` on Vultr |
| Schedule | Sundays at `05:30 UTC` with up to 30 minutes randomized delay |
| MT7 API | `/api/intelligence/hermes` |
| MT7 UI | Intelligence -> Hermes |

Run a fresh Hermes memo:

```bash
ssh root@62.238.15.113 "/opt/mt7-hermes/run_consultancy.sh"
```

Sync the latest memo into production MT7:

```bash
rsync -avz root@62.238.15.113:/opt/mt7-hermes/out/latest_memo.json /private/tmp/mt7-latest-hermes-memo.json
ssh root@207.148.66.39 "mkdir -p /opt/matrix-trader/data/hermes"
rsync -avz /private/tmp/mt7-latest-hermes-memo.json root@207.148.66.39:/opt/matrix-trader/data/hermes/latest_memo.json
```

Weekly automation:

```bash
# Check next scheduled run
systemctl list-timers mt7-hermes-weekly.timer --no-pager

# Run immediately from Vultr
systemctl start mt7-hermes-weekly.service
journalctl -u mt7-hermes-weekly.service -n 80 --no-pager
```

Safety rules:

- Hermes does not get MEXC or Hyperliquid private keys.
- Hermes does not write MT7 config files.
- Hermes does not auto-apply learner suggestions.
- Hermes does not place trades.
- Hermes memos are advisory; apply changes through MT7 review flows only.

---

## MEXC Subaccount Notes

- MEXC linked IP must be `207.148.66.39`.
- The subaccount API key pair lives only in `/opt/matrix-trader/.env`.
- `MEXC_API_KEY` is the access key; `MEXC_API_SECRET` is the secret key.
- The key needs contract/futures read permissions for account and positions. Trading permissions are only relevant later, and live trading still remains gated by MT7 safety flags.
- `lib/mexc_private.py` uses MEXC contract private endpoints:
  - `/private/account/assets`
  - `/private/position/open_positions`

Never print, commit, paste, or rsync `.env`.

---

## Edge Lab Lite

Edge Lab Lite is the bounded weekly research job. It is not Hermes, not live scoring, and not execution logic.

| Item | Value |
|---|---|
| Timer | `edge-lab-lite.timer` |
| Schedule | Sundays at `03:15 UTC` with up to 20 minutes randomized delay |
| Runner | `/opt/matrix-trader/scripts/run_edge_lab_lite.sh` |
| Log | `/opt/matrix-trader/logs/edge_lab_lite.log` |
| Main DB | `/opt/matrix-trader/data/edge_lab.db` |
| Report | `/opt/matrix-trader/data/factor_report.json` |
| Defaults | top 200 symbols, 75-minute max runtime, batch size 50 |
| Disk guard | Aborts when the data volume has less than 12GB free |

Manual run:

```bash
systemctl start edge-lab-lite.service
tail -f /opt/matrix-trader/logs/edge_lab_lite.log
```

---

## Files You Edit Most

| File | Purpose |
|---|---|
| `app.py` | Entire Flask backend |
| `templates/index.html` | Entire frontend |
| `lib/mexc_private.py` | MEXC private read-only account client |
| `HANDOFF.md` | Authoritative project state |
| `SERVER_GUIDE.md` | Production server operations |
| `.env` | Server-only secrets; never sync, never commit |

---

## Exit SSH

```bash
exit
# or Ctrl+D
```
