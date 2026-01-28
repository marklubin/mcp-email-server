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

    host = os.environ.get("ROUTER_HOST", "127.0.0.1")
    port = int(os.environ.get("ROUTER_PORT", "8080"))

    print(f"Starting MCP Router (email) on {host}:{port}")
    # Use the ASGI app directly with uvicorn for host/port control
    app = email_mcp.streamable_http_app()
    uvicorn.run(app, host=host, port=port)
