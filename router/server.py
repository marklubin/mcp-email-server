#!/usr/bin/env python3
"""
MCP Router - Serves email backend with SSL patch for ProtonMail Bridge.

For now, this directly runs the email server. Future versions may aggregate
multiple backends using FastMCP's proxy/mount features when APIs stabilize.
"""

import os
import sys
from pathlib import Path

# Add project root to path for backend imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

# Load environment variables
load_dotenv(project_root / ".env")

# --- SSL Patch ---
# Apply SSL patch before importing email server (for ProtonMail Bridge self-signed cert)
from backends.email.patches.ssl_bypass import apply_patch
apply_patch()
print("✓ SSL bypass patch applied")

# --- Email Backend ---
from mcp_email_server.app import mcp as email_mcp

# Add a health check tool to the email server
@email_mcp.tool()
def router_health() -> dict:
    """Health check endpoint for the MCP router."""
    return {
        "status": "ok",
        "ssl_patch": "applied",
        "backends": ["email"]
    }

print("✓ Email backend loaded")

# --- Future: Add more backends ---
# When fastmcp mount() API stabilizes, we can aggregate multiple backends here.
# For now, additional backends would need separate server processes.


if __name__ == "__main__":
    import uvicorn
    import json

    host = os.environ.get("ROUTER_HOST", "127.0.0.1")
    port = int(os.environ.get("ROUTER_PORT", "8080"))
    mcp_secret = os.environ.get("MCP_SECRET")

    if not mcp_secret:
        print("⚠ WARNING: MCP_SECRET not set - API key validation disabled!")
    else:
        print(f"✓ API key validation enabled")

    print(f"Starting MCP Router (email) on {host}:{port}")

    # Get the base app
    base_app = email_mcp.streamable_http_app()

    class AuthMiddleware:
        """Validate X-MCP-Secret header and rewrite Host for tunnel compatibility."""

        def __init__(self, app, secret: str | None):
            self.app = app
            self.secret = secret

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                headers_dict = {k: v for k, v in scope["headers"]}

                # Check API key if configured
                if self.secret:
                    provided_secret = headers_dict.get(b"x-mcp-secret", b"").decode()
                    if provided_secret != self.secret:
                        response = {
                            "jsonrpc": "2.0",
                            "id": "auth-error",
                            "error": {"code": -32001, "message": "Unauthorized"}
                        }
                        body = json.dumps(response).encode()
                        await send({
                            "type": "http.response.start",
                            "status": 401,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                            ],
                        })
                        await send({
                            "type": "http.response.body",
                            "body": body,
                        })
                        return

                # Rewrite host header to localhost for MCP library validation
                headers = [(k, v) for k, v in scope["headers"] if k != b"host"]
                headers.append((b"host", b"127.0.0.1:8080"))
                scope = dict(scope, headers=headers)

            await self.app(scope, receive, send)

    app = AuthMiddleware(base_app, mcp_secret)

    uvicorn.run(app, host=host, port=port)
