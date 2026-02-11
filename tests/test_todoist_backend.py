"""Tests for the Todoist backend."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

import httpx


# Mock API responses
MOCK_TASKS = [
    {
        'id': '123',
        'content': 'Test task',
        'description': 'A test task description',
        'project_id': 'p1',
        'section_id': 's1',
        'priority': 1,
        'labels': ['work'],
        'due': {'date': '2026-02-01', 'string': 'today'},
    },
    {
        'id': '124',
        'content': 'Another task',
        'description': '',
        'project_id': 'p1',
        'section_id': None,
        'priority': 4,
        'labels': [],
        'due': None,
    },
    {
        'id': '125',
        'content': 'Personal task',
        'description': '',
        'project_id': 'p2',
        'section_id': None,
        'priority': 2,
        'labels': ['personal'],
        'due': {'date': '2026-02-02', 'string': 'tomorrow'},
    },
]

MOCK_PROJECTS = [
    {'id': 'p1', 'name': 'Work', 'color': 'blue', 'is_favorite': True, 'view_style': 'list'},
    {'id': 'p2', 'name': 'Personal', 'color': 'green', 'is_favorite': False, 'view_style': 'board'},
    {'id': 'p3', 'name': 'Archive', 'color': 'grey', 'is_favorite': False, 'view_style': 'list'},
]

MOCK_SECTIONS = [
    {'id': 's1', 'name': 'To Do', 'project_id': 'p1', 'order': 1},
    {'id': 's2', 'name': 'In Progress', 'project_id': 'p1', 'order': 2},
    {'id': 's3', 'name': 'Done', 'project_id': 'p1', 'order': 3},
]

MOCK_COMMENT = {
    'id': 'c1',
    'task_id': '123',
    'content': 'This is a comment',
}


class MockResponse:
    """Mock httpx response."""

    def __init__(self, status_code: int, json_data=None, text: str = ''):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text or (str(json_data) if json_data else '')

    def json(self):
        return self._json_data


async def call_tool(tool, **kwargs):
    """Call a FastMCP tool's underlying function."""
    return await tool.fn(**kwargs)


@pytest.fixture
def todoist_env_vars(monkeypatch):
    """Set required environment variables for Todoist tests."""
    monkeypatch.setenv('TODOIST_API_TOKEN', 'test-token-12345')


