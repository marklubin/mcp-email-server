"""Todoist backend for task management.

Provides a streamlined 2-tool API that consolidates the full Todoist API
into LLM-friendly operations with deduped metadata.
"""

import os
import uuid
from typing import Optional

import httpx

from fastmcp import FastMCP

TODOIST_API_TOKEN = os.environ.get('TODOIST_API_TOKEN', '')
BASE_URL = 'https://api.todoist.com/api/v1'
SYNC_URL = 'https://api.todoist.com/api/v1/sync'

mcp = FastMCP('todoist')


def _headers() -> dict:
    """Return headers with auth token."""
    return {
        'Authorization': f'Bearer {TODOIST_API_TOKEN}',
        'Content-Type': 'application/json',
    }


async def _api(
    method: str,
    endpoint: str,
    params: dict = None,
    json_body: dict = None,
) -> tuple[dict | list | None, str | None]:
    """Make API request to Todoist REST API.

    Returns:
        Tuple of (response_data, error_message)
    """
    url = f'{BASE_URL}/{endpoint}'

    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(
                method,
                url,
                headers=_headers(),
                params=params,
                json=json_body,
                timeout=30,
            )

            if response.status_code == 204:
                return None, None

            if response.status_code >= 400:
                return None, f'API error {response.status_code}: {response.text}'

            data = response.json()
            # v1 API wraps list responses in {"results": [...]}
            if isinstance(data, dict) and 'results' in data:
                data = data['results']
            return data, None

        except httpx.TimeoutException:
            return None, 'Request timed out'
        except Exception as e:
            return None, str(e)


