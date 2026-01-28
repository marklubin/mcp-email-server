# MCP Router Infrastructure

## Overview

Extensible MCP (Model Context Protocol) server that aggregates multiple backends behind a single endpoint. Currently provides email access via ProtonMail Bridge.

## Architecture

```
Claude → CF Worker (OAuth) → nginx/SSL → MCP Router → [Backends]
                                              │
                                              ├── email → ProtonMail Bridge
                                              └── (future backends)
```

## Key Files

- `router/server.py` - Main FastMCP router, mounts backends
- `router/backends/email.py` - Email backend (IMAP/SMTP via ProtonMail Bridge)
- `cloudflare/worker/src/index.ts` - Cloudflare Worker for OAuth proxy
- `services/mcp-router.service` - systemd service template

## Development Workflow

### Local Changes
All changes should be made locally in this repo, then deployed via git:

```bash
# Make changes locally
# Commit
git add -A && git commit -m "description"

# Deploy to remote
./scripts/deploy.sh
```

### Management Scripts

| Script | Purpose |
|--------|---------|
| `scripts/deploy.sh` | Push and deploy to remote server |
| `scripts/logs.sh [n]` | View last n log lines (default: 50) |
| `scripts/status.sh` | Check service status and health |
| `scripts/test-email.sh list` | Test list_emails |
| `scripts/test-email.sh search <query>` | Test search_emails |

### Remote Server

- Host: `oxnard` (configured in ~/.ssh/config)
- Service: `mcp-router@mark.service`
- Working dir: `~/mcp-infrastructure`
- Logs: `journalctl -u mcp-router@mark`

## Adding New Backends

1. Create `router/backends/newbackend.py`:
```python
from fastmcp import FastMCP

mcp = FastMCP('newbackend')

@mcp.tool()
async def my_tool(arg: str) -> dict:
    """Tool description."""
    return {'result': 'value'}
```

2. Mount in `router/server.py`:
```python
from backends import newbackend
router.mount(newbackend.mcp, prefix='newbackend')
```

3. Deploy: `./scripts/deploy.sh`

Tools will appear as `newbackend_my_tool`.

## Environment Variables

Stored in `.env` on remote server (not in git):

```
MCP_SECRET=              # API key for auth
ROUTER_HOST=127.0.0.1
ROUTER_PORT=8080
PROTON_BRIDGE_HOST=127.0.0.1
PROTON_BRIDGE_IMAP_PORT=1143
PROTON_BRIDGE_SMTP_PORT=1025
PROTON_BRIDGE_USER=user@proton.me
PROTON_BRIDGE_PASSWORD=bridge-password
```

## Email Backend Tools

| Tool | Description |
|------|-------------|
| `email_list_emails(mailbox, limit)` | List recent emails (newest first) |
| `email_search_emails(query, mailbox, limit, search_body)` | Search emails |
| `email_get_email(message_id, mailbox)` | Get full email content |
| `email_send_email(to, subject, body)` | Send email |

All email responses include:
- `date` - Original date string with timezone
- `local_time` - Normalized local time (YYYY-MM-DD HH:MM)

## Common Issues

### Email ordering looks wrong
Emails are sorted by actual UTC time. The `local_time` field shows normalized times for easy reading. Different `date` timezones may look out of order but are correct.

### Service won't start
Check logs: `./scripts/logs.sh 100`
Common causes:
- Missing `.env` file
- ProtonMail Bridge not running
- Python dependency issues (run `uv sync` on remote)

### Module not found errors
Clear Python cache on remote:
```bash
ssh oxnard 'cd ~/mcp-infrastructure && rm -rf .venv __pycache__ router/__pycache__ router/backends/__pycache__ && ~/.local/bin/uv sync'
```

## Cloudflare Worker

Located in `cloudflare/worker/`. Handles:
- GitHub OAuth authentication
- Proxies MCP requests to backend
- Health check endpoint at `/health`

Deploy with: `cd cloudflare/worker && npx wrangler deploy`
