#!/usr/bin/env python3
"""MCP Router - Aggregates multiple MCP backends behind a single endpoint.

To add a new backend:
1. Create router/backends/yourbackend.py with a FastMCP instance named 'mcp'
2. Import and mount it below
"""

import os
import json
import subprocess

from fastmcp import FastMCP

# Import backends
from backends import email
from backends import kp3
from backends import browser

MCP_SECRET = os.environ.get('MCP_SECRET', '')

# Create router and mount backends
router = FastMCP('mcp-router')
router.mount(email.mcp, prefix='email')
router.mount(kp3.mcp, prefix='kp3')
router.mount(browser.mcp, prefix='browser')


@router.tool()
def health() -> dict:
    """Health check for the MCP router."""
    return {'status': 'ok', 'backends': ['email', 'kp3', 'browser']}


@router.tool()
def logs(lines: int = 50, service: str = "mcp-router") -> dict:
    """Tail the last N lines of service logs.

    Args:
        lines: Number of log lines to return (default: 50, max: 500)
        service: Service to get logs for - "mcp-router" or "cloudflared" (default: mcp-router)
    """
    lines = min(max(1, lines), 500)  # Clamp between 1-500

    service_map = {
        "mcp-router": "mcp-router@mark",
        "cloudflared": "cloudflared",
    }

    unit = service_map.get(service)
    if not unit:
        return {'error': f'Unknown service: {service}. Valid: {list(service_map.keys())}'}

    try:
        result = subprocess.run(
            ['journalctl', '-u', unit, '-n', str(lines), '--no-pager'],
            capture_output=True,
            text=True,
            timeout=10
        )
        return {
            'service': service,
            'lines': lines,
            'logs': result.stdout,
            'stderr': result.stderr if result.stderr else None
        }
    except subprocess.TimeoutExpired:
        return {'error': 'Timeout reading logs'}
    except Exception as e:
        return {'error': str(e)}


class AuthMiddleware:
    """Check X-MCP-Secret header."""

    def __init__(self, app, secret):
        self.app = app
        self.secret = secret

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and self.secret:
            headers = {k.decode(): v.decode() for k, v in scope.get('headers', [])}
            if headers.get('x-mcp-secret') != self.secret:
                response = json.dumps({
                    'jsonrpc': '2.0',
                    'id': 'auth-error',
                    'error': {'code': -32001, 'message': 'Unauthorized'}
                }).encode()
                await send({
                    'type': 'http.response.start',
                    'status': 401,
                    'headers': [(b'content-type', b'application/json')]
                })
                await send({'type': 'http.response.body', 'body': response})
                return
        await self.app(scope, receive, send)


def main():
    import uvicorn

    host = os.environ.get('ROUTER_HOST', '127.0.0.1')
    port = int(os.environ.get('ROUTER_PORT', '8080'))

    app = router.http_app()
    if MCP_SECRET:
        print('API key auth enabled')
        app = AuthMiddleware(app, MCP_SECRET)

    print(f'MCP Router starting on {host}:{port}')
    print('Mounted backends: email, kp3, browser')
    uvicorn.run(app, host=host, port=port)


if __name__ == '__main__':
    main()
