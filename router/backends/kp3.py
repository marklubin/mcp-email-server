"""KP3 backend - passage storage and hybrid search."""

import os
from datetime import datetime, timezone

import aiohttp
from fastmcp import FastMCP

mcp = FastMCP('kp3')

KP3_HOST = os.environ.get('KP3_HOST', '127.0.0.1')
KP3_PORT = int(os.environ.get('KP3_PORT', '8081'))
KP3_AGENT_ID = os.environ.get('KP3_AGENT_ID', 'MCP_PROXY_SERVER_CLIENT')


@mcp.tool()
async def search(query: str, limit: int = 10) -> dict:
    """Search passages using hybrid search (FTS + semantic + recency).

    Args:
        query: Search query text
        limit: Maximum results (default: 10, max: 50)

    Returns:
        Search results with id, content, passage_type, score
    """
    limit = min(max(1, limit), 50)
    url = f"http://{KP3_HOST}:{KP3_PORT}/passages/search"
    params = {"query": query, "mode": "hybrid", "limit": limit}
    headers = {"X-Agent-ID": KP3_AGENT_ID}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status != 200:
                return {"error": f"kp3 returned {resp.status}", "detail": await resp.text()}
            return await resp.json()


@mcp.tool()
async def put(content: str) -> dict:
    """Store a new passage in kp3.

    Args:
        content: Text content to store

    Returns:
        Created passage with id, content, passage_type
    """
    now = datetime.now(timezone.utc).isoformat()
    url = f"http://{KP3_HOST}:{KP3_PORT}/passages"
    headers = {"X-Agent-ID": KP3_AGENT_ID, "Content-Type": "application/json"}
    payload = {
        "content": content,
        "passage_type": "EXTERNAL_ENTRY",
        "period_start": now,
        "period_end": now,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status not in (200, 201):
                return {"error": f"kp3 returned {resp.status}", "detail": await resp.text()}
            return await resp.json()
