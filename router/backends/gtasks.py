"""Google Tasks backend: the interview-preparation board.

Google Tasks is the source of truth for Mark's preparation state. One list per lane
(Coding, System design, AI systems, Behavioral and projects), one task per curriculum
unit titled "<ID> - <title>", one subtask per depth. A unit's current depth is its first
open subtask; a unit is complete when every depth subtask is complete; the open unit at
the top of a lane list is that lane's next unit. Notes are append-only.

Credentials are a long-lived OAuth refresh token for Mark's Google account, held only in
this router's environment. The backend never creates or deletes lists and never deletes
tasks; agents reopen or annotate instead.
"""

import asyncio
import json
import os
import time
from datetime import date, datetime
from typing import Any

import httpx
from fastmcp import FastMCP

mcp = FastMCP('gtasks')

API = 'https://tasks.googleapis.com/tasks/v1'
TOKEN_URI = 'https://oauth2.googleapis.com/token'
REQUEST_TIMEOUT_SECONDS = 20.0
PAGE_SIZE = 100
# Google enforces a small per-minute, per-user quota on the Tasks API. Bulk work such as
# seeding trips it; back off and retry rather than failing the whole operation.
QUOTA_RETRY_DELAYS = (2, 4, 8, 16, 30, 30)

DEFAULT_LANES = {
    'coding': 'Coding',
    'system_design': 'System design',
    'agent_domain': 'AI systems',
    'behavioral': 'Behavioral and projects',
}
DEPTH_LABELS = {1: 'Depth 1 · breadth', 2: 'Depth 2 · transfer', 3: 'Depth 3 · interview'}
TASK_ACTIONS = ('list', 'get', 'add', 'complete', 'reopen', 'note', 'move', 'update')

# Access-token cache: (token, unix expiry)
_token_cache: dict[str, Any] = {'token': None, 'expires': 0.0}


def _error(message: str, code: str, **details: Any) -> dict:
    return {'error': message, 'code': code, **details}


def _config() -> dict | tuple[str, str, str]:
    client_id = os.environ.get('GOOGLE_TASKS_CLIENT_ID', '').strip()
    client_secret = os.environ.get('GOOGLE_TASKS_CLIENT_SECRET', '').strip()
    refresh_token = os.environ.get('GOOGLE_TASKS_REFRESH_TOKEN', '').strip()
    if not (client_id and client_secret and refresh_token):
        return _error(
            'Google Tasks is not configured on the router (GOOGLE_TASKS_CLIENT_ID, '
            'GOOGLE_TASKS_CLIENT_SECRET, GOOGLE_TASKS_REFRESH_TOKEN)',
            'not_configured',
        )
    return client_id, client_secret, refresh_token


def lane_titles() -> dict[str, str]:
    """Lane key -> list title. GOOGLE_TASKS_LANES (JSON object) overrides titles."""
    titles = dict(DEFAULT_LANES)
    raw = os.environ.get('GOOGLE_TASKS_LANES', '').strip()
    if raw:
        try:
            loaded = json.loads(raw)
        except ValueError:
            return titles
        for key, title in (loaded or {}).items():
            if key in titles and isinstance(title, str) and title.strip():
                titles[key] = title.strip()
    return titles


