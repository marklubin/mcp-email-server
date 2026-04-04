#!/usr/bin/env python3
"""
memfs-sync: Bidirectional sync between local MemFS .md files and Letta server blocks.

Bridges the gap left by Letta Cloud's /v1/git/{agentId}/state.git endpoint,
which doesn't exist on self-hosted deployments.

Usage:
    memfs-sync.py <agent_id> --once          # One-shot sync
    memfs-sync.py <agent_id> --watch         # Watch for file changes
    memfs-sync.py <agent_id> --post-commit   # Run as git post-commit hook

Requires: httpx, pyyaml (available in mcp-infrastructure venv)
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LETTA_BASE_URL = os.environ.get("LETTA_BASE_URL", "http://oxnard:8283")
LETTA_TOKEN = os.environ.get("LETTA_TOKEN", "")
LETTA_HOME = Path(os.environ.get("LETTA_HOME", Path.home() / ".letta"))
MEMORY_FILESYSTEM_LABEL = "memory_filesystem"

# ---------------------------------------------------------------------------
# Frontmatter parsing (no PyYAML dependency for simple key: value pairs)
# ---------------------------------------------------------------------------


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file.

    Returns (metadata_dict, body_text).
    """
    if not content.startswith("---"):
        return {}, content

    # Find closing ---
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content

    fm_text = content[4:end]  # skip opening ---\n
    body = content[end + 4:].lstrip("\n")  # skip closing ---\n and blank separator line(s)

    metadata = {}
    for line in fm_text.strip().splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Type coercion for known fields
        if key == "limit":
            try:
                value = int(value)
            except ValueError:
                pass
        elif key == "read_only":
            value = value.lower() == "true"
        metadata[key] = value

    return metadata, body


def render_frontmatter(metadata: dict, body: str) -> str:
    """Render metadata and body back to a .md file with frontmatter."""
    lines = ["---"]
    # Canonical order
    for key in ("label", "description", "limit", "read_only"):
        if key in metadata:
            val = metadata[key]
            if isinstance(val, bool):
                val = "true" if val else "false"
            lines.append(f"{key}: {val}")
    # Any extra keys
    for key, val in metadata.items():
        if key not in ("label", "description", "limit", "read_only"):
            lines.append(f"{key}: {val}")
    lines.append("---")
    lines.append("")

    # Ensure body ends with newline
    if body and not body.endswith("\n"):
        body += "\n"

    return "\n".join(lines) + body


# ---------------------------------------------------------------------------
# Letta API client
# ---------------------------------------------------------------------------


class LettaClient:
    def __init__(self, base_url: str, token: str = ""):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=30.0,
        )

    def get_agent(self, agent_id: str) -> dict:
        resp = self.client.get(f"/v1/agents/{agent_id}")
        resp.raise_for_status()
        return resp.json()

    def get_blocks(self, agent_id: str) -> list[dict]:
        agent = self.get_agent(agent_id)
        return agent.get("memory", {}).get("blocks", [])

    def patch_block(self, block_id: str, **kwargs) -> dict:
        resp = self.client.patch(f"/v1/blocks/{block_id}", json=kwargs)
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self.client.close()


# ---------------------------------------------------------------------------
# Local file operations
# ---------------------------------------------------------------------------


def get_memory_dir(agent_id: str) -> Path:
    return LETTA_HOME / "agents" / agent_id / "memory"


def get_system_dir(agent_id: str) -> Path:
    return get_memory_dir(agent_id) / "system"


