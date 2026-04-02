#!/bin/bash
# Start Second Brain combined server (Flask web UI + MCP) + Cloudflare Tunnel
# Combined server on port 5001, Cloudflare exposes it at brain.broadburch.dev

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Use project venv
PYTHON="$DIR/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    echo "ERROR: venv not found at $DIR/.venv" >&2
    exit 1
fi

CLOUDFLARED="/usr/local/bin/cloudflared"
if [ ! -x "$CLOUDFLARED" ]; then
    echo "ERROR: cloudflared not found" >&2
    exit 1
fi

LOG_DIR="$DIR/logs"
mkdir -p "$LOG_DIR"

SERVER_PORT=5001
TUNNEL_DOMAIN="brain.broadburch.dev"

# Kill any existing instances
pkill -f "server.py" 2>/dev/null || true
pkill -f "mcp_server.py" 2>/dev/null || true
pkill -f "cloudflared tunnel run" 2>/dev/null || true
sleep 1

echo "$(date): Starting combined server on port $SERVER_PORT..." >> "$LOG_DIR/server.log"

# Start combined server (Flask + MCP)
PORT=$SERVER_PORT $PYTHON "$DIR/server.py" >> "$LOG_DIR/server.log" 2>&1 &
SERVER_PID=$!
echo "Combined server PID: $SERVER_PID"

# Wait for server to be ready
sleep 3

if ! kill -0 $SERVER_PID 2>/dev/null; then
    echo "ERROR: Combined server failed to start. Check logs/server.log" >&2
    exit 1
fi

# Start Cloudflare Tunnel
echo "$(date): Starting Cloudflare tunnel..." >> "$LOG_DIR/cloudflared.log"
$CLOUDFLARED tunnel run second-brain >> "$LOG_DIR/cloudflared.log" 2>&1 &
TUNNEL_PID=$!
echo "Cloudflare tunnel PID: $TUNNEL_PID"

sleep 5

if kill -0 $TUNNEL_PID 2>/dev/null; then
    echo ""
    echo "=========================================="
    echo "  Second Brain is LIVE"
    echo "  Web UI: https://$TUNNEL_DOMAIN"
    echo "  MCP endpoint: https://$TUNNEL_DOMAIN/mcp"
    echo "  API endpoint: https://$TUNNEL_DOMAIN/api/add"
    echo "=========================================="
    echo ""
else
    echo "ERROR: Cloudflare tunnel failed to start. Check logs/cloudflared.log" >&2
    exit 1
fi

# Keep script running (so LaunchAgent stays alive)
wait
