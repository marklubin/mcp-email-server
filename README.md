# MCP Router

Extensible MCP server that aggregates multiple backends behind a single endpoint.

## Architecture

```
Remote MCP client → CF Worker (OAuth) → Workers VPC → CF Tunnel → MCP Router → [Backends]
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

## Google Tasks backend (`gtasks_*`)

Mark's interview-preparation board lives in Google Tasks: one list per lane (Coding,
System design, AI systems, Behavioral and projects), one task per curriculum unit titled
`<ID> - <title>`, one subtask per depth. The backend derives the board and applies
bounded changes; it never creates or deletes lists and never deletes tasks.

Tools: `gtasks_lists`, `gtasks_board`, `gtasks_tasks` (list, get, add, complete, reopen,
note, move, update), `gtasks_seed` (idempotent curriculum load, dry run by default).

Credentials: a long-lived OAuth refresh token for Mark's Google account. Produce it once
with `python3 scripts/gtasks_auth.py <client_secret.json>` (Desktop-app OAuth client
from a Google Cloud project with the Tasks API enabled and the app **published**, not in
Testing mode, or the token expires weekly). Append the three lines it writes to `.env`
and restart the router. Optional `GOOGLE_TASKS_LANES` overrides list titles.

## Deployment

The canonical deployed instance runs on **oxnard** as the systemd unit `mcp-router@mark.service`.

**Steady-state flow:**

1. Edit code locally on a dev machine (clone of this repo).
2. Commit and push to `origin/master`.
3. From the local clone, run `./scripts/deploy.sh` — pushes (if needed), SSHes to the remote, runs `git pull`, and restarts the service.
4. If dependencies changed (`pyproject.toml` / `uv.lock`), deploy.sh's `uv sync` keeps the venv in sync. If it doesn't, restart will still pick up the new deps on next `uv run`.

**Do not edit code directly on oxnard.** The repo is the source of truth — changes made only on the box will drift and get lost. If you must hotpatch in an emergency, copy the fix back into the repo and commit it before doing anything else.

Override the deploy target with `MCP_REMOTE=hostname ./scripts/deploy.sh`.

## Backends

Each backend lives in `router/backends/` and exposes tools under its mount prefix.

| Backend | Prefix | Purpose |
|---------|--------|---------|
| `email` | `email_` | ProtonMail Bridge — list/search/get/send |
| `web` | `web_` | Web search + contents via Exa |
| `finance` | `finance_` | Read-only local finance data service |
| `gtasks` | `gtasks_` | Google Tasks preparation board |
| `cartesia` | `cartesia_` | Text-to-speech |

Also available but not currently mounted in `server.py`:

- `browser.py` — Playwright-over-CDP MCP tools; its internal HTTP route remains enabled.
- `memory.py` — agent memory tools.
- `todoist.py` — Todoist tasks/projects.
- `notifications.py` — notification MCP tools; its internal HTTP routes remain enabled.
- `discord.py` — Discord MCP tools; its internal HTTP routes remain enabled.
- `twitter.py` — Twitter read tools via twitterapi.io.
- `unified_memory.py` — proxies all synix MCP tools from the agent-mesh server on salinas. Wire it up by importing + mounting if you want to replace or augment `memory`.

### Email Backend (prefix: `email_`)

| Tool | Description |
|------|-------------|
| `email_list_emails(mailbox, limit)` | List recent emails |
| `email_search_emails(query, mailbox, limit, search_body)` | Search by subject, sender, or body |
| `email_get_email(message_id, mailbox)` | Get full email content |
| `email_send_email(to, subject, body)` | Send email |

### Web Backend (prefix: `web_`)

Uses [Exa](https://exa.ai) under the hood. Free tier: 1,000 searches/month.

| Tool | Description |
|------|-------------|
| `web_search(query, num_results, search_type, include_text, include_domains, exclude_domains)` | Web search; optional inline text excerpts |
| `web_get_contents(urls, max_chars)` | Fetch clean text for one or more URLs |

### Finance Backend (prefix: `finance_`)

This is a read-only gateway to a separate finance data service on the same host.
Plaid credentials, sync operations, and account administration stay inside that
service and are never exposed through MCP.

| Tool | Description |
|------|-------------|
| `finance_summary(days)` | Bounded financial summary and freshness timestamp |
| `finance_accounts(include_inactive, limit)` | Normalized account list (max 100) |
| `finance_transactions(start_date, end_date, account_id, limit)` | Transactions for at most 366 days (max 500) |
| `finance_changes(cursor, limit)` | Incremental transaction changes (max 500) |
| `finance_sync_status()` | Connection health and data freshness |

For detailed signatures of other backends, read the source — each tool's docstring is its contract.

### Router

| Tool | Description |
|------|-------------|
| `health()` | Health check with backend status |
| `logs(lines, service)` | Tail service logs (`mcp-router` or `cloudflared`) |

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

Also append the prefix to the `health()` backend list.

3. If the backend needs secrets, add them to `.env.example` and `.env`.

4. Commit and run `./scripts/deploy.sh`. Tools appear as `yourbackend_your_tool`.

## Remote MCP endpoint

Use `https://mcp-router-proxy.melubin.workers.dev/mcp` as a Streamable HTTP
MCP endpoint. The Cloudflare Worker handles OAuth, then streams MCP traffic to
the private router through Workers VPC.

