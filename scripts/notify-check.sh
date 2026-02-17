#!/usr/bin/env bash
# notify-check — show unread notifications on shell startup
# Source this from .zshrc: source ~/mcp-infrastructure/scripts/notify-check.sh
#
# Queries the MCP router over Tailscale. Displays a box with unread
# notifications, then scrolls off naturally. Runs async so it never
# blocks shell startup.

NOTIFY_HOST="${NOTIFY_HOST:-100.96.58.51}"
NOTIFY_PORT="${NOTIFY_PORT:-8080}"
NOTIFY_URL="http://${NOTIFY_HOST}:${NOTIFY_PORT}/notifications"

_notify_check() {
    local response
    response=$(curl -sf --max-time 2 "${NOTIFY_URL}?unread_only=true&limit=10" 2>/dev/null) || return

    local count
    count=$(echo "$response" | jq -r '.count // 0' 2>/dev/null)
    [[ "$count" == "0" || -z "$count" ]] && return

    # Colors
    local reset=$'\033[0m'
    local dim=$'\033[2m'
    local bold=$'\033[1m'
    local yellow=$'\033[33m'
    local red=$'\033[31m'
    local cyan=$'\033[36m'
    local white=$'\033[37m'

    # Box drawing
    local top="╭─── Notifications (${count} unread) "
    local pad_len=$(( 60 - ${#top} ))
    (( pad_len < 3 )) && pad_len=3
    top="${top}$(printf '─%.0s' $(seq 1 $pad_len))╮"

    echo ""
    echo "${dim}${top}${reset}"

    echo "$response" | jq -r '.notifications[] | "\(.level)\t\(.source)\t\(.title)\t\(.created_at)"' 2>/dev/null | while IFS=$'\t' read -r level source title created_at; do
        # Pick color by level
        local lc="$cyan"
        case "$level" in
            error)   lc="$red" ;;
            warning) lc="$yellow" ;;
        esac

        # Extract just HH:MM from the timestamp
        local ts=""
        if [[ -n "$created_at" ]]; then
            ts=$(echo "$created_at" | grep -oP '\d{2}:\d{2}' | head -1)
        fi

        local tag="${lc}${level}${reset}"
        local line="│ ${tag} ${dim}${source}${reset} ${white}${title}${reset}"

        # Right-align timestamp
        if [[ -n "$ts" ]]; then
            local visible_len=$(( ${#level} + ${#source} + ${#title} + 5 ))
            local spaces=$(( 58 - visible_len - ${#ts} ))
            (( spaces < 1 )) && spaces=1
            line="${line}$(printf ' %.0s' $(seq 1 $spaces))${dim}${ts}${reset} │"
        else
            line="${line} │"
        fi

        echo "$line"
    done

    echo "${dim}╰$(printf '─%.0s' $(seq 1 60))╯${reset}"
    echo ""
}

# Run in a subshell so it doesn't block anything
( _notify_check & )
