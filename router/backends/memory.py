"""Synix memory backend — proxies to remote synix knowledge server.

Tools are mounted as memory_* (ingest, search, get_context, list_buckets).
The server URL defaults to salinas over Tailscale.
"""

import os

import httpx
from fastmcp import FastMCP

mcp = FastMCP("memory")

SYNIX_URL = os.environ.get("SYNIX_SERVER_URL", "http://100.120.96.128:8200")
TIMEOUT = 30


async def _get(path: str, params: dict | None = None) -> dict | str:
    """GET request to the synix server."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{SYNIX_URL}{path}", params=params, timeout=TIMEOUT
            )
            if resp.status_code != 200:
                return {"error": f"Synix returned {resp.status_code}: {resp.text[:200]}"}
            content_type = resp.headers.get("content-type", "")
            if "json" in content_type:
                return resp.json()
            return resp.text
        except httpx.TimeoutException:
            return {"error": "Synix server timed out"}
        except httpx.ConnectError:
            return {"error": f"Cannot reach synix server at {SYNIX_URL}"}
        except Exception as e:
            return {"error": f"Synix request failed: {e}"}


async def _post(path: str, json_body: dict) -> dict:
    """POST request to the synix server."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{SYNIX_URL}{path}", json=json_body, timeout=TIMEOUT
            )
            if resp.status_code not in (200, 201):
                return {"error": f"Synix returned {resp.status_code}: {resp.text[:200]}"}
            return resp.json()
        except httpx.TimeoutException:
            return {"error": "Synix server timed out"}
        except httpx.ConnectError:
            return {"error": f"Cannot reach synix server at {SYNIX_URL}"}
        except Exception as e:
            return {"error": f"Synix request failed: {e}"}


@mcp.tool()
async def ingest(bucket: str, content: str, filename: str) -> dict:
    """Write content to a bucket on the synix knowledge server.

    Args:
        bucket: Bucket name (e.g. "documents", "sessions", "reports").
        content: Text content to write.
        filename: Filename to create in the bucket.
    """
    return await _post(f"/api/v1/ingest/{bucket}", {
        "content": content,
        "filename": filename,
    })


@mcp.tool()
async def search(query: str, layers: str | None = None, limit: int = 10) -> dict:
    """Search the knowledge base.

    Args:
        query: Search query string.
        layers: Comma-separated layer names to filter (optional).
        limit: Max results (default 10).
    """
    params = {"q": query, "limit": str(limit)}
    if layers:
        params["layers"] = layers
    return await _get("/api/v1/search", params=params)


@mcp.tool()
async def get_context(name: str = "context-doc") -> str | dict:
    """Retrieve a synthesized context document from the knowledge server.

    Args:
        name: Projection name (default "context-doc").
    """
    return await _get(f"/api/v1/flat-file/{name}")


@mcp.tool()
async def list_buckets() -> dict:
    """List available ingestion buckets on the knowledge server."""
    return await _get("/api/v1/buckets")