async def _sync_api(commands: list) -> tuple[dict | None, str | None]:
    """Make request to Todoist Sync API (for reminders)."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                SYNC_URL,
                headers=_headers(),
                json={
                    'commands': commands,
                },
                timeout=30,
            )

            if response.status_code >= 400:
                return None, f'Sync API error {response.status_code}: {response.text}'

            return response.json(), None

        except httpx.TimeoutException:
            return None, 'Request timed out'
        except Exception as e:
            return None, str(e)


async def _add_comment(task_id: str, content: str) -> tuple[dict | None, str | None]:
    """Add a comment to a task."""
    return await _api('POST', 'comments', json_body={
        'task_id': task_id,
        'content': content,
    })


async def _add_reminder(task_id: str, due_string: str) -> tuple[dict | None, str | None]:
    """Add a reminder to a task via Sync API.

    Note: Reminders require Todoist Premium. Free tier will get an error.
    """
    temp_id = str(uuid.uuid4())
    commands = [{
        'type': 'reminder_add',
        'temp_id': temp_id,
        'uuid': str(uuid.uuid4()),
        'args': {
            'item_id': task_id,
            'due': {'string': due_string},
        },
    }]

    result, error = await _sync_api(commands)
    if error:
        return None, error

    # Check for sync errors
    if result and 'sync_status' in result:
        status = result['sync_status'].get(commands[0]['uuid'])
        if status and status != 'ok':
            return None, f'Reminder error: {status}'

    return result, None


async def _get_deduped_metadata(tasks: list) -> tuple[dict, dict]:
    """Fetch deduped project and section metadata for task list.

    Returns:
        Tuple of (projects_map, sections_map)
    """
    # Collect unique IDs from tasks
    project_ids = set()
    section_ids = set()

    for task in tasks:
        if task.get('project_id'):
            project_ids.add(task['project_id'])
        if task.get('section_id'):
            section_ids.add(task['section_id'])

    # Fetch all projects
    projects_data, _ = await _api('GET', 'projects')
    projects_map = {}
    if projects_data:
        for p in projects_data:
            if p['id'] in project_ids:
                projects_map[p['id']] = {
                    'name': p['name'],
                    'color': p.get('color'),
                }

    # Fetch sections for referenced projects
    sections_map = {}
    for pid in project_ids:
        sections_data, _ = await _api('GET', 'sections', params={'project_id': pid})
        if sections_data:
            for s in sections_data:
                if s['id'] in section_ids:
                    sections_map[s['id']] = {
                        'name': s['name'],
                        'project_id': s['project_id'],
                    }

    return projects_map, sections_map


@mcp.tool()
async def tasks(
    action: str,
    task_id: str = None,
    # Filters (list)
    project_id: str = None,
    section_id: str = None,
    filter: str = None,
    # Task fields (create/update)
    content: str = None,
    description: str = None,
    priority: int = None,
    due_string: str = None,
    due_date: str = None,
    labels: list[str] = None,
    # Inline additions
    comment: str = None,
    reminder: str = None,
) -> dict:
    """Manage Todoist tasks.

    Args:
        action: "list" | "get" | "create" | "update" | "delete" | "complete" | "reopen"
        task_id: Task ID (required for get/update/delete/complete/reopen)
        project_id: Filter by project (list) or assign to project (create)
        section_id: Filter by section (list) or assign to section (create)
        filter: Todoist filter string - "today", "p1", "@label", etc. (list)
        content: Task title/content (create/update)
        description: Task description (create/update)
        priority: 1-4, where 4 is urgent (create/update)
        due_string: Natural language due date - "tomorrow 3pm", "every monday" (create/update)
        due_date: ISO date YYYY-MM-DD (create/update)
        labels: List of label names (create/update)
        comment: Add a comment when creating/updating
        reminder: Add a reminder when creating (Premium only)

    Returns:
        For list: {"tasks": [...], "count": N, "projects": {...}, "sections": {...}}
        For get/create/update: {"task": {...}}
        For delete/complete/reopen: {"success": True}
        On error: {"error": "message", "detail": "..."}
    """
    valid_actions = ['list', 'get', 'create', 'update', 'delete', 'complete', 'reopen']
    if action not in valid_actions:
        return {'error': f'Invalid action: {action}', 'detail': f'Valid actions: {valid_actions}'}

    # Actions that require task_id
    if action in ['get', 'update', 'delete', 'complete', 'reopen'] and not task_id:
        return {'error': f'task_id required for {action}'}

    # LIST
    if action == 'list':
        params = {}
        if project_id:
            params['project_id'] = project_id
        if section_id:
            params['section_id'] = section_id
        if filter:
            params['filter'] = filter

        tasks_data, error = await _api('GET', 'tasks', params=params if params else None)
        if error:
            return {'error': error}

        tasks_list = tasks_data or []
        projects_map, sections_map = await _get_deduped_metadata(tasks_list)

        return {
            'tasks': tasks_list,
            'count': len(tasks_list),
            'projects': projects_map,
            'sections': sections_map,
        }

    # GET
    if action == 'get':
        task_data, error = await _api('GET', f'tasks/{task_id}')
        if error:
            return {'error': error}
        return {'task': task_data}

    # CREATE
    if action == 'create':
        if not content:
            return {'error': 'content required for create'}

        body = {'content': content}
        if description:
            body['description'] = description
        if project_id:
            body['project_id'] = project_id
        if section_id:
            body['section_id'] = section_id
        if priority:
            body['priority'] = priority
        if due_string:
            body['due_string'] = due_string
        if due_date:
            body['due_date'] = due_date
        if labels:
            body['labels'] = labels

        task_data, error = await _api('POST', 'tasks', json_body=body)
        if error:
            return {'error': error}

        created_task_id = task_data['id']

        # Add comment if provided
        if comment:
            _, comment_error = await _add_comment(created_task_id, comment)
            if comment_error:
                return {
                    'task': task_data,
                    'warning': f'Task created but comment failed: {comment_error}',
                }

        # Add reminder if provided (Premium only)
        if reminder:
            _, reminder_error = await _add_reminder(created_task_id, reminder)
            if reminder_error:
                return {
                    'task': task_data,
                    'warning': f'Task created but reminder failed: {reminder_error}',
                }

        return {'task': task_data}

    # UPDATE
    if action == 'update':
        body = {}
        if content:
            body['content'] = content
        if description is not None:
            body['description'] = description
        if priority:
            body['priority'] = priority
        if due_string:
            body['due_string'] = due_string
        if due_date:
            body['due_date'] = due_date
        if labels is not None:
            body['labels'] = labels

        if not body and not comment and not section_id:
            return {'error': 'No fields to update'}

        task_data = None

        # Move to section if provided
        if section_id:
            _, move_error = await _api('POST', f'tasks/{task_id}/move', json_body={
                'section_id': section_id,
            })
            if move_error:
                return {'error': f'Failed to move task: {move_error}'}

        if body:
            task_data, error = await _api('POST', f'tasks/{task_id}', json_body=body)
            if error:
                return {'error': error}

        # Add comment if provided
        if comment:
            _, comment_error = await _add_comment(task_id, comment)
            if comment_error:
                return {
                    'task': task_data,
                    'warning': f'Task updated but comment failed: {comment_error}',
                }

        # If only comment was added, fetch the task
        if not task_data:
            task_data, error = await _api('GET', f'tasks/{task_id}')
            if error:
                return {'error': error}

        return {'task': task_data}

    # DELETE
    if action == 'delete':
        _, error = await _api('DELETE', f'tasks/{task_id}')
        if error:
            return {'error': error}
        return {'success': True, 'task_id': task_id}

    # COMPLETE
    if action == 'complete':
        _, error = await _api('POST', f'tasks/{task_id}/close')
        if error:
            return {'error': error}
        return {'success': True, 'task_id': task_id}

    # REOPEN
    if action == 'reopen':
        _, error = await _api('POST', f'tasks/{task_id}/reopen')
        if error:
            return {'error': error}
        return {'success': True, 'task_id': task_id}


@mcp.tool()
async def projects(
    action: str,
    project_id: str = None,
    section_id: str = None,
    # Project fields
    name: str = None,
    color: str = None,
    is_favorite: bool = None,
    view_style: str = None,
    # Section fields
    section_name: str = None,
    section_order: int = None,
) -> dict:
    """Manage Todoist projects and sections.

    Args:
        action: "list" | "get" | "create" | "update" | "delete" |
                "list_sections" | "add_section" | "delete_section"
        project_id: Project ID (required for most actions)
        section_id: Section ID (required for delete_section)
        name: Project name (create/update)
        color: Project color (create/update)
        is_favorite: Favorite status (create/update)
        view_style: "list" or "board" (create/update)
        section_name: Section name (add_section)
        section_order: Section order (add_section)

    Returns:
        For list: {"projects": [...], "count": N}
        For get: {"project": {...}, "sections": [...]}
        For create/update: {"project": {...}}
        For list_sections: {"sections": [...], "count": N}
        For add_section: {"section": {...}}
        For delete/delete_section: {"success": True}
        On error: {"error": "message"}
    """
    valid_actions = ['list', 'get', 'create', 'update', 'delete',
                     'list_sections', 'add_section', 'delete_section']
    if action not in valid_actions:
        return {'error': f'Invalid action: {action}', 'detail': f'Valid actions: {valid_actions}'}

    # Actions that require project_id
    if action in ['get', 'update', 'delete', 'list_sections', 'add_section'] and not project_id:
        return {'error': f'project_id required for {action}'}

    # Actions that require section_id
    if action == 'delete_section' and not section_id:
        return {'error': 'section_id required for delete_section'}

    # LIST
    if action == 'list':
        projects_data, error = await _api('GET', 'projects')
        if error:
            return {'error': error}
        return {
            'projects': projects_data or [],
            'count': len(projects_data or []),
        }

    # GET (includes sections)
    if action == 'get':
        project_data, error = await _api('GET', f'projects/{project_id}')
        if error:
            return {'error': error}

        sections_data, _ = await _api('GET', 'sections', params={'project_id': project_id})

        return {
            'project': project_data,
            'sections': sections_data or [],
        }

    # CREATE
    if action == 'create':
        if not name:
            return {'error': 'name required for create'}

        body = {'name': name}
        if color:
            body['color'] = color
        if is_favorite is not None:
            body['is_favorite'] = is_favorite
        if view_style:
            body['view_style'] = view_style

        project_data, error = await _api('POST', 'projects', json_body=body)
        if error:
            return {'error': error}
        return {'project': project_data}

    # UPDATE
    if action == 'update':
        body = {}
        if name:
            body['name'] = name
        if color:
            body['color'] = color
        if is_favorite is not None:
            body['is_favorite'] = is_favorite
        if view_style:
            body['view_style'] = view_style

        if not body:
            return {'error': 'No fields to update'}

        project_data, error = await _api('POST', f'projects/{project_id}', json_body=body)
        if error:
            return {'error': error}
        return {'project': project_data}

    # DELETE
    if action == 'delete':
        _, error = await _api('DELETE', f'projects/{project_id}')
        if error:
            return {'error': error}
        return {'success': True, 'project_id': project_id}

    # LIST_SECTIONS
    if action == 'list_sections':
        sections_data, error = await _api('GET', 'sections', params={'project_id': project_id})
        if error:
            return {'error': error}
        return {
            'sections': sections_data or [],
            'count': len(sections_data or []),
        }

    # ADD_SECTION
    if action == 'add_section':
        if not section_name:
            return {'error': 'section_name required for add_section'}

        body = {
            'project_id': project_id,
            'name': section_name,
        }
        if section_order is not None:
            body['order'] = section_order

        section_data, error = await _api('POST', 'sections', json_body=body)
        if error:
            return {'error': error}
        return {'section': section_data}

    # DELETE_SECTION
    if action == 'delete_section':
        _, error = await _api('DELETE', f'sections/{section_id}')
        if error:
            return {'error': error}
        return {'success': True, 'section_id': section_id}
