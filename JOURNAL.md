# Development Journal

Short entries documenting each episode of work on this repo.

---

## 2026-01-29: Add kp3 Backend

**Goal:** Integrate kp3 (passage storage and hybrid search) as an MCP backend.

**Changes:**
- Created `router/backends/kp3.py` with `search` and `put` tools
- Mounted kp3 backend in router (tools appear as `kp3_search`, `kp3_put`)
- Added aiohttp dependency for async HTTP calls to kp3 service
- Created `services/kp3-podman.service` systemd unit for kp3 (podman compose)
- Updated kp3 compose to expose port 8081 (avoiding conflict with MCP router on 8080)
- Added KP3_HOST, KP3_PORT, KP3_AGENT_ID to remote .env

**Result:** kp3 backend is mounted and routable. The kp3 service runs via podman compose on port 8081. Note: OpenAI API calls from container are timing out - needs network/API key investigation.

**Files touched:**
- `router/backends/kp3.py` - new
- `router/server.py` - mount kp3
- `pyproject.toml` - aiohttp dependency
- `services/kp3-podman.service` - new
- Remote: `~/kairix/kp3/compose.standalone.yml` - port 8081
- Remote: `~/mcp-infrastructure/.env` - kp3 env vars

---

## 2026-01-28: Cloudflare Tunnel + Workers VPC Migration

**Goal:** Replace public nginx/SSL endpoint with private Cloudflare Tunnel + Workers VPC.

**Changes:**
- Created `oxnard-mcp` tunnel with 4 HA connections
- Created `mcp-router-vpc` Workers VPC service binding
- Updated worker to use `env.MCP_BACKEND.fetch()` instead of public URL
- Removed nginx proxy config, disabled nginx service
- Configured UFW firewall (SSH only)
- Hardened SSH (disabled root login)
- Added `logs` MCP tool for remote log access

**Result:** Zero public attack surface for MCP traffic. Fully private point-to-point connection via Cloudflare's backbone.

**Files touched:**
- `cloudflare/worker/wrangler.jsonc` - VPC binding
- `cloudflare/worker/src/index.ts` - VPC fetch
- `services/cloudflared.service` - new
- `scripts/tunnel.sh` - new
- `router/server.py` - logs tool

---
