"""Blah backend - rant suggestion queue."""

import os
from typing import Optional

import aiohttp
from fastmcp import FastMCP

mcp = FastMCP('blah')

BLAH_SUGGEST_URL = os.environ.get('BLAH_SUGGEST_URL', '')
BLAH_SUGGEST_TOKEN = os.environ.get('BLAH_SUGGEST_TOKEN', '')


@mcp.tool()
async def rant_suggestion(idea: str, tags: Optional[list[str]] = None) -> dict:
    """Submit an idea to the blah rant suggestion queue for downstream processing.

    Args:
        idea: The idea or rant topic to suggest
        tags: Optional list of tags (default: ["ai"])

    Returns:
        Confirmation from the blah-suggest worker
    """
    if not BLAH_SUGGEST_URL or not BLAH_SUGGEST_TOKEN:
        return {"error": "BLAH_SUGGEST_URL and BLAH_SUGGEST_TOKEN must be set"}

    payload = {
        "idea": idea,
        "source": "agent",
        "tags": tags or ["ai"],
    }
    headers = {
        "Authorization": f"Bearer {BLAH_SUGGEST_TOKEN}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(BLAH_SUGGEST_URL, json=payload, headers=headers) as resp:
            if resp.status != 200:
                return {"error": f"blah-suggest returned {resp.status}", "detail": await resp.text()}
            return await resp.json()
