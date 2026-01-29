# MCP Router Infrastructure

## Overview

Extensible MCP (Model Context Protocol) server that aggregates multiple backends behind a single endpoint. Currently provides email access via ProtonMail Bridge.

## Architecture

```
Claude → CF Worker (OAuth) → Workers VPC → CF Tunnel → MCP Router → [Backends]
              │                   │             │             │
              │                   │             │             └── email → ProtonMail Bridge
              │                   │             └── cloudflared (127.0.0.1:8080)
              │                   └── Binds to tunnel, no public hostname
              └── Uses env.MCP_BACKEND.fetch()
```

**Key points:**
- No public DNS exposure - zero attack surface
- Workers VPC uses Cloudflare's private backbone
- Tunnel traffic encrypted end-to-end via QUIC (port 7844 outbound)
- MCP_SECRET provides authentication layer

## Key Files

- `router/server.py` - Main FastMCP router, mounts backends
- `router/backends/email.py` - Email backend (IMAP/SMTP via ProtonMail Bridge)
- `cloudflare/worker/src/index.ts` - Cloudflare Worker for OAuth proxy (uses VPC binding)
- `cloudflare/tunnel-config.yml.example` - Tunnel configuration template
- `services/mcp-router.service` - systemd service template for MCP router
- `services/cloudflared.service` - systemd service for Cloudflare Tunnel

## Development Journal

See `JOURNAL.md` for a log of work sessions on this repo.

### Journal Protocol

After completing a significant work session, add an entry to `JOURNAL.md`:

```markdown
## YYYY-MM-DD: Brief Title

**Goal:** What you set out to accomplish.

**Changes:**
- Bullet points of what was done

**Result:** Outcome and any notable learnings.

**Files touched:**
- List of key files modified
```

Keep entries concise. Focus on *what* and *why*, not exhaustive details.

---

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
| `scripts/tunnel.sh [status\|logs\|restart]` | Manage Cloudflare Tunnel |
| `scripts/test-email.sh list` | Test list_emails |
| `scripts/test-email.sh search <query>` | Test search_emails |

### Remote Server

- Host: `oxnard` (configured in ~/.ssh/config)
- Services: `mcp-router@mark.service`, `cloudflared.service`
- Working dir: `~/mcp-infrastructure`
- Logs: `journalctl -u mcp-router@mark` or `journalctl -u cloudflared`

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

## Cloudflare Tunnel Setup

### Prerequisites
- cloudflared installed on remote server
- Cloudflare account with Workers VPC access

### Create Tunnel
```bash
ssh oxnard
cloudflared tunnel login        # Opens browser
cloudflared tunnel create mcp-router
# Note the tunnel ID!
```

### Configure Tunnel
```bash
# Copy template to ~/.cloudflared/config.yml
# Edit with your tunnel ID
# No hostname field = no public DNS, only Workers VPC access
```

### Start Tunnel Service
```bash
sudo systemctl enable --now cloudflared
```

### Create Workers VPC Service
In Cloudflare Dashboard:
1. Go to Workers & Pages → Workers VPC
2. Create Service: `mcp-router-vpc`
3. Select tunnel: `mcp-router`
4. Target: `http://127.0.0.1:8080`

Or via CLI:
```bash
npx wrangler vpc create mcp-router-vpc --tunnel-id <TUNNEL_ID> --target http://127.0.0.1:8080
```

### Deploy Worker
```bash
cd cloudflare/worker && npx wrangler deploy
```

## Common Issues

### Email ordering looks wrong
Emails are sorted by actual UTC time. The `local_time` field shows normalized times for easy reading. Different `date` timezones may look out of order but are correct.

### Service won't start
Check logs: `./scripts/logs.sh 100`
Common causes:
- Missing `.env` file
- ProtonMail Bridge not running
- Python dependency issues (run `uv sync` on remote)

### Tunnel not connecting
Check tunnel status: `./scripts/tunnel.sh status`
Check tunnel logs: `./scripts/tunnel.sh logs 100`
Common causes:
- Missing/invalid credentials file
- Incorrect tunnel ID in config
- Network issues (check port 7844 outbound)

### Module not found errors
Clear Python cache on remote:
```bash
ssh oxnard 'cd ~/mcp-infrastructure && rm -rf .venv __pycache__ router/__pycache__ router/backends/__pycache__ && ~/.local/bin/uv sync'
```

## Cloudflare Worker

Located in `cloudflare/worker/`. Handles:
- GitHub OAuth authentication
- Proxies MCP requests to backend via Workers VPC
- Health check endpoint at `/health`

Deploy with: `cd cloudflare/worker && npx wrangler deploy`

## Verification

1. **Tunnel health:**
   ```bash
   ./scripts/tunnel.sh status
   ```

2. **Both services running:**
   ```bash
   ./scripts/status.sh
   ```

3. **Worker health check:**
   ```bash
   curl https://mcp-router-proxy.melubin.workers.dev/health
   ```

4. **Test via Claude:**
   - Test email_list_emails via Claude