def create_mock_todoist_api():
    """Create mock httpx.AsyncClient for Todoist API calls."""

    async def mock_request(method, url, **kwargs):
        """Route mock requests based on URL and method."""
        # Tasks endpoints
        if '/api/v1/tasks' in url:
            if method == 'GET':
                if url.endswith('/tasks'):
                    return MockResponse(200, MOCK_TASKS)
                # Get single task
                task_id = url.split('/')[-1]
                for task in MOCK_TASKS:
                    if task['id'] == task_id:
                        return MockResponse(200, task)
                return MockResponse(404, text='Task not found')
            elif method == 'POST':
                if '/close' in url:
                    return MockResponse(204)
                if '/reopen' in url:
                    return MockResponse(204)
                if '/move' in url:
                    return MockResponse(204)
                # Create or update
                json_body = kwargs.get('json', {})
                new_task = {
                    'id': '999',
                    'content': json_body.get('content', 'New task'),
                    'description': json_body.get('description', ''),
                    'project_id': json_body.get('project_id', 'p1'),
                    'section_id': json_body.get('section_id'),
                    'priority': json_body.get('priority', 1),
                    'labels': json_body.get('labels', []),
                    'due': None,
                }
                if json_body.get('due_string'):
                    new_task['due'] = {'string': json_body['due_string']}
                return MockResponse(200, new_task)
            elif method == 'DELETE':
                return MockResponse(204)

        # Projects endpoints
        if '/api/v1/projects' in url:
            if method == 'GET':
                if url.endswith('/projects'):
                    return MockResponse(200, MOCK_PROJECTS)
                # Get single project
                project_id = url.split('/')[-1]
                for proj in MOCK_PROJECTS:
                    if proj['id'] == project_id:
                        return MockResponse(200, proj)
                return MockResponse(404, text='Project not found')
            elif method == 'POST':
                json_body = kwargs.get('json', {})
                new_proj = {
                    'id': 'p999',
                    'name': json_body.get('name', 'New Project'),
                    'color': json_body.get('color', 'blue'),
                    'is_favorite': json_body.get('is_favorite', False),
                    'view_style': json_body.get('view_style', 'list'),
                }
                return MockResponse(200, new_proj)
            elif method == 'DELETE':
                return MockResponse(204)

        # Sections endpoints
        if '/api/v1/sections' in url:
            if method == 'GET':
                params = kwargs.get('params', {})
                project_id = params.get('project_id')
                if project_id:
                    filtered = [s for s in MOCK_SECTIONS if s['project_id'] == project_id]
                    return MockResponse(200, filtered)
                return MockResponse(200, MOCK_SECTIONS)
            elif method == 'POST':
                json_body = kwargs.get('json', {})
                new_section = {
                    'id': 's999',
                    'name': json_body.get('name', 'New Section'),
                    'project_id': json_body.get('project_id', 'p1'),
                    'order': json_body.get('order', 1),
                }
                return MockResponse(200, new_section)
            elif method == 'DELETE':
                return MockResponse(204)

        # Comments endpoint
        if '/api/v1/comments' in url:
            if method == 'POST':
                json_body = kwargs.get('json', {})
                new_comment = {
                    'id': 'c999',
                    'task_id': json_body.get('task_id'),
                    'content': json_body.get('content'),
                }
                return MockResponse(200, new_comment)

        # Sync API (for reminders)
        if '/api/v1/sync' in url:
            # Simulate premium required error
            return MockResponse(200, {
                'sync_status': {
                    kwargs.get('json', {}).get('commands', [{}])[0].get('uuid', ''): 'error',
                },
            })

        return MockResponse(404, text='Unknown endpoint')

    # Create mock for post that properly awaits
    async def mock_post(url, **kw):
        return await mock_request('POST', url, **kw)

    # Create the mock client context manager
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(side_effect=mock_request)
    mock_client.post = AsyncMock(side_effect=mock_post)

    return mock_client


@pytest.fixture
def mock_todoist_api():
    """Fixture that patches httpx for Todoist API."""
    mock_client = create_mock_todoist_api()
    with patch('router.backends.todoist.httpx.AsyncClient', return_value=mock_client):
        yield mock_client


