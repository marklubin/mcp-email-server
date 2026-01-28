#!/bin/bash
# Check MCP router status on remote server
set -euo pipefail

REMOTE="${MCP_REMOTE:-oxnard}"

echo "=== Service Status ==="
ssh "$REMOTE" 'sudo systemctl status mcp-router@$USER --no-pager'

echo ""
echo "=== Health Check ==="
curl -s https://mcp-router-proxy.melubin.workers.dev/health | jq .
