#!/bin/bash
#
# Headless Brave Browser Wrapper
# Starts Xvfb and Brave with remote debugging enabled
#
set -euo pipefail

DISPLAY_NUM=99
XVFB_PID=""
BRAVE_PID=""
VNC_PID=""

cleanup() {
    echo "Shutting down..."
    if [ -n "$BRAVE_PID" ] && kill -0 "$BRAVE_PID" 2>/dev/null; then
        kill "$BRAVE_PID" 2>/dev/null || true
    fi
    if [ -n "$VNC_PID" ] && kill -0 "$VNC_PID" 2>/dev/null; then
        kill "$VNC_PID" 2>/dev/null || true
    fi
    if [ -n "$XVFB_PID" ] && kill -0 "$XVFB_PID" 2>/dev/null; then
        kill "$XVFB_PID" 2>/dev/null || true
    fi
    exit 0
}

trap cleanup SIGTERM SIGINT EXIT

# Start Xvfb if not already running
if ! pgrep -f "Xvfb :${DISPLAY_NUM}" > /dev/null; then
    echo "Starting Xvfb on display :${DISPLAY_NUM}..."
    Xvfb ":${DISPLAY_NUM}" -screen 0 1920x1080x24 &
    XVFB_PID=$!
    sleep 1
fi

export DISPLAY=":${DISPLAY_NUM}"

# Brave user data directory for session persistence
USER_DATA_DIR="$HOME/.config/brave-headless"
mkdir -p "$USER_DATA_DIR"

# Start x11vnc on localhost only (use SSH tunnel for access)
echo "Starting x11vnc on localhost:5900..."
x11vnc -display ":${DISPLAY_NUM}" -localhost -forever -nopw -quiet &
VNC_PID=$!
sleep 1

echo "Starting Brave with remote debugging on port 9222..."
brave-browser \
    --remote-debugging-port=9222 \
    --remote-debugging-address=127.0.0.1 \
    --user-data-dir="$USER_DATA_DIR" \
    --no-first-run \
    --no-default-browser-check \
    --disable-background-networking \
    --disable-sync \
    --disable-extensions \
    --disable-gpu \
    --start-maximized &
BRAVE_PID=$!

echo "Brave started with PID $BRAVE_PID"
echo "VNC available on localhost:5900 (tunnel with: ssh -N -L 5900:127.0.0.1:5900 oxnard)"

# Wait for Brave to exit
wait $BRAVE_PID