async def _access_token(client: httpx.AsyncClient) -> str | dict:
    if _token_cache['token'] and time.time() < _token_cache['expires'] - 60:
        return _token_cache['token']
    config = _config()
    if isinstance(config, dict):
        return config
    client_id, client_secret, refresh_token = config
    try:
        response = await client.post(
            TOKEN_URI,
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token',
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return _error(f'Google token refresh failed: {exc.__class__.__name__}', 'upstream_unavailable')
    if response.status_code >= 400:
        detail = ''
        try:
            detail = str(response.json().get('error', ''))
        except ValueError:
            pass
        return _error(
            'Google refused the refresh token; re-run the gtasks authorization and update the router env',
            'auth_failed',
            status=response.status_code,
            detail=detail,
        )
    payload = response.json()
    _token_cache['token'] = payload['access_token']
    _token_cache['expires'] = time.time() + float(payload.get('expires_in', 3600))
    return _token_cache['token']


async def _api(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    params: dict | None = None,
    body: dict | None = None,
) -> tuple[dict | None, dict | None]:
    """Call the Tasks API. Returns (data, error)."""
    token = await _access_token(client)
    if isinstance(token, dict):
        return None, token
    for attempt, delay in enumerate((*QUOTA_RETRY_DELAYS, None)):
        try:
            response = await client.request(
                method,
                API + path,
                headers={'Authorization': f'Bearer {token}'},
                params={k: v for k, v in (params or {}).items() if v is not None},
                json=body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException:
            return None, _error('Google Tasks request timed out', 'timeout')
        except httpx.HTTPError as exc:
            return None, _error(f'Google Tasks request failed: {exc.__class__.__name__}', 'upstream_unavailable')
        if response.status_code in (403, 429) and delay is not None and _is_quota_error(response):
            await asyncio.sleep(delay)
            continue
        break
    if response.status_code == 401:
        _token_cache['token'] = None
        return None, _error('Google Tasks rejected the access token', 'auth_failed', status=401)
    if response.status_code >= 400:
        detail = ''
        try:
            detail = str(response.json().get('error', {}).get('message', ''))
        except (ValueError, AttributeError):
            pass
        return None, _error('Google Tasks API error', 'upstream_error', status=response.status_code, detail=detail[:300])
    if response.status_code == 204 or not response.content:
        return {}, None
    try:
        return response.json(), None
    except ValueError:
        return None, _error('Google Tasks returned a non-JSON body', 'upstream_error')


def _is_quota_error(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    try:
        message = json.dumps(response.json()).lower()
    except ValueError:
        message = response.text.lower()
    return 'quota' in message or 'ratelimit' in message or 'rate limit' in message


async def _all_lists(client: httpx.AsyncClient) -> tuple[list[dict], dict | None]:
    items: list[dict] = []
    token = None
    while True:
        page, error = await _api(client, 'GET', '/users/@me/lists', {'maxResults': PAGE_SIZE, 'pageToken': token})
        if error:
            return [], error
        items.extend(page.get('items', []))
        token = page.get('nextPageToken')
        if not token:
            return items, None


async def _resolve_list(client: httpx.AsyncClient, ref: str) -> tuple[dict | None, dict | None]:
    """Resolve a lane key, list title, or list id to a task list."""
    ref = (ref or '').strip()
    if not ref:
        return None, _error('list is required (lane key, list title, or list id)', 'invalid_argument')
    lanes = lane_titles()
    wanted = lanes.get(ref.lower().replace('-', '_'), ref).strip().lower()
    lists, error = await _all_lists(client)
    if error:
        return None, error
    for item in lists:
        if item.get('id') == ref or str(item.get('title', '')).strip().lower() == wanted:
            return item, None
    titles = sorted(str(item.get('title')) for item in lists)
    return None, _error(f'no Google Tasks list named {wanted!r}', 'not_found', existing_lists=titles)


async def _all_tasks(client: httpx.AsyncClient, list_id: str, include_completed: bool = True) -> tuple[list[dict], dict | None]:
    items: list[dict] = []
    token = None
    flag = 'true' if include_completed else 'false'
    while True:
        page, error = await _api(
            client,
            'GET',
            f'/lists/{list_id}/tasks',
            {'maxResults': PAGE_SIZE, 'showCompleted': flag, 'showHidden': flag, 'pageToken': token},
        )
        if error:
            return [], error
        items.extend(page.get('items', []))
        token = page.get('nextPageToken')
        if not token:
            break
    items.sort(key=lambda item: str(item.get('position', '')))
    return items, None


def _find_task(items: list[dict], ref: str) -> tuple[dict | None, dict | None]:
    """Resolve a task id, exact title, or unambiguous unit-id prefix among items."""
    ref = (ref or '').strip()
    if not ref:
        return None, _error('task is required (task id, exact title, or unit id such as COD-03)', 'invalid_argument')
    for item in items:
        if item.get('id') == ref:
            return item, None
    exact = [item for item in items if str(item.get('title', '')).strip() == ref]
    if len(exact) == 1:
        return exact[0], None
    prefixed = [
        item
        for item in items
        if not item.get('parent') and str(item.get('title', '')).strip().lower().startswith(ref.lower() + ' ')
    ]
    if len(prefixed) == 1:
        return prefixed[0], None
    if len(exact) > 1 or len(prefixed) > 1:
        return None, _error(f'{ref!r} matches more than one task; use the task id', 'ambiguous')
    return None, _error(f'no task {ref!r} in this list', 'not_found')


def _due_rfc3339(value: str) -> str | None:
    try:
        return date.fromisoformat(value).isoformat() + 'T00:00:00.000Z'
    except ValueError:
        return None


def _public(task: dict) -> dict:
    return {
        'id': task.get('id'),
        'title': task.get('title', ''),
        'status': task.get('status', 'needsAction'),
        'notes': task.get('notes', ''),
        'due': str(task.get('due', ''))[:10],
        'parent': task.get('parent'),
        'position': task.get('position'),
        'updated': task.get('updated'),
    }


def _lane_view(key: str, tasklist: dict, items: list[dict]) -> dict:
    by_parent: dict[str, list[dict]] = {}
    for item in items:
        by_parent.setdefault(str(item.get('parent') or ''), []).append(item)
    units = []
    for item in by_parent.get('', []):
        depths = [
            {'id': sub['id'], 'title': sub.get('title', ''), 'done': sub.get('status') == 'completed'}
            for sub in by_parent.get(str(item['id']), [])
        ]
        open_depths = [d for d in depths if not d['done']]
        done = item.get('status') == 'completed'
        units.append(
            {
                'id': item['id'],
                'unit': str(item.get('title', '')).split(' - ', 1)[0].strip(),
                'title': item.get('title', ''),
                'done': done,
                'current_depth': None if done or not depths else len(depths) - len(open_depths) + 1,
                'current_depth_id': None if done or not open_depths else open_depths[0]['id'],
                'current_work': '' if done or not open_depths else open_depths[0]['title'],
                'depths_done': len(depths) - len(open_depths),
                'depths_total': len(depths),
                'depths': depths,
                'due': str(item.get('due', ''))[:10],
                'notes': item.get('notes', ''),
            }
        )
    open_units = [unit for unit in units if not unit['done']]
    return {
        'key': key,
        'title': tasklist.get('title'),
        'list_id': tasklist.get('id'),
        'next': open_units[0] if open_units else None,
        'open': len(open_units),
        'complete': len(units) - len(open_units),
        'units': units,
    }


@mcp.tool()
async def lists() -> dict:
    """List the Google Tasks lists in Mark's account and the lane mapping the board uses.

    Returns:
        {"lists": [{"id", "title"}], "lanes": {"coding": "Coding", ...}}
    """
    async with httpx.AsyncClient() as client:
        items, error = await _all_lists(client)
        if error:
            return error
    return {'lists': [{'id': item.get('id'), 'title': item.get('title')} for item in items], 'lanes': lane_titles()}


@mcp.tool()
async def board(lane: str = None) -> dict:
    """Derive the four-lane interview-preparation board from Google Tasks. Read-only.

    Args:
        lane: Optional lane key to restrict to: coding | system_design | agent_domain | behavioral.

    Returns:
        {"generated": iso, "lanes": [{"key", "title", "list_id", "next": unit|null, "open", "complete",
        "units": [{"id", "unit", "title", "done", "current_depth", "current_depth_id", "current_work",
        "depths_done", "depths_total", "depths": [...], "due", "notes"}]}]}
        A lane whose list is missing carries {"error", "code": "not_found"} instead of units.
    """
    lanes = lane_titles()
    keys = [lane] if lane else list(lanes)
    unknown = [key for key in keys if key not in lanes]
    if unknown:
        return _error(f'unknown lane {unknown[0]!r}', 'invalid_argument', lanes=list(lanes))
    view = {'generated': datetime.now().isoformat(timespec='minutes'), 'lanes': []}
    async with httpx.AsyncClient() as client:
        for key in keys:
            tasklist, error = await _resolve_list(client, key)
            if error:
                if error.get('code') == 'not_found':
                    view['lanes'].append({'key': key, 'title': lanes[key], **error, 'units': []})
                    continue
                return error
            items, error = await _all_tasks(client, tasklist['id'])
            if error:
                return error
            view['lanes'].append(_lane_view(key, tasklist, items))
    return view


@mcp.tool()
async def tasks(
    action: str,
    list: str,
    task: str = None,
    title: str = None,
    notes: str = None,
    due: str = None,
    parent: str = None,
    top: bool = False,
    include_completed: bool = True,
) -> dict:
    """Read or change tasks in one preparation lane list. Never deletes.

    Args:
        action: "list" | "get" | "add" | "complete" | "reopen" | "note" | "move" | "update"
        list: Lane key (coding, system_design, agent_domain, behavioral), list title, or list id.
        task: Task id, exact title, or unit id prefix such as COD-03 (get/complete/reopen/note/move/update).
        title: New task title (add) or replacement title (update).
        notes: Notes text: set on add/update; appended as a dated line on note.
        due: YYYY-MM-DD (add/update); use only for a live process date, not curriculum work.
        parent: Parent task (id, title, or unit id) to add a subtask under (add).
        top: Move the task to the top of the list (add/move). Top open unit = the lane's next unit.
        include_completed: Include completed tasks (list).

    Returns:
        list: {"list": {...}, "tasks": [...], "count": N}; get/add/update/note/complete/reopen/move: {"task": {...}}
        On error: {"error", "code"}.
    """
    if action not in TASK_ACTIONS:
        return _error(f'invalid action {action!r}', 'invalid_argument', valid_actions=[*TASK_ACTIONS])
    if action != 'list' and action != 'add' and not task:
        return _error(f'task is required for {action}', 'invalid_argument')
    if due is not None and due != '' and _due_rfc3339(due) is None:
        return _error('due must be YYYY-MM-DD', 'invalid_argument')

    async with httpx.AsyncClient() as client:
        tasklist, error = await _resolve_list(client, list)
        if error:
            return error
        list_id = tasklist['id']

        if action == 'list':
            items, error = await _all_tasks(client, list_id, include_completed)
            if error:
                return error
            return {
                'list': {'id': list_id, 'title': tasklist.get('title')},
                'tasks': [_public(item) for item in items],
                'count': len(items),
            }

        if action == 'add':
            if not title or not title.strip():
                return _error('title is required for add', 'invalid_argument')
            body: dict[str, Any] = {'title': title.strip()}
            if notes:
                body['notes'] = notes
            if due:
                body['due'] = _due_rfc3339(due)
            params: dict[str, Any] = {}
            if parent:
                items, error = await _all_tasks(client, list_id)
                if error:
                    return error
                parent_task, error = _find_task(items, parent)
                if error:
                    return error
                params['parent'] = parent_task['id']
            created, error = await _api(client, 'POST', f'/lists/{list_id}/tasks', params or None, body)
            if error:
                return error
            if top and not parent:
                created, error = await _api(client, 'POST', f"/lists/{list_id}/tasks/{created['id']}/move")
                if error:
                    return error
            return {'task': _public(created)}

        items, error = await _all_tasks(client, list_id)
        if error:
            return error
        target, error = _find_task(items, task)
        if error:
            return error
        task_id = target['id']

        if action == 'get':
            children = [_public(item) for item in items if item.get('parent') == task_id]
            return {'task': _public(target), 'subtasks': children}
        if action == 'complete':
            updated, error = await _api(client, 'PATCH', f'/lists/{list_id}/tasks/{task_id}', body={'status': 'completed'})
        elif action == 'reopen':
            updated, error = await _api(
                client, 'PATCH', f'/lists/{list_id}/tasks/{task_id}', body={'status': 'needsAction', 'completed': None}
            )
        elif action == 'note':
            if not notes or not notes.strip():
                return _error('notes text is required for note', 'invalid_argument')
            existing = str(target.get('notes') or '').rstrip()
            combined = (existing + '\n\n' if existing else '') + f'{date.today().isoformat()}: {notes.strip()}'
            updated, error = await _api(client, 'PATCH', f'/lists/{list_id}/tasks/{task_id}', body={'notes': combined})
        elif action == 'move':
            if not top:
                return _error('move supports top=true only; ordering within the list is the queue', 'invalid_argument')
            params = {'parent': target['parent']} if target.get('parent') else None
            updated, error = await _api(client, 'POST', f'/lists/{list_id}/tasks/{task_id}/move', params)
        else:  # update
            body = {}
            if title and title.strip():
                body['title'] = title.strip()
            if notes is not None:
                body['notes'] = notes
            if due is not None:
                body['due'] = _due_rfc3339(due) if due else None
            if not body:
                return _error('nothing to update', 'invalid_argument')
            updated, error = await _api(client, 'PATCH', f'/lists/{list_id}/tasks/{task_id}', body=body)
        if error:
            return error
        return {'task': _public(updated)}


def seed_plan(units: list[dict], existing_titles: dict[str, set[str]], selected: str | None) -> list[dict]:
    """Turn exported curriculum units into create operations, skipping units already present."""
    plan = []
    for unit in units:
        lane = str(unit.get('lane', ''))
        unit_id = str(unit.get('id', '')).strip()
        if lane not in DEFAULT_LANES or not unit_id or not unit.get('title'):
            plan.append({'lane': lane, 'title': unit_id or '?', 'skip': 'invalid unit (needs lane, id, title)'})
            continue
        title = f"{unit_id} - {str(unit['title']).strip()}"
        if title in existing_titles.get(lane, set()):
            plan.append({'lane': lane, 'title': title, 'skip': 'already present'})
            continue
        max_depth = int(unit.get('max_depth') or 3)
        current_depth = int(unit.get('depth') or 1)
        solid = unit.get('status') == 'solid'
        depths = []
        for depth in range(1, max_depth + 1):
            label = DEPTH_LABELS.get(depth, f'Depth {depth}')
            work = str(unit.get('work') or '').strip() if depth == current_depth else ''
            depths.append({'title': f'{label}: {work}' if work else label, 'done': solid or depth < current_depth})
        notes = []
        if unit.get('activation') and unit['activation'] != 'core':
            notes.append(f"Overlay ({unit['activation']}), not core breadth work.")
        if unit.get('overlay'):
            notes.append('Role overlay: visible, not required for the core sweep.')
        if unit.get('evidence'):
            notes.append('Evidence: ' + str(unit['evidence']).strip())
        notes.append(f'Imported from the curriculum on {date.today().isoformat()}.')
        plan.append(
            {
                'lane': lane,
                'title': title,
                'notes': '\n'.join(notes),
                'done': solid,
                'depths': depths,
                'top': unit_id == selected,
            }
        )
    return plan


@mcp.tool()
async def seed(units: list[dict], selected: str = None, dry_run: bool = True) -> dict:
    """Load curriculum units into the existing lane lists. Idempotent by unit title.

    Args:
        units: [{"lane": "coding", "id": "COD-03", "title": "...", "depth": 1, "max_depth": 3,
                 "status": "not_started|learning|revisit|solid", "work": "...", "evidence": "...",
                 "activation": "core|on_demand", "overlay": false}]
        selected: Unit id to move to the top of its lane (the selected unit).
        dry_run: Preview only (default true). Pass false to create.

    Returns:
        {"dry_run", "created": N, "skipped": N, "report": ["create coding: COD-03 - ...", ...]}
    """
    if not isinstance(units, list) or not units:
        return _error('units must be a non-empty list', 'invalid_argument')
    lanes_needed = sorted({str(unit.get('lane', '')) for unit in units})
    report: list[str] = []
    created = skipped = 0
    async with httpx.AsyncClient() as client:
        list_ids: dict[str, str] = {}
        existing: dict[str, set[str]] = {}
        for lane in lanes_needed:
            if lane not in DEFAULT_LANES:
                continue
            tasklist, error = await _resolve_list(client, lane)
            if error:
                return error
            list_ids[lane] = tasklist['id']
            items, error = await _all_tasks(client, tasklist['id'])
            if error:
                return error
            existing[lane] = {str(item.get('title', '')).strip() for item in items if not item.get('parent')}
        for entry in seed_plan(units, existing, selected):
            if entry.get('skip'):
                skipped += 1
                report.append(f"skip {entry['lane']}: {entry['title']} ({entry['skip']})")
                continue
            done_depths = sum(d['done'] for d in entry['depths'])
            status = 'done' if entry['done'] else f"{done_depths}/{len(entry['depths'])} depths done"
            report.append(f"{'would create' if dry_run else 'create'} {entry['lane']}: {entry['title']} [{status}{', top' if entry['top'] else ''}]")
            created += 1
            if dry_run:
                continue
            list_id = list_ids[entry['lane']]
            parent, error = await _api(client, 'POST', f'/lists/{list_id}/tasks', body={'title': entry['title'], 'notes': entry['notes']})
            if error:
                return {**error, 'report': report}
            previous = None
            for depth in entry['depths']:
                child, error = await _api(client, 'POST', f'/lists/{list_id}/tasks', {'parent': parent['id']}, {'title': depth['title']})
                if error:
                    return {**error, 'report': report}
                if previous:
                    _, error = await _api(client, 'POST', f"/lists/{list_id}/tasks/{child['id']}/move", {'parent': parent['id'], 'previous': previous})
                    if error:
                        return {**error, 'report': report}
                if depth['done']:
                    _, error = await _api(client, 'PATCH', f"/lists/{list_id}/tasks/{child['id']}", body={'status': 'completed'})
                    if error:
                        return {**error, 'report': report}
                previous = child['id']
            if entry['done']:
                _, error = await _api(client, 'PATCH', f"/lists/{list_id}/tasks/{parent['id']}", body={'status': 'completed'})
                if error:
                    return {**error, 'report': report}
            if entry['top']:
                _, error = await _api(client, 'POST', f"/lists/{list_id}/tasks/{parent['id']}/move")
                if error:
                    return {**error, 'report': report}
    return {'dry_run': dry_run, 'created': created, 'skipped': skipped, 'report': report}
