#!/bin/bash
# Start Second Brain combined server (Flask web UI + MCP) + Cloudflare Tunnel
# Combined server on port 5001, Cloudflare exposes it at brain.broadburch.dev
#
# Designed for LaunchAgent (KeepAlive): runs a health-check loop that
# restarts the server or tunnel if either dies.

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PYTHON="$DIR/venv/bin/python3"
CLOUDFLARED="$HOME/bin/cloudflared"
LOG_DIR="$DIR/logs"
SERVER_PORT=5001
TUNNEL_NAME="second-brain"
TUNNEL_DOMAIN="brain.broadburch.dev"
HEALTH_INTERVAL=30          # seconds between health checks
MAX_FAILURES=5              # consecutive failures before full restart

mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_DIR/supervisor.log"; }

# --- Preflight checks ---
if [ ! -x "$PYTHON" ]; then
    log "FATAL: venv not found at $DIR/.venv"
    exit 1
fi
if [ ! -x "$CLOUDFLARED" ]; then
    log "FATAL: cloudflared not found at $CLOUDFLARED"
    exit 1
fi

# --- Process management helpers ---
SERVER_PID=""
TUNNEL_PID=""

cleanup() {
    log "Shutting down (signal received)..."
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
    [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null
    wait 2>/dev/null
    log "Shutdown complete."
    exit 0
}
trap cleanup SIGTERM SIGINT SIGHUP

start_server() {
    log "Starting combined server on port $SERVER_PORT..."
    PORT=$SERVER_PORT "$PYTHON" "$DIR/server.py" >> "$LOG_DIR/server.log" 2>&1 &
    SERVER_PID=$!
    sleep 3
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        log "ERROR: Server failed to start (PID $SERVER_PID)"
        SERVER_PID=""
        return 1
    fi
    log "Server running (PID $SERVER_PID)"
    return 0
}

start_tunnel() {
    log "Starting Cloudflare tunnel ($TUNNEL_NAME)..."
    "$CLOUDFLARED" tunnel run "$TUNNEL_NAME" >> "$LOG_DIR/cloudflared.log" 2>&1 &
    TUNNEL_PID=$!
    sleep 5
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        log "ERROR: Tunnel failed to start (PID $TUNNEL_PID)"
        TUNNEL_PID=""
        return 1
    fi
    log "Tunnel running (PID $TUNNEL_PID)"
    return 0
}

server_healthy() {
    # Check process is alive AND port is responding
    [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null && \
        curl -sf -o /dev/null --max-time 5 "http://localhost:$SERVER_PORT/" 2>/dev/null
}

tunnel_healthy() {
    [ -n "$TUNNEL_PID" ] && kill -0 "$TUNNEL_PID" 2>/dev/null
}

# --- Initial startup ---
start_server || { log "FATAL: Server failed on initial start"; exit 1; }
start_tunnel || { log "FATAL: Tunnel failed on initial start"; exit 1; }

log "=========================================="
log "  Second Brain is LIVE"
log "  Web UI: https://$TUNNEL_DOMAIN"
log "  MCP endpoint: https://$TUNNEL_DOMAIN/mcp"
log "=========================================="

# --- Health-check loop ---
server_failures=0
tunnel_failures=0

while true; do
    sleep "$HEALTH_INTERVAL"

    # Check server
    if server_healthy; then
        server_failures=0
    else
        server_failures=$((server_failures + 1))
        log "WARN: Server unhealthy (failure $server_failures/$MAX_FAILURES)"
        if [ "$server_failures" -ge "$MAX_FAILURES" ]; then
            log "Restarting server after $MAX_FAILURES consecutive failures..."
            [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null && wait "$SERVER_PID" 2>/dev/null
            if start_server; then
                server_failures=0
            else
                log "ERROR: Server restart failed, will retry next cycle"
            fi
        fi
    fi

    # Check tunnel
    if tunnel_healthy; then
        tunnel_failures=0
    else
        tunnel_failures=$((tunnel_failures + 1))
        log "WARN: Tunnel unhealthy (failure $tunnel_failures/$MAX_FAILURES)"
        if [ "$tunnel_failures" -ge "$MAX_FAILURES" ]; then
            log "Restarting tunnel after $MAX_FAILURES consecutive failures..."
            [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null && wait "$TUNNEL_PID" 2>/dev/null
            if start_tunnel; then
                tunnel_failures=0
            else
                log "ERROR: Tunnel restart failed, will retry next cycle"
            fi
        fi
    fi
done
