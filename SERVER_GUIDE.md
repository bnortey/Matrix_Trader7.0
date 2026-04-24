# Matrix Trader — VPS Quick Reference

## Server Details
| | |
|---|---|
| IP | `62.238.15.113` |
| User | `root` |
| App dir | `/opt/matrix-trader/` |
| Port | `8080` |
| Service | `matrix-trader` |

---

## Access

```bash
# SSH in
ssh root@62.238.15.113

# Open the app in browser
http://62.238.15.113:8080
```

---

## Deploy Updates (from your Mac)

```bash
# 1. Sync local code to server (run from project root)
cd /Users/bnortey/Documents/coding_projects/Matrix_Trader_7.0
rsync -avz --exclude='.env' --exclude='data/' --exclude='__pycache__' --exclude='.git' --exclude='*.pyc' ./ root@62.238.15.113:/opt/matrix-trader/

# 2. Restart the service
ssh root@62.238.15.113 "systemctl restart matrix-trader"

# 3. Verify it's running
ssh root@62.238.15.113 "systemctl status matrix-trader --no-pager"
```

---

## Service Management (run these on the server)

```bash
# Status
systemctl status matrix-trader --no-pager

# Restart
systemctl restart matrix-trader

# Stop
systemctl stop matrix-trader

# Start
systemctl start matrix-trader

# View live logs
journalctl -u matrix-trader -f

# View last 50 log lines
journalctl -u matrix-trader -n 50 --no-pager
```

---

## If the Service Won't Restart

```bash
# Force kill all python3 processes, then restart
pkill -9 python3 2>/dev/null; sleep 3; pkill -9 python3 2>/dev/null; sleep 2
systemctl start matrix-trader
sleep 8
ss -tulnp | grep python   # confirm it's listening on 8080
```

---

## Smoke Checks (after deploy)

```bash
# App is serving the latest template
curl -s http://localhost:8080/ | grep "loadStrategies"

# API is responding
curl -s http://localhost:8080/api/strategies
```

---

## Files You'll Edit

| File | Purpose |
|---|---|
| `app.py` | Entire Flask backend |
| `templates/index.html` | Entire frontend |
| `HANDOFF.md` | Project state — update after every session |
| `.env` | API keys — **never sync, never commit** |

`.env` is excluded from rsync automatically. Never touch it via deploy commands.

---

## Exit SSH

```bash
exit
# or Ctrl+D
```
