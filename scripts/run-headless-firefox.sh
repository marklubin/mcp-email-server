#!/bin/bash
#
# Headless Firefox Browser Wrapper
#

DISPLAY_NUM=99

cleanup() {
    echo "Shutting down..."
    pkill -f "firefox.*firefox-headless" 2>/dev/null || true
    pkill -f "x11vnc.*:${DISPLAY_NUM}" 2>/dev/null || true
    exit 0
}

trap cleanup EXIT INT TERM

# Start Xvfb if not already running
if ! pgrep -f "Xvfb :${DISPLAY_NUM}" > /dev/null; then
    echo "Starting Xvfb on display :${DISPLAY_NUM}..."
    Xvfb ":${DISPLAY_NUM}" -screen 0 1920x1080x24 &
    sleep 1
fi

export DISPLAY=":${DISPLAY_NUM}"

# Firefox profile directory for session persistence
PROFILE_DIR="$HOME/.mozilla/firefox-headless"
mkdir -p "$PROFILE_DIR"

# Start x11vnc on localhost only
if ! pgrep -f "x11vnc.*:${DISPLAY_NUM}" > /dev/null; then
    echo "Starting x11vnc on localhost:5900..."
    x11vnc -display ":${DISPLAY_NUM}" -localhost -forever -nopw -quiet &
    sleep 1
fi

echo "Starting Firefox with remote debugging on port 9222..."
firefox-esr \
    --remote-debugging-port 9222 \
    --profile "$PROFILE_DIR" \
    --no-remote \
    --width 1920 \
    --height 1080 &

FIREFOX_PID=$!
echo "Firefox started with PID $FIREFOX_PID"
echo "VNC available on localhost:5900"

wait $FIREFOX_PID
