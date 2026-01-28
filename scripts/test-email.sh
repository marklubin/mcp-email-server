#!/bin/bash
# Test email backend on remote server
set -euo pipefail

REMOTE="${MCP_REMOTE:-oxnard}"
ACTION="${1:-list}"

case "$ACTION" in
    list)
        ssh "$REMOTE" 'cd ~/mcp-infrastructure && ~/.local/bin/uv run python -c "
import asyncio, sys
sys.path.insert(0, \"router\")
from dotenv import load_dotenv
load_dotenv()
from backends.email import list_emails

async def test():
    results = await list_emails.fn(limit=5)
    for r in results:
        print(f\"{r.get(\"local_time\", \"?\")} | {r.get(\"subject\", \"?\")[:50]}\")
asyncio.run(test())
"'
        ;;
    search)
        QUERY="${2:-test}"
        ssh "$REMOTE" "cd ~/mcp-infrastructure && ~/.local/bin/uv run python -c \"
import asyncio, sys
sys.path.insert(0, 'router')
from dotenv import load_dotenv
load_dotenv()
from backends.email import search_emails

async def test():
    results = await search_emails.fn('$QUERY', limit=5)
    print(f'Found {len(results)} results for: $QUERY')
    for r in results:
        print(f\\\"{r.get('local_time', '?')} | {r.get('subject', '?')[:50]}\\\")
asyncio.run(test())
\""
        ;;
    *)
        echo "Usage: $0 [list|search <query>]"
        exit 1
        ;;
esac
