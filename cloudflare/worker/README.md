# Oxnard Remote MCP Worker

This Worker exposes the private Oxnard MCP router at:

```text
https://mcp-router-proxy.melubin.workers.dev/mcp
```

It authenticates MCP clients with GitHub OAuth and proxies authorized requests
to the router through the `MCP_BACKEND` Workers VPC binding. The router is not
publicly addressable.

## Client connection

The endpoint uses Streamable HTTP. In ChatGPT developer mode, add the URL as a
custom connector and complete the GitHub OAuth flow. The allowlist in
`src/github-handler.ts` controls who can finish authorization.

The Worker publishes:

- protected resource metadata at
  `/.well-known/oauth-protected-resource/mcp`
- authorization server metadata at
  `/.well-known/oauth-authorization-server`
- dynamic client registration at `/register`
- authorization at `/authorize`
- token exchange at `/token`

Refresh the ChatGPT connector after a deployment so ChatGPT reads the latest
OAuth and tool metadata.

## Configuration

`wrangler.jsonc` binds:

- `MCP_BACKEND`, the VPC service connected to the Oxnard Cloudflare Tunnel
- `OAUTH_KV`, the namespace used by the OAuth provider

Set these Worker secrets with `wrangler secret put`:

- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `COOKIE_ENCRYPTION_KEY`
- `MCP_SECRET`

The GitHub OAuth app callback URL is:

```text
https://mcp-router-proxy.melubin.workers.dev/callback
```

## Validation and deployment

```bash
npm install
npm run type-check
npx wrangler deploy --dry-run
npx wrangler deploy
```

After deployment, check the health endpoint and OAuth discovery documents:

```bash
curl -fsS https://mcp-router-proxy.melubin.workers.dev/health
curl -fsS https://mcp-router-proxy.melubin.workers.dev/.well-known/oauth-protected-resource/mcp
curl -fsS https://mcp-router-proxy.melubin.workers.dev/.well-known/oauth-authorization-server
```

The implementation follows the current OpenAI guidance for
[remote MCP servers](https://developers.openai.com/plugins/build/mcp-server),
[OAuth](https://developers.openai.com/plugins/build/auth), and
[connecting from ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt).
