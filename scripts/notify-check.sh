#!/usr/bin/env bash
# notify-check — show agent digest on shell startup
# Source this from .zshrc: source ~/mcp-infrastructure/scripts/notify-check.sh
#
# Queries the MCP router over Tailscale. Always shows a header,
# lists unread notifications if any, otherwise shows all-clear.

NOTIFY_HOST="${NOTIFY_HOST:-100.96.58.51}"
NOTIFY_PORT="${NOTIFY_PORT:-8080}"
NOTIFY_URL="http://${NOTIFY_HOST}:${NOTIFY_PORT}/notifications"

_notify_check() {
    # Colors
    local reset=$'\033[0m'
    local dim=$'\033[2m'
    local bold=$'\033[1m'
    local yellow=$'\033[33m'
    local red=$'\033[31m'
    local cyan=$'\033[36m'
    local green=$'\033[32m'
    local white=$'\033[37m'
    local mag=$'\033[35m'

    local W=62

    # Greeting based on time of day
    local hour=$(date +%H)
    local greeting="Good morning"
    if (( hour >= 17 )); then
        greeting="Good evening"
    elif (( hour >= 12 )); then
        greeting="Good afternoon"
    fi

    local dateline
    dateline=$(date '+%A, %B %-d')

    # Try to reach the notification service
    local summary response
    summary=$(curl -sf --max-time 2 "${NOTIFY_URL}/summary" 2>/dev/null)
    local reachable=$?

    local unread=0
    if [[ $reachable -eq 0 && -n "$summary" ]]; then
        unread=$(echo "$summary" | jq -r '.total_unread // 0' 2>/dev/null)
    fi

    echo ""
    echo "${dim}╭$(printf '─%.0s' $(seq 1 $W))╮${reset}"
    echo "${dim}│${reset} ${bold}${mag}Agent Digest${reset}${dim} — ${dateline}$(printf ' %.0s' $(seq 1 $(( W - 17 - ${#dateline} ))))│${reset}"
    echo "${dim}│${reset} ${dim}${greeting}.$(printf ' %.0s' $(seq 1 $(( W - ${#greeting} - 3 ))))${reset}${dim}│${reset}"

    if [[ $reachable -ne 0 ]]; then
        echo "${dim}├$(printf '─%.0s' $(seq 1 $W))┤${reset}"
        echo "${dim}│${reset} ${yellow}Notification service unreachable${reset}$(printf ' %.0s' $(seq 1 $(( W - 34 ))))${dim}│${reset}"
    elif [[ "$unread" == "0" || -z "$unread" ]]; then
        echo "${dim}├$(printf '─%.0s' $(seq 1 $W))┤${reset}"
        echo "${dim}│${reset} ${green}All clear${reset} ${dim}— no unread notifications$(printf ' %.0s' $(seq 1 $(( W - 42 ))))│${reset}"
    else
        response=$(curl -sf --max-time 2 "${NOTIFY_URL}?unread_only=true&limit=10" 2>/dev/null)
        echo "${dim}├$(printf '─%.0s' $(seq 1 $W))┤${reset}"

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
            local line="${dim}│${reset} ${tag} ${dim}${source}${reset} ${white}${title}${reset}"

            # Right-align timestamp
            if [[ -n "$ts" ]]; then
                local visible_len=$(( ${#level} + ${#source} + ${#title} + 5 ))
                local spaces=$(( W - 2 - visible_len - ${#ts} ))
                (( spaces < 1 )) && spaces=1
                line="${line}$(printf ' %.0s' $(seq 1 $spaces))${dim}${ts}${reset} ${dim}│${reset}"
            else
                line="${line} ${dim}│${reset}"
            fi

            echo "$line"
        done
    fi

    echo "${dim}╰$(printf '─%.0s' $(seq 1 $W))╯${reset}"
    echo ""
}

# Run in a subshell so it doesn't block anything
( _notify_check & )
