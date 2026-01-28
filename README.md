# MCP Router

Extensible MCP server that aggregates multiple backends behind a single endpoint.

## Architecture

```
Claude → CF Worker (OAuth) → Workers VPC → CF Tunnel → MCP Router → [Backends]
                                   │                        ↓
                            (private backbone)         email backend → ProtonMail Bridge
                                                       (add more backends here)
```

**Security:** No public DNS exposure. Traffic flows through Cloudflare's private network via Workers VPC binding to a Cloudflare Tunnel with no public hostname.

## Quick Start

### 1. Clone and install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/marklubin/mcp-email-server.git mcp-infrastructure
cd mcp-infrastructure
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. ProtonMail Bridge (one-time)

```bash
protonmail-bridge --cli
# > login
# > info  (note the bridge password)
```

### 4. Run

```bash
uv run python router/server.py
```

Or with systemd:

```bash
./setup.sh
sudo systemctl enable --now mcp-router@$USER
```

### 5. Set up Cloudflare Tunnel (for private backend)

```bash
cloudflared tunnel login
cloudflared tunnel create mcp-router
# Configure ~/.cloudflared/config.yml (see cloudflare/tunnel-config.yml.example)
sudo systemctl enable --now cloudflared
```

### 6. Create Workers VPC Service

In Cloudflare Dashboard → Workers & Pages → Workers VPC:
- Create service: `mcp-router-vpc`
- Select tunnel: `mcp-router`
- Target: `http://127.0.0.1:8080`

### 7. Deploy Worker

```bash
cd cloudflare/worker && npx wrangler deploy
```

## Tools

### Email Backend (prefix: `email_`)

| Tool | Description |
|------|-------------|
| `email_list_emails(mailbox, limit)` | List recent emails |
| `email_search_emails(query, mailbox, limit, search_body)` | Search by subject, sender, or body |
| `email_get_email(message_id, mailbox)` | Get full email content |
| `email_send_email(to, subject, body)` | Send email |

### Router

| Tool | Description |
|------|-------------|
| `health()` | Health check with backend status |

## Adding New Backends

1. Create `router/backends/yourbackend.py`:

```python
from fastmcp import FastMCP

mcp = FastMCP('yourbackend')

@mcp.tool()
async def your_tool(arg: str) -> dict:
    """Your tool description."""
    return {'result': 'value'}
```

2. Mount in `router/server.py`:

```python
from backends import yourbackend
router.mount(yourbackend.mcp, prefix='yourbackend')
```

3. Restart the service. Tools appear as `yourbackend_your_tool`.

## Claude Config

```json
{
  "mcpServers": {
    "router": {
      "url": "https://mcp-router-proxy.melubin.workers.dev/mcp",
      "transport": "sse"
    }
  }
}
```

## Environment Variables

```bash
MCP_SECRET=              # API key for auth
ROUTER_HOST=127.0.0.1
ROUTER_PORT=8080
PROTON_BRIDGE_HOST=127.0.0.1
PROTON_BRIDGE_IMAP_PORT=1143
PROTON_BRIDGE_SMTP_PORT=1025
PROTON_BRIDGE_USER=you@proton.me
PROTON_BRIDGE_PASSWORD=bridge-password
```

## Project Structure

```
mcp-infrastructure/
├── router/
│   ├── server.py           # Main router, mounts backends
│   └── backends/
│       ├── __init__.py
│       └── email.py        # Email backend (ProtonMail)
├── services/
│   ├── mcp-router.service  # systemd template for router
│   └── cloudflared.service # systemd service for tunnel
├── cloudflare/
│   ├── tunnel-config.yml.example  # Tunnel config template
│   └── worker/             # CF Worker for OAuth + VPC proxy
├── scripts/
│   ├── deploy.sh           # Push and deploy to remote
│   ├── logs.sh             # View service logs
│   ├── status.sh           # Check service status
│   ├── tunnel.sh           # Manage Cloudflare Tunnel
│   └── test-email.sh       # Test email tools
├── setup.sh                # One-shot setup script
├── pyproject.toml          # Python dependencies
└── .env.example            # Config template
```

## Management Scripts

| Script | Purpose |
|--------|---------|
| `scripts/deploy.sh` | Push and deploy to remote |
| `scripts/logs.sh [n]` | View last n log lines |
| `scripts/status.sh` | Check service status |
| `scripts/tunnel.sh [status\|logs\|restart]` | Manage tunnel |
| `scripts/test-email.sh` | Test email tools |