In ChatGPT developer mode, add the URL as a custom connector and complete the
GitHub OAuth flow. Only the GitHub users listed in
`cloudflare/worker/src/github-handler.ts` can connect. Refresh the connector
after deploying router tool changes or Worker OAuth changes.

## Environment Variables

See `.env.example` for the full list. Required for core operation:

```bash
MCP_SECRET=              # API key for auth from CF Worker
ROUTER_HOST=127.0.0.1
ROUTER_PORT=8080
PROTON_BRIDGE_HOST=127.0.0.1
PROTON_BRIDGE_IMAP_PORT=1143
PROTON_BRIDGE_SMTP_PORT=1025
PROTON_BRIDGE_USER=you@proton.me
PROTON_BRIDGE_PASSWORD=bridge-password
```

Integration-specific keys (only required when the corresponding MCP or HTTP integration is enabled):

```bash
EXA_API_KEY=             # web search
TODOIST_API_TOKEN=       # todoist
TWITTERAPI_IO_KEY=       # twitter
DISCORD_TOKEN=           # discord
FINANCE_SERVICE_URL=     # finance service loopback URL (default http://127.0.0.1:8090)
FINANCE_SERVICE_TOKEN=   # bearer token shared with finance-data-service
MESH_SERVER_URL=         # memory
MESH_TOKEN=              # memory
AGENT_MESH_MCP_URL=      # unified_memory (if mounted)
```

## Project Structure

```
mcp-infrastructure/
├── router/
│   ├── server.py                   # Main router, mounts backends
│   └── backends/
│       ├── __init__.py
│       ├── email.py                # ProtonMail
│       ├── browser.py              # Playwright over CDP
│       ├── todoist.py              # Todoist
│       ├── notifications.py        # Push/inbox
│       ├── discord.py              # Discord user-token
│       ├── memory.py               # Agent memory
│       ├── twitter.py              # Twitter (twitterapi.io)
│       ├── web.py                  # Web search (Exa)
│       └── unified_memory.py       # Synix proxy (not mounted by default)
├── services/
│   ├── mcp-router.service          # systemd template for router
│   ├── cloudflared.service         # systemd service for tunnel
│   ├── headless-brave.service      # headless browser
│   └── protonmail-bridge.service   # ProtonMail Bridge
├── cloudflare/
│   ├── tunnel-config.yml.example   # Tunnel config template
│   └── worker/                     # CF Worker for OAuth + VPC proxy
├── scripts/
│   ├── deploy.sh                   # Push and deploy to remote
│   ├── logs.sh                     # View service logs
│   ├── status.sh                   # Check service status
│   ├── tunnel.sh                   # Manage Cloudflare Tunnel
│   ├── test-email.sh               # Test email tools
│   ├── test.sh                     # Run tests
│   ├── browser-connect.sh          # Connect to CDP browser
│   ├── run-headless-brave.sh       # Launch headless Brave
│   ├── run-headless-firefox.sh     # Launch headless Firefox + VNC
│   └── notify-check.sh             # Notification health check
├── setup.sh                        # One-shot setup script
├── pyproject.toml                  # Python dependencies
└── .env.example                    # Config template
```

## Management Scripts

| Script | Purpose |
|--------|---------|
| `scripts/deploy.sh` | Push and deploy to remote (respects `MCP_REMOTE` env var) |
| `scripts/logs.sh [n]` | View last n log lines |
| `scripts/status.sh` | Check service status |
| `scripts/tunnel.sh [status\|logs\|restart]` | Manage tunnel |
| `scripts/test-email.sh` | Test email tools |
| `scripts/test.sh` | Run tests |
| `scripts/browser-connect.sh` | Connect to the CDP-exposed browser |
| `scripts/run-headless-brave.sh` | Launch headless Brave with CDP |
| `scripts/run-headless-firefox.sh` | Launch headless Firefox + VNC on :99 |
| `scripts/notify-check.sh` | Notification backend health check |
