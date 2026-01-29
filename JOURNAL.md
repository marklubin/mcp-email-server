# Development Journal

Short entries documenting each episode of work on this repo.

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
