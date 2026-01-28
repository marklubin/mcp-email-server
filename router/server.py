#!/usr/bin/env python3
"""
MCP Router - FastMCP aggregator for multiple backends.

This router mounts multiple MCP backends under prefixed namespaces,
providing a single endpoint for Claude to access all tools.
"""

import os
import sys
from pathlib import Path

# Add project root to path for backend imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from fastmcp import FastMCP

# Load environment variables
load_dotenv(project_root / ".env")

# Create the main router
router = FastMCP("MCP Router")

# --- Email Backend ---
# Apply SSL patch before importing email server (for ProtonMail Bridge self-signed cert)
from backends.email.patches.ssl_bypass import apply_patch
apply_patch()

# Import and mount email backend
try:
    from mcp_email_server import mcp as email_mcp
    router.mount(email_mcp, prefix="email")
    print("✓ Email backend mounted at /email")
except ImportError as e:
    print(f"✗ Email backend not available: {e}")

# --- Health Check ---
@router.tool()
def health() -> dict:
    """Health check endpoint for the MCP router."""
    return {
        "status": "ok",
        "backends": {
            "email": "mounted"
        }
    }

# --- Add New Backends Below ---
# Example:
# from some_other_mcp import mcp as other_mcp
# router.mount(other_mcp, prefix="other")


if __name__ == "__main__":
    host = os.environ.get("ROUTER_HOST", "127.0.0.1")
    port = int(os.environ.get("ROUTER_PORT", "8080"))

    print(f"Starting MCP Router on {host}:{port}")
    router.run(transport="streamable-http", host=host, port=port)
