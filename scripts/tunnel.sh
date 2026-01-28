#!/bin/bash
set -euo pipefail

ACTION="${1:-status}"
REMOTE="${MCP_REMOTE:-oxnard}"

case "$ACTION" in
    status)
        ssh "$REMOTE" 'cloudflared tunnel info mcp-router 2>/dev/null || echo "Tunnel not found"'
        ;;
    logs)
        ssh "$REMOTE" "sudo journalctl -u cloudflared -n ${2:-50} --no-pager"
        ;;
    restart)
        ssh "$REMOTE" 'sudo systemctl restart cloudflared'
        ;;
    *)
        echo "Usage: $0 [status|logs [n]|restart]"
        exit 1
        ;;
esac
