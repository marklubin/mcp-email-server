# MCP Email Server

Minimal MCP server for email via ProtonMail Bridge.

## Architecture

```
Claude → CF Worker (OAuth) → nginx/SSL → MCP Server → ProtonMail Bridge
```

## Quick Start

### 1. Install uv and clone

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <repo-url>
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
uv run mcp-email-server
```

Or with systemd:

```bash
sudo cp services/mcp-router.service /etc/systemd/system/
sudo systemctl enable --now mcp-router
```

## Tools

- `list_emails(mailbox, limit)` - List recent emails
- `get_email(message_id, mailbox)` - Get full email content
- `send_email(to, subject, body)` - Send email
- `health()` - Health check

## Claude Config

```json
{
  "mcpServers": {
    "email": {
      "url": "https://mcp-router-proxy.melubin.workers.dev/mcp",
      "transport": "sse"
    }
  }
}
```

## Environment Variables

```bash
MCP_SECRET=           # API key for auth
ROUTER_HOST=127.0.0.1
ROUTER_PORT=8080
PROTON_BRIDGE_HOST=127.0.0.1
PROTON_BRIDGE_IMAP_PORT=1143
PROTON_BRIDGE_SMTP_PORT=1025
PROTON_BRIDGE_USER=you@proton.me
PROTON_BRIDGE_PASSWORD=bridge-password
```
