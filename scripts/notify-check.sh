#!/usr/bin/env bash
# notify-check — agent digest on shell startup
# Source from .zshrc (before p10k instant prompt)
#
# Queries notifications + lab status over Tailscale.
# Shows grouped sections with visual separation.

NOTIFY_HOST="${NOTIFY_HOST:-100.96.58.51}"
NOTIFY_PORT="${NOTIFY_PORT:-8080}"
_BASE="http://${NOTIFY_HOST}:${NOTIFY_PORT}"

_notify_check() {
    # Colors
    local r=$'\033[0m'
    local d=$'\033[2m'
    local b=$'\033[1m'
    local yel=$'\033[33m'
    local red=$'\033[31m'
    local cyn=$'\033[36m'
    local grn=$'\033[32m'
    local wht=$'\033[37m'
    local mag=$'\033[35m'

    local W=62

    # ── helpers ──────────────────────────────────────────────────
    _hr()      { echo "${d}├$(printf '─%.0s' $(seq 1 $W))┤${r}"; }
    _blank()   { echo "${d}│${r}$(printf ' %.0s' $(seq 1 $W))${d}│${r}"; }
    _top()     { echo "${d}╭$(printf '─%.0s' $(seq 1 $W))╮${r}"; }
    _bot()     { echo "${d}╰$(printf '─%.0s' $(seq 1 $W))╯${r}"; }

    _pad_line() {
        # Usage: _pad_line "visible text" total_width
        # Prints spaces to fill from current visible length to width
        local vis_len=$1 width=$2
        local pad=$(( width - vis_len ))
        (( pad < 0 )) && pad=0
        printf ' %.0s' $(seq 1 $pad) 2>/dev/null
    }

    _section_header() {
        local label="$1"
        local label_len=${#label}
        local dashes=$(( W - label_len - 3 ))
        (( dashes < 3 )) && dashes=3
        echo "${d}├─ ${r}${d}${label} $(printf '─%.0s' $(seq 1 $dashes))┤${r}"
    }

    _trunc() {
        local text="$1" max="$2"
        if (( ${#text} > max )); then
            echo "${text:0:$(( max - 1 ))}…"
        else
            echo "$text"
        fi
    }

    # ── greeting ─────────────────────────────────────────────────
    local hour=$(date +%H)
    local greeting="Good morning"
    if (( hour >= 17 )); then
        greeting="Good evening"
    elif (( hour >= 12 )); then
        greeting="Good afternoon"
    fi
    local dateline
    dateline=$(date '+%A, %B %-d')

    # ── fetch data ───────────────────────────────────────────────
    local notif_summary notif_list lab_digest
    notif_summary=$(curl -sf --max-time 2 "${_BASE}/notifications/summary" 2>/dev/null)
    local reachable=$?

    local unread=0
    if [[ $reachable -eq 0 && -n "$notif_summary" ]]; then
        unread=$(echo "$notif_summary" | jq -r '.total_unread // 0' 2>/dev/null)
        if [[ "$unread" != "0" ]]; then
            notif_list=$(curl -sf --max-time 2 "${_BASE}/notifications?unread_only=true&limit=5" 2>/dev/null)
        fi
    fi

    lab_digest=$(curl -sf --max-time 2 "${_BASE}/lab/digest" 2>/dev/null)
    local lab_outstanding=0 lab_recent=0
    if [[ -n "$lab_digest" ]]; then
        lab_outstanding=$(echo "$lab_digest" | jq -r '.outstanding_count // 0' 2>/dev/null)
        lab_recent=$(echo "$lab_digest" | jq -r '.recent_count // 0' 2>/dev/null)
    fi

    # ── render ───────────────────────────────────────────────────
    echo ""
    _top

    # Header
    local hdr_pad=$(( W - 17 - ${#dateline} ))
    echo "${d}│${r} ${b}${mag}Agent Digest${r}${d} — ${dateline}$(_pad_line 0 $hdr_pad)│${r}"
    local greet_pad=$(( W - ${#greeting} - 3 ))
    echo "${d}│${r} ${d}${greeting}.$(_pad_line 0 $greet_pad)${r}${d}│${r}"

    # ── Notifications section ────────────────────────────────────
    if [[ $reachable -ne 0 ]]; then
        _section_header "Notifications"
        local msg="Service unreachable"
        echo "${d}│${r}  ${yel}${msg}${r}$(_pad_line $(( ${#msg} + 2 )) $W)${d}│${r}"
    elif [[ "$unread" == "0" || -z "$unread" ]]; then
        _section_header "Notifications"
        local msg="All clear — nothing unread"
        echo "${d}│${r}  ${grn}${msg}${r}$(_pad_line $(( ${#msg} + 2 )) $W)${d}│${r}"
    else
        _section_header "Notifications (${unread})"
        echo "$notif_list" | jq -r '.notifications[] | "\(.level)\t\(.source)\t\(.title)\t\(.created_at)"' 2>/dev/null | while IFS=$'\t' read -r level source title created_at; do
            local lc="$cyn"
            case "$level" in
                error)   lc="$red" ;;
                warning) lc="$yel" ;;
            esac

            local ts=""
            [[ -n "$created_at" ]] && ts=$(echo "$created_at" | grep -oP '\d{2}:\d{2}' | head -1)

            # Truncate title to fit
            local avail=$(( W - 8 - ${#level} - ${#source} - ${#ts} ))
            title=$(_trunc "$title" $avail)

            local vis=$(( ${#level} + ${#source} + ${#title} + ${#ts} + 6 ))
            echo "${d}│${r}  ${lc}${level}${r} ${d}${source}${r} ${wht}${title}${r}$(_pad_line $vis $W)${d}${ts} │${r}"
        done
    fi

    # ── Lab section ──────────────────────────────────────────────
    if [[ -n "$lab_digest" ]]; then
        if (( lab_outstanding > 0 || lab_recent > 0 )); then
            _section_header "Lab"

            # Outstanding hypotheses
            if (( lab_outstanding > 0 )); then
                echo "$lab_digest" | jq -r '.outstanding[] | "\(.status)\t\(.hypothesis)\t\(.id)"' 2>/dev/null | while IFS=$'\t' read -r hstatus hypothesis hid; do
                    local sc="$yel"
                    local icon="◌"
                    if [[ "$hstatus" == "processing" ]]; then
                        sc="$cyn"
                        icon="◉"
                    fi

                    hypothesis=$(_trunc "$hypothesis" $(( W - ${#hstatus} - 8 )))
                    local vis=$(( ${#hstatus} + ${#hypothesis} + 5 ))
                    echo "${d}│${r}  ${sc}${icon} ${hstatus}${r} ${wht}${hypothesis}${r}$(_pad_line $vis $W)${d}│${r}"
                done
            fi

            # Recent reports
            if (( lab_recent > 0 )); then
                echo "$lab_digest" | jq -r '.recent_reports[] | "\(.verdict)\t\(.confidence)\t\(.hypothesis)\t\(.created_at)"' 2>/dev/null | while IFS=$'\t' read -r verdict confidence hypothesis created_at; do
                    local vc="$grn"
                    local icon="✓"
                    case "$verdict" in
                        REFUTED)      vc="$red"; icon="✗" ;;
                        INCONCLUSIVE) vc="$yel"; icon="?" ;;
                        FAILED)       vc="$red"; icon="!" ;;
                    esac

                    local ts=""
                    [[ -n "$created_at" ]] && ts=$(echo "$created_at" | grep -oP '\d{2}:\d{2}' | head -1)

                    hypothesis=$(_trunc "$hypothesis" $(( W - ${#verdict} - ${#ts} - 12 )))
                    local vis=$(( ${#verdict} + ${#hypothesis} + ${#ts} + 9 ))
                    echo "${d}│${r}  ${vc}${icon} ${verdict}${r} ${d}(${confidence}/5)${r} ${wht}${hypothesis}${r}$(_pad_line $vis $W)${d}${ts} │${r}"
                done
            fi
        fi
    fi

    _bot
    echo ""
}

# Run synchronously — curl timeout (2s) keeps it fast
_notify_check