class TestTasksTool:
    """Tests for the tasks tool."""

    async def test_tasks_list_returns_tasks_with_metadata(self, mock_todoist_api, todoist_env_vars):
        """List should return tasks with deduped projects/sections metadata."""
        from router.backends.todoist import tasks

        result = await call_tool(tasks, action='list')

        assert 'tasks' in result
        assert 'count' in result
        assert 'projects' in result
        assert 'sections' in result
        assert result['count'] == len(MOCK_TASKS)

        # Verify deduped metadata contains only referenced projects
        assert 'p1' in result['projects']
        assert 'p2' in result['projects']
        # p3 is not referenced by any task
        assert 'p3' not in result['projects']

        # Verify section metadata
        assert 's1' in result['sections']

    async def test_tasks_list_with_filter(self, mock_todoist_api, todoist_env_vars):
        """List with filter should pass filter param to API."""
        from router.backends.todoist import tasks

        result = await call_tool(tasks, action='list', filter='today')

        assert 'tasks' in result
        assert 'error' not in result

    async def test_tasks_list_with_project_filter(self, mock_todoist_api, todoist_env_vars):
        """List with project_id should filter by project."""
        from router.backends.todoist import tasks

        result = await call_tool(tasks, action='list', project_id='p1')

        assert 'tasks' in result
        assert 'error' not in result

    async def test_tasks_create_basic(self, mock_todoist_api, todoist_env_vars):
        """Create should create a task and return it."""
        from router.backends.todoist import tasks

        result = await call_tool(tasks, action='create', content='New test task')

        assert 'task' in result
        assert result['task']['content'] == 'New test task'
        assert 'error' not in result

    async def test_tasks_create_with_all_fields(self, mock_todoist_api, todoist_env_vars):
        """Create with all fields should include them in request."""
        from router.backends.todoist import tasks

        result = await call_tool(
            tasks,
            action='create',
            content='Full task',
            description='A description',
            project_id='p1',
            section_id='s1',
            priority=4,
            due_string='tomorrow 3pm',
            labels=['urgent', 'work'],
        )

        assert 'task' in result
        assert 'error' not in result

    async def test_tasks_create_with_comment(self, mock_todoist_api, todoist_env_vars):
        """Create with comment should create task and add comment."""
        from router.backends.todoist import tasks

        result = await call_tool(
            tasks,
            action='create',
            content='Task with comment',
            comment='Initial comment',
        )

        assert 'task' in result
        assert 'error' not in result
        # No warning means comment was added successfully
        assert 'warning' not in result or 'comment' not in result.get('warning', '')

    async def test_tasks_create_with_reminder_free_tier(self, mock_todoist_api, todoist_env_vars):
        """Create with reminder on free tier should return warning."""
        from router.backends.todoist import tasks

        result = await call_tool(
            tasks,
            action='create',
            content='Task with reminder',
            reminder='tomorrow 9am',
        )

        assert 'task' in result
        # Should have warning about reminder failing (Premium required)
        assert 'warning' in result
        assert 'reminder' in result['warning'].lower()

    async def test_tasks_create_missing_content_error(self, mock_todoist_api, todoist_env_vars):
        """Create without content should return error."""
        from router.backends.todoist import tasks

        result = await call_tool(tasks, action='create')

        assert 'error' in result
        assert 'content' in result['error'].lower()

    async def test_tasks_get_single(self, mock_todoist_api, todoist_env_vars):
        """Get should fetch a single task by ID."""
        from router.backends.todoist import tasks

        result = await call_tool(tasks, action='get', task_id='123')

        assert 'task' in result
        assert result['task']['id'] == '123'
        assert result['task']['content'] == 'Test task'

    async def test_tasks_update_content(self, mock_todoist_api, todoist_env_vars):
        """Update should modify task fields."""
        from router.backends.todoist import tasks

        result = await call_tool(
            tasks,
            action='update',
            task_id='123',
            content='Updated content',
        )

        assert 'task' in result
        assert 'error' not in result

    async def test_tasks_update_with_comment(self, mock_todoist_api, todoist_env_vars):
        """Update with comment should update task and add comment."""
        from router.backends.todoist import tasks

        result = await call_tool(
            tasks,
            action='update',
            task_id='123',
            content='Updated content',
            comment='Follow-up comment',
        )

        assert 'task' in result
        assert 'error' not in result

    async def test_tasks_update_comment_only(self, mock_todoist_api, todoist_env_vars):
        """Update with only comment should add comment to existing task."""
        from router.backends.todoist import tasks

        result = await call_tool(
            tasks,
            action='update',
            task_id='123',
            comment='Just a comment',
        )

        assert 'task' in result
        assert 'error' not in result

    async def test_tasks_update_no_fields_error(self, mock_todoist_api, todoist_env_vars):
        """Update with no fields should return error."""
        from router.backends.todoist import tasks

        result = await call_tool(tasks, action='update', task_id='123')

        assert 'error' in result

    async def test_tasks_complete(self, mock_todoist_api, todoist_env_vars):
        """Complete should close the task."""
        from router.backends.todoist import tasks

        result = await call_tool(tasks, action='complete', task_id='123')

        assert result['success'] is True
        assert result['task_id'] == '123'

    async def test_tasks_reopen(self, mock_todoist_api, todoist_env_vars):
        """Reopen should reopen a completed task."""
        from router.backends.todoist import tasks

        result = await call_tool(tasks, action='reopen', task_id='123')

        assert result['success'] is True
        assert result['task_id'] == '123'

    async def test_tasks_delete(self, mock_todoist_api, todoist_env_vars):
        """Delete should remove the task."""
        from router.backends.todoist import tasks

        result = await call_tool(tasks, action='delete', task_id='123')

        assert result['success'] is True
        assert result['task_id'] == '123'

    async def test_tasks_missing_task_id_error(self, mock_todoist_api, todoist_env_vars):
        """Actions requiring task_id should return error if missing."""
        from router.backends.todoist import tasks

        for action in ['get', 'update', 'delete', 'complete', 'reopen']:
            result = await call_tool(tasks, action=action)
            assert 'error' in result
            assert 'task_id' in result['error'].lower()

    async def test_tasks_invalid_action_error(self, mock_todoist_api, todoist_env_vars):
        """Invalid action should return error."""
        from router.backends.todoist import tasks

        result = await call_tool(tasks, action='invalid_action')

        assert 'error' in result
        assert 'invalid' in result['error'].lower()


