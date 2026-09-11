# Development Journal

Short entries documenting each episode of work on this repo.

---

## 2026-09-11: Reduce the public MCP tool catalog

**Goal:** Stop publishing Discord, Todoist, Twitter, and notification tools through the aggregate MCP server.

**Changes:**
- Unmounted the `discord_`, `todoist_`, `twitter_`, and `notify_` tool groups from the MCP router.
- Kept the Discord and notification HTTP routes enabled for existing internal callers.
- Updated the health response, metadata contract, regression tests, and backend documentation.

**Validation:**
- The local MCP catalog contains 33 tools and none use a disabled prefix.
- Full non-browser test suite: 164 passed.

**Result:** Backend implementations and credentials remain untouched and can be remounted later, but MCP clients no longer discover these four tool groups.

## 2026-09-11: Modernize the ChatGPT MCP connector

**Goal:** Bring the public Oxnard MCP endpoint up to the current ChatGPT custom-connector and OAuth discovery contract.

**Changes:**
- Added current OAuth protected-resource discovery, CIMD/DCR support, S256-only PKCE, and streaming proxy responses in the Cloudflare Worker.
- Added stable titles, annotations, and output schemas for all router tools.
- Updated Worker dependencies, configuration, documentation, and connector troubleshooting guidance.

**Validation:**
- Router test suite: 162 passed, excluding the live browser suite whose local CDP endpoint was unavailable.
- Worker typecheck, dependency audit, and Wrangler dry run passed; dependency audit reported zero vulnerabilities.
- Deployed Cloudflare Worker version `dea673fb-16f0-4b93-b1b3-1bf01ed65916`.
- Live checks returned 200 for health and both OAuth metadata resources; unauthenticated `/mcp` returned 401 with the required `resource_metadata` challenge.

**Result:** The public endpoint at `https://mcp-router-proxy.melubin.workers.dev/mcp` is deployed and ready to reconnect in ChatGPT.

**Files touched:**
- `cloudflare/worker/`, `router/server.py`, `router/tool_metadata.py`, `tests/test_tool_metadata.py`, `README.md`

## 2026-09-03: Google Tasks preparation-board backend

**Goal:** Give every agent runtime one non-brittle path to Mark's interview-prep board after the custom job-search desk was retired.

**Changes:**
- New `router/backends/gtasks.py` (FastMCP `gtasks`): `lists`, `board`, `tasks`, `seed`; refresh-token auth with in-process access-token cache; typed errors (`not_configured`, `auth_failed`, `not_found`, `ambiguous`, `timeout`, `upstream_error`); no list creation, no deletes.
- Mounted as `gtasks_*` in `router/server.py`; health lists it.
- `scripts/gtasks_auth.py`: one-time PKCE loopback flow that writes the three env lines (never prints the token).
- `.env.example` and README documented; 17 backend tests.

**Result:** Authorized against Mark's personal Google account the same night and seeded with 44 units. Two follow-ups landed during the seed: quota errors (403 Quota Exceeded after ~20 units) now back off and retry, and the seed reorders lanes to curriculum order and depth subtasks to depth order because Google inserts new tasks at the top. Refresh tokens only last if the OAuth app is published rather than in Testing. Lane list titles come from `GOOGLE_TASKS_LANES`.

**Files touched:**
- `router/backends/gtasks.py`, `router/server.py`, `scripts/gtasks_auth.py`, `tests/test_gtasks_backend.py`, `.env.example`, `README.md`

## 2026-02-01: Add Todoist Backend

**Goal:** Add Todoist task management backend with a streamlined 2-tool API.

**Changes:**
- Created `router/backends/todoist.py` with `tasks` and `projects` tools
- Mounted todoist backend in router (tools appear as `todoist_tasks`, `todoist_projects`)
- Created `tests/test_todoist_backend.py` with 37 unit tests

**Design:**
- 2 tools only to minimize context pollution
- `tasks` tool: list/get/create/update/delete/complete/reopen with inline comments
- `projects` tool: list/get/create/update/delete + section management
- List returns deduped project/section metadata alongside tasks
- Reminders supported via Sync API (Premium only)

**Result:** All 37 tests pass. Ready for deployment after adding `TODOIST_API_TOKEN` to remote .env.

**Files touched:**
- `router/backends/todoist.py` - new (~300 lines)
- `router/server.py` - import and mount todoist backend
- `tests/test_todoist_backend.py` - new (37 tests)

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

**Result:** kp3 backend is mounted and routable. The kp3 service runs via podman compose on port 8081.

**UFW fix:** Container network traffic was being blocked by UFW's default `deny (routed)` policy. Added rules:
```bash
sudo ufw route allow from 10.89.0.0/24
sudo ufw route allow to 10.89.0.0/24
sudo ufw allow in on podman1 from 10.89.0.0/24 to 10.89.0.1 port 53
```

**Files touched:**
- `router/backends/kp3.py` - new
- `router/server.py` - mount kp3
- `pyproject.toml` - aiohttp dependency
- `services/kp3-podman.service` - new
- Remote: `~/kairix/kp3/compose.standalone.yml` - port 8081
- Remote: `~/mcp-infrastructure/.env` - kp3 env vars
- Remote: UFW rules for podman network

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
