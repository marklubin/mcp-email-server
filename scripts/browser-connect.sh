#!/bin/bash
#
# Connect to headless Brave on oxnard via SSH tunnel
# Creates a tunnel so you can use chrome://inspect locally
#
set -euo pipefail

HOST="${1:-oxnard}"
LOCAL_PORT="${2:-9222}"
REMOTE_PORT="${3:-9222}"

echo "Creating SSH tunnel to $HOST..."
echo "Local port $LOCAL_PORT -> Remote port $REMOTE_PORT"
echo ""
echo "To inspect the browser:"
echo "  1. Open Chrome/Brave"
echo "  2. Go to chrome://inspect"
echo "  3. Click 'Configure...' and add localhost:$LOCAL_PORT"
echo "  4. Your remote browser tabs will appear under 'Remote Target'"
echo ""
echo "Press Ctrl+C to close the tunnel"
echo ""

ssh -N -L "${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" "$HOST"
