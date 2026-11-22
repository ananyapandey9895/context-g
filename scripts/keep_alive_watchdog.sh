#!/usr/bin/env bash
# ContextPilot AI - Continuous Background Keep-Alive Supervisor
# Ensures caffeinate, Cloud Run, and the public tunnel stay alive 24/7.

REPO_DIR="/Users/aarush/Desktop/Ctxly/context-g"
LOG_DIR="$HOME/.ctxly/logs"
mkdir -p "$LOG_DIR"

echo "[Watchdog] Starting ContextPilot 24/7 Keep-Alive Supervisor..." >> "$LOG_DIR/supervisor.log"

while true; do
    # 1. Ensure Caffeinate is running (prevents macOS sleep)
    if ! pgrep -f "caffeinate -d -i -m -u" > /dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting caffeinate..." >> "$LOG_DIR/supervisor.log"
        caffeinate -d -i -m -u &
    fi

    # 2. Ensure Cloud Run server on 8080 is healthy
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/ 2>/dev/null)
    if [ "$HTTP_CODE" != "200" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cloud Run unhealthy (HTTP $HTTP_CODE). Restarting..." >> "$LOG_DIR/supervisor.log"
        pkill -f "contextpilot.cloud_run" 2>/dev/null
        cd "$REPO_DIR" && PORT=8080 .venv/bin/python3 -m contextpilot.cloud_run >> "$LOG_DIR/cloud_run.log" 2>&1 &
        sleep 2
    fi

    # 3. Ensure cloudflared tunnel is running
    if ! pgrep -f "cloudflared tunnel" > /dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Tunnel down. Restarting cloudflared..." >> "$LOG_DIR/supervisor.log"
        cloudflared tunnel --url http://127.0.0.1:8080 >> "$LOG_DIR/tunnel.log" 2>&1 &
    fi

    sleep 10
done
