# MCP Router

Extensible MCP server that aggregates multiple backends behind a single endpoint.

## Architecture

```
Claude → CF Worker (OAuth) → nginx/SSL → MCP Router → [Backends]
                                              ↓
                                         email backend → ProtonMail Bridge
                                         (add more backends here)
```

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
│   └── mcp-router.service  # systemd template
├── cloudflare/
│   └── worker/             # CF Worker for OAuth
├── setup.sh                # One-shot setup script
├── pyproject.toml          # Python dependencies
└── .env.example            # Config template
```
