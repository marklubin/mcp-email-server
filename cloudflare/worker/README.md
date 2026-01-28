# Cloudflare Worker

This directory contains the reference implementation for the Cloudflare Worker
that handles OAuth authentication and proxies requests to the MCP router.

## Overview

The worker sits between Claude and your MCP infrastructure:

```
Claude → CF Worker (OAuth) → Tunnel → MCP Router
```

## Setup

The worker is deployed separately using Wrangler. See the Cloudflare Workers
documentation for deployment instructions.

## Key Features

- OAuth 2.0 authentication flow
- API key validation (X-MCP-Secret header)
- Request proxying to origin via tunnel
- CORS handling for web clients

## Configuration

The worker expects these secrets (set via `wrangler secret put`):

- `MCP_SECRET` - Shared secret for authenticating with the router
- `OAUTH_CLIENT_ID` - OAuth client ID (if using OAuth)
- `OAUTH_CLIENT_SECRET` - OAuth client secret (if using OAuth)

## Files

Place your worker source code here:

- `worker.js` or `worker.ts` - Main worker code
- `wrangler.toml` - Wrangler configuration

## Example wrangler.toml

```toml
name = "mcp-router-worker"
main = "worker.js"
compatibility_date = "2024-01-01"

[vars]
ROUTER_URL = "https://mcp.yourdomain.com"

# Routes
routes = [
  { pattern = "mcp-api.yourdomain.com/*", zone_name = "yourdomain.com" }
]
```