class TestProjectsTool:
    """Tests for the projects tool."""

    async def test_projects_list(self, mock_todoist_api, todoist_env_vars):
        """List should return all projects."""
        from router.backends.todoist import projects

        result = await call_tool(projects, action='list')

        assert 'projects' in result
        assert 'count' in result
        assert result['count'] == len(MOCK_PROJECTS)
        assert result['projects'][0]['name'] == 'Work'

    async def test_projects_get_includes_sections(self, mock_todoist_api, todoist_env_vars):
        """Get should return project with its sections."""
        from router.backends.todoist import projects

        result = await call_tool(projects, action='get', project_id='p1')

        assert 'project' in result
        assert 'sections' in result
        assert result['project']['id'] == 'p1'
        assert result['project']['name'] == 'Work'
        # Should have sections for this project
        assert len(result['sections']) > 0

    async def test_projects_create(self, mock_todoist_api, todoist_env_vars):
        """Create should create a new project."""
        from router.backends.todoist import projects

        result = await call_tool(projects, action='create', name='New Project')

        assert 'project' in result
        assert result['project']['name'] == 'New Project'

    async def test_projects_create_with_all_fields(self, mock_todoist_api, todoist_env_vars):
        """Create with all fields should include them."""
        from router.backends.todoist import projects

        result = await call_tool(
            projects,
            action='create',
            name='Full Project',
            color='red',
            is_favorite=True,
            view_style='board',
        )

        assert 'project' in result
        assert 'error' not in result

    async def test_projects_create_missing_name_error(self, mock_todoist_api, todoist_env_vars):
        """Create without name should return error."""
        from router.backends.todoist import projects

        result = await call_tool(projects, action='create')

        assert 'error' in result
        assert 'name' in result['error'].lower()

    async def test_projects_update(self, mock_todoist_api, todoist_env_vars):
        """Update should modify project fields."""
        from router.backends.todoist import projects

        result = await call_tool(
            projects,
            action='update',
            project_id='p1',
            name='Updated Name',
        )

        assert 'project' in result
        assert 'error' not in result

    async def test_projects_update_no_fields_error(self, mock_todoist_api, todoist_env_vars):
        """Update with no fields should return error."""
        from router.backends.todoist import projects

        result = await call_tool(projects, action='update', project_id='p1')

        assert 'error' in result

    async def test_projects_delete(self, mock_todoist_api, todoist_env_vars):
        """Delete should remove the project."""
        from router.backends.todoist import projects

        result = await call_tool(projects, action='delete', project_id='p1')

        assert result['success'] is True
        assert result['project_id'] == 'p1'

    async def test_projects_list_sections(self, mock_todoist_api, todoist_env_vars):
        """List sections should return sections for a project."""
        from router.backends.todoist import projects

        result = await call_tool(projects, action='list_sections', project_id='p1')

        assert 'sections' in result
        assert 'count' in result
        assert result['count'] > 0

    async def test_projects_add_section(self, mock_todoist_api, todoist_env_vars):
        """Add section should create a new section."""
        from router.backends.todoist import projects

        result = await call_tool(
            projects,
            action='add_section',
            project_id='p1',
            section_name='New Section',
        )

        assert 'section' in result
        assert result['section']['name'] == 'New Section'

    async def test_projects_add_section_with_order(self, mock_todoist_api, todoist_env_vars):
        """Add section with order should include it."""
        from router.backends.todoist import projects

        result = await call_tool(
            projects,
            action='add_section',
            project_id='p1',
            section_name='Ordered Section',
            section_order=5,
        )

        assert 'section' in result
        assert 'error' not in result

    async def test_projects_add_section_missing_name_error(self, mock_todoist_api, todoist_env_vars):
        """Add section without name should return error."""
        from router.backends.todoist import projects

        result = await call_tool(projects, action='add_section', project_id='p1')

        assert 'error' in result
        assert 'section_name' in result['error'].lower()

    async def test_projects_delete_section(self, mock_todoist_api, todoist_env_vars):
        """Delete section should remove the section."""
        from router.backends.todoist import projects

        result = await call_tool(projects, action='delete_section', section_id='s1')

        assert result['success'] is True
        assert result['section_id'] == 's1'

    async def test_projects_missing_project_id_error(self, mock_todoist_api, todoist_env_vars):
        """Actions requiring project_id should return error if missing."""
        from router.backends.todoist import projects

        for action in ['get', 'update', 'delete', 'list_sections', 'add_section']:
            result = await call_tool(projects, action=action)
            assert 'error' in result
            assert 'project_id' in result['error'].lower()

    async def test_projects_missing_section_id_error(self, mock_todoist_api, todoist_env_vars):
        """Delete section without section_id should return error."""
        from router.backends.todoist import projects

        result = await call_tool(projects, action='delete_section')

        assert 'error' in result
        assert 'section_id' in result['error'].lower()

    async def test_projects_invalid_action_error(self, mock_todoist_api, todoist_env_vars):
        """Invalid action should return error."""
        from router.backends.todoist import projects

        result = await call_tool(projects, action='invalid_action')

        assert 'error' in result
        assert 'invalid' in result['error'].lower()


