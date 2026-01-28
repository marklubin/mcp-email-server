#!/bin/bash
# View MCP router logs on remote server
set -euo pipefail

REMOTE="${MCP_REMOTE:-oxnard}"
LINES="${1:-50}"

ssh "$REMOTE" "sudo journalctl -u mcp-router@\$USER -n $LINES --no-pager"
