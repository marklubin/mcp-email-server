"""Agent Memory backend — proxies all synix MCP tools from Salinas.

Uses FastMCP.as_proxy to forward all 20 synix tools (search, build,
artifacts, lineage, etc.) from the agent-mesh MCP HTTP server.
"""
import os

from fastmcp import FastMCP

MCP_URL = os.environ.get('AGENT_MESH_MCP_URL', 'http://salinas:8200/mcp')

mcp = FastMCP.as_proxy(MCP_URL)