def read_local_files(agent_id: str) -> dict[str, tuple[dict, str]]:
    """Read all .md files from the agent's memory/system/ directory.

    Returns {label: (metadata, body)} for each file.
    """
    system_dir = get_system_dir(agent_id)
    if not system_dir.exists():
        return {}

    files = {}
    for md_file in sorted(system_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        metadata, body = parse_frontmatter(content)
        label = metadata.get("label", md_file.stem)
        files[label] = (metadata, body)

    return files


def write_local_file(agent_id: str, label: str, metadata: dict, body: str):
    """Write a block as a .md file in the agent's memory/system/ directory."""
    system_dir = get_system_dir(agent_id)
    system_dir.mkdir(parents=True, exist_ok=True)

    filename = label.replace("/", "_") + ".md"
    file_path = system_dir / filename
    content = render_frontmatter(metadata, body)
    file_path.write_text(content, encoding="utf-8")
    return file_path


# ---------------------------------------------------------------------------
# Sync state tracking (for bidirectional conflict detection)
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def get_sync_state_path(agent_id: str) -> Path:
    return get_memory_dir(agent_id) / ".memfs-sync-state.json"


def load_sync_state(agent_id: str) -> dict[str, str]:
    """Load last-synced content hashes per label.

    Returns {label: content_hash}.
    """
    path = get_sync_state_path(agent_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_sync_state(agent_id: str, state: dict[str, str]):
    """Save content hashes after successful sync."""
    path = get_sync_state_path(agent_id)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tree rendering (mirrors scanMemoryFilesystem from letta.js)
# ---------------------------------------------------------------------------


def render_memory_tree(agent_id: str) -> str:
    """Render a tree view of the memory directory, matching letta.js format."""
    memory_dir = get_memory_dir(agent_id)
    if not memory_dir.exists():
        return "/memory/\n"

    lines = ["/memory/"]
    _render_tree_recursive(memory_dir, memory_dir, lines, depth=0, parent_is_last=[])
    return "\n".join(lines) + "\n"


def _render_tree_recursive(
    root: Path, current: Path, lines: list[str], depth: int, parent_is_last: list[bool]
):
    try:
        entries = sorted(current.iterdir())
    except PermissionError:
        return

    # Filter hidden files/dirs
    entries = [e for e in entries if not e.name.startswith(".")]

    # Sort: directories first, then alphabetical; "system" dir comes first at depth 0
    dirs = sorted([e for e in entries if e.is_dir()], key=lambda e: (e.name != "system" if depth == 0 else False, e.name))
    files = sorted([e for e in entries if e.is_file()], key=lambda e: e.name)
    sorted_entries = dirs + files

    for i, entry in enumerate(sorted_entries):
        is_last = i == len(sorted_entries) - 1

        # Build prefix
        prefix = ""
        for j, p_last in enumerate(parent_is_last):
            prefix += "    " if p_last else "\u2502   "

        connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
        name = entry.name + "/" if entry.is_dir() else entry.name

        # For files, show description from frontmatter
        annotation = ""
        if entry.is_file() and entry.suffix == ".md":
            try:
                content = entry.read_text(encoding="utf-8")
                metadata, body = parse_frontmatter(content)
                desc = metadata.get("description", "")
                char_count = len(body.strip())
                limit = metadata.get("limit", "")
                if desc:
                    annotation = f"  ({char_count}/{limit} chars) {desc}"
            except Exception:
                pass

        lines.append(f"{prefix}{connector}{name}{annotation}")

        if entry.is_dir():
            _render_tree_recursive(
                root, entry, lines, depth + 1, parent_is_last + [is_last]
            )


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------


def sync(agent_id: str, verbose: bool = True, local_authoritative: bool = False) -> dict:
    """Bidirectional sync between local files and server blocks.

    Args:
        agent_id: Letta agent ID
        verbose: Print detailed output
        local_authoritative: If True, local always wins on conflict (post-commit mode).
            If False, uses sync state to determine direction.

    Returns a summary dict with counts of actions taken.
    """
    client = LettaClient(LETTA_BASE_URL, LETTA_TOKEN)
    stats = {"pushed": 0, "pulled": 0, "tree_updated": False, "conflicts": 0, "errors": []}

    try:
        # Fetch server blocks
        server_blocks = client.get_blocks(agent_id)
        server_by_label = {b["label"]: b for b in server_blocks}

        # Read local files
        local_files = read_local_files(agent_id)

        # Load last-synced state
        sync_state = load_sync_state(agent_id)
        new_state = dict(sync_state)

        # --- Process all labels (union of local + server) ---
        all_labels = set(local_files.keys()) | set(server_by_label.keys())

        for label in sorted(all_labels):
            if label == MEMORY_FILESYSTEM_LABEL:
                continue  # Managed separately

            local_entry = local_files.get(label)
            server_block = server_by_label.get(label)

            local_value = local_entry[1].strip() if local_entry else None
            server_value = (server_block.get("value") or "").strip() if server_block else None

            local_hash = _content_hash(local_value) if local_value is not None else None
            server_hash = _content_hash(server_value) if server_value is not None else None
            last_synced_hash = sync_state.get(label)

            # Both exist and match — in sync
            if local_hash and server_hash and local_hash == server_hash:
                new_state[label] = local_hash
                if verbose:
                    print(f"  [ok] {label}: in sync")
                continue

            # Only on server, not local — pull
            if local_value is None and server_block:
                metadata = {
                    "label": label,
                    "description": server_block.get("description", ""),
                    "limit": server_block.get("limit", 5000),
                }
                if server_block.get("read_only"):
                    metadata["read_only"] = True
                body = server_block.get("value", "")
                path = write_local_file(agent_id, label, metadata, body)
                new_state[label] = server_hash
                stats["pulled"] += 1
                if verbose:
                    print(f"  [pull] {label}: created local file {path.name}")
                continue

            # Only local, not on server — skip (can't create blocks via API easily)
            if server_block is None and local_value is not None:
                if verbose:
                    print(f"  [skip] {label}: exists locally but not on server")
                continue

            # Both exist but differ — need to determine direction
            if local_authoritative:
                # Post-commit: local always wins
                _push_block(client, server_block, local_entry, label, stats, new_state, local_hash, verbose)
            elif last_synced_hash is None:
                # First sync — local wins (initializing state)
                _push_block(client, server_block, local_entry, label, stats, new_state, local_hash, verbose)
            elif local_hash == last_synced_hash and server_hash != last_synced_hash:
                # Local unchanged, server changed — pull
                metadata = local_entry[0].copy()
                metadata["description"] = server_block.get("description", metadata.get("description", ""))
                metadata["limit"] = server_block.get("limit", metadata.get("limit", 5000))
                body = server_block.get("value", "")
                write_local_file(agent_id, label, metadata, body)
                new_state[label] = server_hash
                stats["pulled"] += 1
                if verbose:
                    print(f"  [pull] {label}: server changed, updated local file")
            elif local_hash != last_synced_hash and server_hash == last_synced_hash:
                # Local changed, server unchanged — push
                _push_block(client, server_block, local_entry, label, stats, new_state, local_hash, verbose)
            else:
                # Both changed — conflict
                stats["conflicts"] += 1
                if verbose:
                    print(f"  [CONFLICT] {label}: both local and server changed since last sync")
                    print(f"    Local hash:  {local_hash}")
                    print(f"    Server hash: {server_hash}")
                    print(f"    Last synced: {last_synced_hash}")
                    print(f"    Run with --post-commit to force local, or edit manually")

        # --- Update memory_filesystem block ---
        fs_block = server_by_label.get(MEMORY_FILESYSTEM_LABEL)
        if fs_block:
            tree_text = render_memory_tree(agent_id)
            current_tree = (fs_block.get("value") or "").strip()
            if tree_text.strip() != current_tree:
                try:
                    client.patch_block(fs_block["id"], value=tree_text)
                    stats["tree_updated"] = True
                    if verbose:
                        print(f"  [tree] memory_filesystem block updated")
                except Exception as e:
                    stats["errors"].append(f"tree update: {e}")
                    if verbose:
                        print(f"  [error] memory_filesystem: failed to update - {e}")
            else:
                if verbose:
                    print(f"  [ok] memory_filesystem: tree is current")
        else:
            if verbose:
                print(f"  [info] no memory_filesystem block on server (agent may not use tree view)")

        # Save sync state
        save_sync_state(agent_id, new_state)

    finally:
        client.close()

    return stats


def _push_block(
    client: LettaClient,
    server_block: dict,
    local_entry: tuple[dict, str],
    label: str,
    stats: dict,
    new_state: dict,
    local_hash: str,
    verbose: bool,
):
    """Push a local block value to the server."""
    metadata, body = local_entry
    try:
        patch_data = {"value": body.rstrip("\n")}
        if metadata.get("description") and metadata["description"] != server_block.get("description", ""):
            patch_data["description"] = metadata["description"]
        if metadata.get("limit") and metadata["limit"] != server_block.get("limit"):
            patch_data["limit"] = metadata["limit"]

        client.patch_block(server_block["id"], **patch_data)
        new_state[label] = local_hash
        stats["pushed"] += 1
        if verbose:
            print(f"  [push] {label}: synced to server ({len(body.strip())} chars)")
    except Exception as e:
        stats["errors"].append(f"push {label}: {e}")
        if verbose:
            print(f"  [error] {label}: failed to push - {e}")


# ---------------------------------------------------------------------------
# Watch mode (filesystem polling)
# ---------------------------------------------------------------------------


def watch(agent_id: str, interval: float = 2.0):
    """Watch for file changes and sync on change."""
    system_dir = get_system_dir(agent_id)
    if not system_dir.exists():
        print(f"Error: {system_dir} does not exist")
        sys.exit(1)

    print(f"Watching {system_dir} for changes (interval: {interval}s)...")
    print(f"Press Ctrl+C to stop.\n")

    # Track file mtimes
    last_mtimes = _get_mtimes(system_dir)

    try:
        while True:
            time.sleep(interval)
            current_mtimes = _get_mtimes(system_dir)

            if current_mtimes != last_mtimes:
                changed = set(current_mtimes.keys()) ^ set(last_mtimes.keys())
                for f in current_mtimes:
                    if f in last_mtimes and current_mtimes[f] != last_mtimes[f]:
                        changed.add(f)

                print(f"\n[{time.strftime('%H:%M:%S')}] Changes detected: {', '.join(str(c) for c in changed)}")
                stats = sync(agent_id, verbose=True)
                print(f"  Summary: pushed={stats['pushed']}, pulled={stats['pulled']}, tree={stats['tree_updated']}")

                last_mtimes = current_mtimes
    except KeyboardInterrupt:
        print("\nStopped watching.")


def _get_mtimes(directory: Path) -> dict[Path, float]:
    mtimes = {}
    if directory.exists():
        for f in directory.glob("*.md"):
            try:
                mtimes[f] = f.stat().st_mtime
            except OSError:
                pass
    return mtimes


# ---------------------------------------------------------------------------
# Post-commit hook mode
# ---------------------------------------------------------------------------


def post_commit(agent_id: str):
    """Run as a git post-commit hook. Silent on success, prints errors."""
    stats = sync(agent_id, verbose=False, local_authoritative=True)
    if stats["errors"]:
        print(f"memfs-sync errors: {'; '.join(stats['errors'])}", file=sys.stderr)
    elif stats["pushed"] > 0 or stats["tree_updated"]:
        # Brief confirmation for post-commit
        parts = []
        if stats["pushed"]:
            parts.append(f"{stats['pushed']} block(s) synced")
        if stats["tree_updated"]:
            parts.append("tree updated")
        print(f"memfs-sync: {', '.join(parts)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _override_base_url(url: str):
    global LETTA_BASE_URL
    LETTA_BASE_URL = url


def main():
    parser = argparse.ArgumentParser(
        description="Sync local MemFS .md files with Letta server blocks"
    )
    parser.add_argument("agent_id", help="Letta agent ID (e.g., agent-xxx-yyy)")
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"Letta server URL (default: {LETTA_BASE_URL})",
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="One-shot sync")
    mode.add_argument("--watch", action="store_true", help="Watch for changes")
    mode.add_argument(
        "--post-commit", action="store_true", help="Run as git post-commit hook"
    )

    args = parser.parse_args()

    if args.base_url:
        _override_base_url(args.base_url)

    if args.once:
        print(f"Syncing agent {args.agent_id} with {LETTA_BASE_URL}...")
        stats = sync(args.agent_id, verbose=True)
        print(f"\nDone: pushed={stats['pushed']}, pulled={stats['pulled']}, conflicts={stats['conflicts']}, tree_updated={stats['tree_updated']}")
        if stats["errors"]:
            print(f"Errors: {stats['errors']}")
            sys.exit(1)
        if stats["conflicts"]:
            print(f"Warning: {stats['conflicts']} conflict(s) detected. Resolve manually or use --post-commit to force local.")
            sys.exit(2)

    elif args.watch:
        watch(args.agent_id)

    elif args.post_commit:
        post_commit(args.agent_id)


if __name__ == "__main__":
    main()
