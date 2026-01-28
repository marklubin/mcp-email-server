# Email Backend

This backend provides email functionality via ProtonMail Bridge using the zerolib `mcp-email-server`.

## Prerequisites

- ProtonMail account with Bridge access (requires paid plan)
- ProtonMail Bridge installed on the VPS

## Setup

### 1. Install ProtonMail Bridge

The `setup.sh` script handles this, but for manual installation:

```bash
# Download latest bridge
wget https://proton.me/download/bridge/protonmail-bridge_3.0.21-1_amd64.deb
sudo dpkg -i protonmail-bridge_*.deb
sudo apt-get install -f  # Fix any missing dependencies
```

### 2. Login to Bridge (One-time)

```bash
protonmail-bridge --cli
```

In the CLI:
```
>>> login
# Follow prompts for email and password
# Complete 2FA if enabled

>>> info
# Note the bridge password (different from your Proton password!)
```

### 3. Configure the Backend

```bash
cp config.toml.example config.toml
```

Edit `config.toml` with:
- Your Proton email address as username
- The **bridge-generated password** (from `info` command)

### 4. Update .env

Add bridge credentials to the root `.env` file:

```bash
PROTON_BRIDGE_USER=your-email@proton.me
PROTON_BRIDGE_PASSWORD=bridge-generated-password
```

## SSL Certificate

ProtonMail Bridge uses a self-signed certificate for local connections.
The `patches/ssl_bypass.py` module handles this automatically by disabling
certificate verification for localhost connections.

## Available Tools

Once mounted, the email backend provides:

- `email_search` - Search emails with queries
- `email_read` - Read email content by ID
- `email_send` - Send new emails
- `email_reply` - Reply to existing emails
- `email_list_folders` - List available folders

## Troubleshooting

### "Connection refused" errors

Ensure ProtonMail Bridge is running:
```bash
systemctl status protonmail-bridge
```

### "Authentication failed" errors

1. Verify you're using the bridge password, not your Proton password
2. Re-run `protonmail-bridge --cli` and check `info` for current credentials

### SSL errors

The SSL bypass should handle this. If you still see errors:
1. Ensure `ssl_bypass.apply_patch()` is called before importing the email server
2. Check that the patch is being applied in `router/server.py`
