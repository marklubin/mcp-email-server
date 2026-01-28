# Adding a New Backend

This template shows how to add a new MCP backend to the router.

## Steps

### 1. Create Backend Directory

```bash
mkdir -p backends/<name>/patches
```

### 2. Add Configuration (if needed)

Create `backends/<name>/config.toml.example` with any configuration templates.

### 3. Add Patches (if needed)

If the backend needs any monkey patches (e.g., SSL workarounds), add them to `backends/<name>/patches/`.

### 4. Register in Router

Edit `router/server.py` to mount your backend:

```python
# --- Your Backend ---
try:
    from your_mcp_package import mcp as your_mcp
    router.mount(your_mcp, prefix="your-prefix")
    print("✓ Your backend mounted at /your-prefix")
except ImportError as e:
    print(f"✗ Your backend not available: {e}")
```

### 5. Add Dependencies

Add any Python packages to `router/requirements.txt`:

```
your-mcp-package>=1.0.0
```

### 6. Add Systemd Service (if needed)

If your backend requires a separate service (like ProtonMail Bridge for email),
add a systemd unit file to `services/`.

### 7. Update setup.sh

Add installation steps for your backend to `setup.sh`.

### 8. Document

Create `backends/<name>/README.md` with:
- Prerequisites
- Setup instructions
- Available tools
- Troubleshooting

## Example: Filesystem Backend

```bash
# 1. Create directory
mkdir -p backends/filesystem

# 2. No config needed for this example

# 3. No patches needed

# 4. Add to router/server.py:
from mcp_filesystem import mcp as fs_mcp
router.mount(fs_mcp, prefix="fs")

# 5. Add to requirements.txt:
mcp-filesystem>=1.0.0

# 6. No additional service needed

# 7. setup.sh already handles Python deps

# 8. Create backends/filesystem/README.md
```

## Proxy Pattern (Alternative)

If the backend runs as a separate process, use the proxy pattern:

```python
from fastmcp.client import Client

async def create_proxy():
    client = Client("backend-server")
    # Connect via stdio, SSE, or other transport
    backend = await client.connect()
    router.mount(backend, prefix="backend")
```
