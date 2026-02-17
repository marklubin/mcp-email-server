"""Letta backend - AI agent management via Letta server REST API."""

import os
from typing import Optional

import aiohttp
from fastmcp import FastMCP

mcp = FastMCP("letta")

LETTA_BASE = os.environ.get("LETTA_BASE_URL", "http://127.0.0.1:8283")


async def _request(method: str, path: str, json=None, params=None) -> dict:
    url = f"{LETTA_BASE}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.request(method, url, json=json, params=params) as resp:
            if resp.status not in (200, 201):
                return {"error": f"Letta returned {resp.status}", "detail": await resp.text()}
            text = await resp.text()
            if not text:
                return {"success": True}
            return await resp.json(content_type=None)


@mcp.tool()
async def list_agents(limit: int = 20, name: Optional[str] = None) -> dict:
    """List Letta agents.

    Args:
        limit: Max agents to return (default: 20)
        name: Filter by agent name (exact match)

    Returns:
        List of agents with id, name, description, model, and created_at
    """
    params = {"limit": min(limit, 100)}
    if name:
        params["name"] = name
    agents = await _request("GET", "/v1/agents/", params=params)
    if isinstance(agents, dict) and "error" in agents:
        return agents
    return {
        "agents": [
            {
                "id": a["id"],
                "name": a.get("name"),
                "description": a.get("description"),
                "agent_type": a.get("agent_type"),
                "model": a.get("model"),
                "created_at": a.get("created_at"),
            }
            for a in agents
        ],
        "count": len(agents),
    }


@mcp.tool()
async def get_agent(agent_id: str) -> dict:
    """Get full details for a specific Letta agent.

    Args:
        agent_id: Agent ID (format: agent-<uuid>)

    Returns:
        Agent details including memory blocks, tools, model config
    """
    return await _request("GET", f"/v1/agents/{agent_id}")


@mcp.tool()
async def create_agent(
    name: str,
    description: str = "",
    model: str = "openai/gpt-4o",
    system: Optional[str] = None,
    human_block: str = "The user hasn't shared personal details yet.",
    persona_block: str = "I am a helpful AI assistant running on a Letta server.",
) -> dict:
    """Create a new Letta agent.

    Args:
        name: Agent name
        description: Agent description
        model: LLM model handle (default: openai/gpt-4o)
        system: Custom system prompt (uses Letta default if not set)
        human_block: Initial human memory block content
        persona_block: Initial persona memory block content

    Returns:
        Created agent with id, name, and configuration
    """
    body = {
        "name": name,
        "description": description,
        "model": model,
        "memory_blocks": [
            {"label": "human", "value": human_block},
            {"label": "persona", "value": persona_block},
        ],
    }
    if system:
        body["system"] = system
    return await _request("POST", "/v1/agents/", json=body)


@mcp.tool()
async def delete_agent(agent_id: str) -> dict:
    """Delete a Letta agent.

    Args:
        agent_id: Agent ID to delete (format: agent-<uuid>)

    Returns:
        Success confirmation
    """
    return await _request("DELETE", f"/v1/agents/{agent_id}")


@mcp.tool()
async def send_message(agent_id: str, message: str) -> dict:
    """Send a message to a Letta agent and get its response.

    Args:
        agent_id: Agent ID (format: agent-<uuid>)
        message: Message text to send

    Returns:
        Agent response with messages and usage stats
    """
    body = {"messages": [{"role": "user", "content": message}]}
    return await _request("POST", f"/v1/agents/{agent_id}/messages", json=body)


@mcp.tool()
async def get_messages(agent_id: str, limit: int = 20) -> dict:
    """Get message history for a Letta agent.

    Args:
        agent_id: Agent ID (format: agent-<uuid>)
        limit: Max messages to return (default: 20)

    Returns:
        List of messages in conversation history
    """
    params = {"limit": min(limit, 100), "order": "desc"}
    return await _request("GET", f"/v1/agents/{agent_id}/messages", params=params)


@mcp.tool()
async def get_memory(agent_id: str) -> dict:
    """Get the core memory blocks for a Letta agent.

    Args:
        agent_id: Agent ID (format: agent-<uuid>)

    Returns:
        Agent memory blocks (human, persona, etc.)
    """
    agent = await _request("GET", f"/v1/agents/{agent_id}")
    if isinstance(agent, dict) and "error" in agent:
        return agent
    blocks = agent.get("memory", {}).get("blocks", [])
    return {
        "agent_id": agent_id,
        "blocks": [
            {
                "id": b.get("id"),
                "label": b.get("label"),
                "value": b.get("value"),
            }
            for b in blocks
        ],
    }


@mcp.tool()
async def update_memory(block_id: str, value: str) -> dict:
    """Update a memory block's content.

    Args:
        block_id: Block ID (format: block-<uuid>)
        value: New content for the memory block

    Returns:
        Updated block
    """
    return await _request("PATCH", f"/v1/blocks/{block_id}", json={"value": value})


@mcp.tool()
async def list_models() -> dict:
    """List available LLM models on the Letta server.

    Returns:
        List of model handles that can be used when creating agents
    """
    models = await _request("GET", "/v1/models/")
    if isinstance(models, dict) and "error" in models:
        return models
    return {
        "models": [
            {"handle": m.get("handle"), "model_type": m.get("model_type")}
            for m in models
        ],
        "count": len(models),
    }