class TestAPIErrorHandling:
    """Tests for API error handling."""

    async def test_api_error_returns_error_dict(self, todoist_env_vars):
        """API errors should return error dict."""
        from router.backends.todoist import tasks

        async def mock_request_error(*args, **kwargs):
            return MockResponse(401, text='Unauthorized')

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.request = AsyncMock(side_effect=mock_request_error)

        with patch('router.backends.todoist.httpx.AsyncClient', return_value=mock_client):
            result = await call_tool(tasks, action='list')

        assert 'error' in result
        assert '401' in result['error']

    async def test_api_timeout_returns_error_dict(self, todoist_env_vars):
        """Timeout should return error dict."""
        from router.backends.todoist import tasks

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.request = AsyncMock(side_effect=httpx.TimeoutException('Timeout'))

        with patch('router.backends.todoist.httpx.AsyncClient', return_value=mock_client):
            result = await call_tool(tasks, action='list')

        assert 'error' in result
        assert 'timed out' in result['error'].lower()

    async def test_api_exception_returns_error_dict(self, todoist_env_vars):
        """General exception should return error dict."""
        from router.backends.todoist import tasks

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.request = AsyncMock(side_effect=Exception('Connection failed'))

        with patch('router.backends.todoist.httpx.AsyncClient', return_value=mock_client):
            result = await call_tool(tasks, action='list')

        assert 'error' in result
        assert 'Connection failed' in result['error']
