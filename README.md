# MCP Router Infrastructure

A replicable, extensible MCP router that aggregates multiple MCP backends behind a single endpoint.

## Architecture

```
Claude → CF Worker (OAuth) → Tunnel → FastMCP Router → [backends]
                                           ↓
                              ┌────────────┴────────────┐
                              ↓                         ↓
                         zerolib email            (future servers)
                              ↓
                      ProtonMail Bridge
```

## Quick Start

### 1. Clone and Setup

```bash
git clone <repo-url>
cd mcp-infrastructure
./setup.sh
```

### 2. Configure Secrets

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. ProtonMail Bridge Login (One-time)

```bash
protonmail-bridge --cli
# > login
# > info  (note the bridge password)
```

### 4. Configure Email Backend

```bash
cp backends/email/config.toml.example backends/email/config.toml
# Edit with bridge credentials from step 3
```

### 5. Start Services

```bash
sudo systemctl enable --now protonmail-bridge mcp-router
```

## Adding New Backends

See `backends/_template/README.md` for instructions on adding new MCP backends.

## Directory Structure

```
mcp-infrastructure/
├── README.md                    # This file
├── setup.sh                     # One-shot VPS setup script
├── .env.example                 # Template for secrets
│
├── router/
│   ├── server.py                # FastMCP aggregator
│   └── requirements.txt         # Python deps
│
├── backends/
│   ├── email/
│   │   ├── config.toml.example  # zerolib config template
│   │   ├── patches/
│   │   │   └── ssl_bypass.py    # Monkey patch for SSL validation
│   │   └── README.md            # Email backend setup
│   │
│   └── _template/               # Template for new backends
│       └── README.md
│
├── services/
│   ├── protonmail-bridge.service
│   └── mcp-router.service
│
└── cloudflare/
    ├── worker/                  # CF Worker source (reference)
    └── tunnel-config.yml.example
```

## Verification

1. Check services: `systemctl status protonmail-bridge mcp-router`
2. Health check: `curl -H "X-MCP-Secret: $SECRET" http://127.0.0.1:8080/health`
3. Test with MCP Inspector or Claude

## Troubleshooting

### ProtonMail Bridge Issues

- Ensure bridge is logged in: `protonmail-bridge --cli` then `info`
- Check bridge logs: `journalctl -u protonmail-bridge -f`

### Router Issues

- Check router logs: `journalctl -u mcp-router -f`
- Verify .env is loaded: `systemctl show mcp-router --property=Environment`

### SSL Certificate Errors

The SSL bypass patch handles ProtonMail Bridge's self-signed certificate.
If you see SSL errors, ensure `ssl_bypass.py` is being imported before the email backend.
