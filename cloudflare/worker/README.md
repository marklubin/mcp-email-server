# MCP Proxy Worker

Cloudflare Worker that handles OAuth authentication and proxies MCP requests to the backend.

## Flow

```
Claude → Worker (OAuth) → Tunnel → Router → Email Backend
              ↓
         1. OAuth flow
         2. Bearer token
         3. Proxy with X-MCP-Secret
```

## Endpoints

| Path | Description |
|------|-------------|
| `/.well-known/oauth-authorization-server` | OAuth metadata |
| `/authorize` | OAuth authorization |
| `/token` | OAuth token exchange |
| `/mcp` | MCP protocol (proxied to backend) |
| `/health` | Health check |

## Setup

### 1. Install dependencies

```bash
cd cloudflare/worker
npm install
```

### 2. Configure secrets

```bash
# Set the shared secret (must match backend MCP_SECRET)
wrangler secret put MCP_SECRET
# Enter: 76639e0a42843e4ab9080dbb8014ccbfc63ec0e0e2b1d51f9120a983d72e06b6
```

### 3. Deploy

```bash
npm run deploy
```

### 4. Configure DNS

Point your MCP endpoint domain to the worker in Cloudflare dashboard.

## Development

```bash
npm run dev
```

## Production Notes

The current implementation uses in-memory token storage which doesn't persist
across worker instances. For production:

1. Add a KV namespace for token storage:
   ```toml
   # wrangler.toml
   [[kv_namespaces]]
   binding = "SESSIONS"
   id = "your-kv-namespace-id"
   ```

2. Replace the `tokens` Map with KV operations

3. Consider adding:
   - Rate limiting
   - Token revocation
   - Refresh token rotation
   - User consent screen
