#!/bin/bash
# Deploy latest changes to remote server
set -euo pipefail

REMOTE="${MCP_REMOTE:-oxnard}"

echo "Deploying to $REMOTE..."

# Push local changes
git push

# Pull on remote and restart service
ssh "$REMOTE" 'cd ~/mcp-infrastructure && git pull && sudo systemctl restart mcp-router@$USER'

echo "Deployed. Checking status..."
sleep 2
ssh "$REMOTE" 'sudo systemctl status mcp-router@$USER --no-pager | head -15'
