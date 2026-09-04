"""Tests for the Google Tasks preparation-board backend."""

from unittest.mock import patch

import httpx
import pytest

from router.backends import gtasks


async def call_tool(tool, **kwargs):
    """Call a FastMCP tool's underlying function."""
    return await tool.fn(**kwargs)


class MockResponse:
    def __init__(self, status_code: int, json_data=None):
        self.status_code = status_code
        self._json = json_data
        self.content = b'x' if json_data is not None else b''
        self.text = str(json_data or '')

    def json(self):
        if self._json is None:
            raise ValueError('no body')
        return self._json


class FakeTasksApi:
    """In-memory Google Tasks: lists, tasks, subtasks, move, patch, token refresh."""

    def __init__(self, lists: dict[str, str], token_status: int = 200):
        self.lists = [{'id': list_id, 'title': title} for list_id, title in lists.items()]
        self.tasks: dict[str, list[dict]] = {list_id: [] for list_id in lists}
        self.calls: list[tuple[str, str]] = []
        self.token_status = token_status
        self.token_calls = 0
        self._counter = 0

    async def post(self, url, data=None, timeout=None):
        assert url == gtasks.TOKEN_URI
        self.token_calls += 1
        if self.token_status != 200:
            return MockResponse(self.token_status, {'error': 'invalid_grant'})
        assert data['grant_type'] == 'refresh_token'
        return MockResponse(200, {'access_token': 'at-1', 'expires_in': 3600})

    async def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        assert headers['Authorization'] == 'Bearer at-1'
        path = url[len(gtasks.API):]
        self.calls.append((method, path))
        params = params or {}
        if path == '/users/@me/lists':
            return MockResponse(200, {'items': self.lists})
        parts = path.strip('/').split('/')
        list_id = parts[1]
        if list_id not in self.tasks:
            return MockResponse(404, {'error': {'message': 'list not found'}})
        items = self.tasks[list_id]
        if method == 'GET':
            return MockResponse(200, {'items': [dict(item) for item in items]})
        if method == 'POST' and parts[-1] == 'tasks':
            self._counter += 1
            task = {'id': f't{self._counter}', 'status': 'needsAction', 'position': f'{100000 - self._counter:05d}', **(json or {})}
            if params.get('parent'):
                task['parent'] = params['parent']
            items.append(task)
            return MockResponse(200, dict(task))
        if method == 'PATCH':
            task = next(item for item in items if item['id'] == parts[-1])
            task.update(json or {})
            return MockResponse(200, dict(task))
        if method == 'POST' and parts[-1] == 'move':
            task = next(item for item in items if item['id'] == parts[-2])
            siblings = [item for item in items if item.get('parent') == task.get('parent') and item['id'] != task['id']]
            if params.get('previous'):
                previous = next(item for item in items if item['id'] == params['previous'])
                task['position'] = previous['position'] + 'z'
            else:
                lowest = min((s['position'] for s in siblings), default='50000')
                task['position'] = '0' + lowest
            return MockResponse(200, dict(task))
        raise AssertionError(f'unexpected {method} {path}')

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


LISTS = {'L1': 'Coding', 'L2': 'System design', 'L3': 'AI systems', 'L4': 'Behavioral and projects'}
UNITS = [
    {'lane': 'coding', 'id': 'COD-01', 'title': 'Hash maps', 'status': 'not_started', 'depth': 2, 'max_depth': 3, 'activation': 'core', 'work': 'Solve Subarray Sum.', 'evidence': 'Prior pass.'},
    {'lane': 'coding', 'id': 'COD-03', 'title': 'Sliding windows', 'status': 'not_started', 'depth': 1, 'max_depth': 3, 'activation': 'core', 'work': 'Walk through LSWRC.', 'evidence': ''},
    {'lane': 'coding', 'id': 'COD-09', 'title': 'Old topic', 'status': 'solid', 'depth': 3, 'max_depth': 3, 'activation': 'core', 'work': '', 'evidence': 'Done.'},
    {'lane': 'behavioral', 'id': 'BEH-01', 'title': 'Story one', 'status': 'not_started', 'depth': 1, 'max_depth': 3, 'activation': 'core', 'work': 'Tell it.', 'evidence': ''},
]


@pytest.fixture(autouse=True)
def gtasks_env(monkeypatch):
    monkeypatch.setenv('GOOGLE_TASKS_CLIENT_ID', 'cid')
    monkeypatch.setenv('GOOGLE_TASKS_CLIENT_SECRET', 'csecret')
    monkeypatch.setenv('GOOGLE_TASKS_REFRESH_TOKEN', 'rt')
    monkeypatch.delenv('GOOGLE_TASKS_LANES', raising=False)
    gtasks._token_cache.update({'token': None, 'expires': 0.0})


@pytest.fixture
def api():
    fake = FakeTasksApi(LISTS)
    with patch('router.backends.gtasks.httpx.AsyncClient', return_value=fake):
        yield fake


async def seeded(api):
    result = await call_tool(gtasks.seed, units=UNITS, selected='COD-03', dry_run=False)
    assert 'error' not in result, result
    return result


class TestToolSurface:
    async def test_exposes_only_board_tools_and_no_delete(self):
        tools = await gtasks.mcp.get_tools()
        assert set(tools) == {'lists', 'board', 'tasks', 'seed'}
        assert 'delete' not in gtasks.TASK_ACTIONS

    async def test_router_mount_uses_gtasks_prefix(self):
        from router.server import router

        tools = await router.get_tools()
        assert {name for name in tools if name.startswith('gtasks_')} == {
            'gtasks_lists',
            'gtasks_board',
            'gtasks_tasks',
            'gtasks_seed',
        }


class TestConfiguration:
    async def test_not_configured_is_a_typed_error(self, monkeypatch, api):
        monkeypatch.delenv('GOOGLE_TASKS_REFRESH_TOKEN')
        result = await call_tool(gtasks.lists)
        assert result['code'] == 'not_configured'
        assert api.calls == []

    async def test_refused_refresh_token_is_auth_failed(self):
        fake = FakeTasksApi(LISTS, token_status=400)
        with patch('router.backends.gtasks.httpx.AsyncClient', return_value=fake):
            result = await call_tool(gtasks.board)
        assert result['code'] == 'auth_failed'
        assert 're-run the gtasks authorization' in result['error']

    async def test_access_token_is_cached_across_calls(self, api):
        await call_tool(gtasks.lists)
        await call_tool(gtasks.lists)
        assert api.token_calls == 1

    async def test_lane_titles_override_from_env(self, monkeypatch):
        monkeypatch.setenv('GOOGLE_TASKS_LANES', '{"coding": "Prep · Coding", "bogus": "x"}')
        titles = gtasks.lane_titles()
        assert titles['coding'] == 'Prep · Coding'
        assert 'bogus' not in titles
        assert titles['behavioral'] == 'Behavioral and projects'


class TestSeedAndBoard:
    async def test_dry_run_creates_nothing(self, api):
        result = await call_tool(gtasks.seed, units=UNITS, selected='COD-03')
        assert result['dry_run'] is True
        assert result['created'] == 4
        assert all(call[0] == 'GET' for call in api.calls)

    async def test_seed_then_board_derives_next_unit_and_depth(self, api):
        result = await seeded(api)
        assert result['created'] == 4 and result['skipped'] == 0
        view = await call_tool(gtasks.board)
        coding = next(lane for lane in view['lanes'] if lane['key'] == 'coding')
        assert coding['open'] == 2 and coding['complete'] == 1
        assert coding['next']['unit'] == 'COD-03'  # selected unit moved to the top
        assert coding['next']['current_depth'] == 1
        assert coding['next']['current_work'].startswith('Depth 1 · breadth: Walk through LSWRC.')
        hash_maps = next(unit for unit in coding['units'] if unit['unit'] == 'COD-01')
        assert hash_maps['current_depth'] == 2 and hash_maps['depths_done'] == 1
        old = next(unit for unit in coding['units'] if unit['unit'] == 'COD-09')
        assert old['done'] and old['depths_done'] == 3
        assert 'Evidence: Done.' in old['notes']

    async def test_seed_leaves_units_in_curriculum_order_and_depths_in_depth_order(self, api):
        await seeded(api)
        view = await call_tool(gtasks.board, lane='coding')
        titles = [unit['unit'] for unit in view['lanes'][0]['units']]
        assert titles == ['COD-03', 'COD-01', 'COD-09']  # selected first, then payload order
        for unit in view['lanes'][0]['units']:
            depths = [gtasks._depth_number(d['title']) for d in unit['depths']]
            assert depths == sorted(depths)

    async def test_seed_rerun_repairs_scrambled_order(self, api):
        await seeded(api)
        # scramble: move COD-09 to the top and a depth-3 subtask above depth 1
        cod09 = next(t for t in api.tasks['L1'] if t.get('title', '').startswith('COD-09'))
        cod09['position'] = '00000'
        cod01 = next(t for t in api.tasks['L1'] if t.get('title', '').startswith('COD-01'))
        depth3 = next(t for t in api.tasks['L1'] if t.get('parent') == cod01['id'] and t['title'].startswith('Depth 3'))
        depth3['position'] = '00000'
        result = await call_tool(gtasks.seed, units=UNITS, selected='COD-03', dry_run=False)
        assert result['created'] == 0
        view = await call_tool(gtasks.board, lane='coding')
        assert [unit['unit'] for unit in view['lanes'][0]['units']] == ['COD-03', 'COD-01', 'COD-09']
        hash_maps = next(unit for unit in view['lanes'][0]['units'] if unit['unit'] == 'COD-01')
        assert [gtasks._depth_number(d['title']) for d in hash_maps['depths']] == [1, 2, 3]
        assert hash_maps['current_depth'] == 2

    async def test_seed_is_idempotent(self, api):
        await seeded(api)
        again = await call_tool(gtasks.seed, units=UNITS, selected='COD-03', dry_run=False)
        assert again['created'] == 0 and again['skipped'] == 4

    async def test_board_reports_missing_lane_list_without_hiding_others(self):
        fake = FakeTasksApi({'L1': 'Coding'})
        with patch('router.backends.gtasks.httpx.AsyncClient', return_value=fake):
            view = await call_tool(gtasks.board)
        by_key = {lane['key']: lane for lane in view['lanes']}
        assert 'error' not in by_key['coding']
        assert by_key['behavioral']['code'] == 'not_found'
        assert 'Coding' in by_key['behavioral']['existing_lists']

    async def test_board_rejects_unknown_lane(self, api):
        result = await call_tool(gtasks.board, lane='warm_paths')
        assert result['code'] == 'invalid_argument'


class TestTaskActions:
    async def test_resolve_by_unit_prefix_complete_reopen_and_note(self, api):
        await seeded(api)
        got = await call_tool(gtasks.tasks, action='get', list='coding', task='COD-03')
        assert got['task']['title'] == 'COD-03 - Sliding windows'
        assert len(got['subtasks']) == 3
        depth_id = got['subtasks'][0]['id']
        done = await call_tool(gtasks.tasks, action='complete', list='Coding', task=depth_id)
        assert done['task']['status'] == 'completed'
        view = await call_tool(gtasks.board, lane='coding')
        assert view['lanes'][0]['next']['current_depth'] == 2
        reopened = await call_tool(gtasks.tasks, action='reopen', list='L1', task=depth_id)
        assert reopened['task']['status'] == 'needsAction'
        noted = await call_tool(gtasks.tasks, action='note', list='coding', task='COD-03', notes='partial session')
        assert noted['task']['notes'].endswith(': partial session')
        assert 'Imported from the curriculum' in noted['task']['notes']

    async def test_move_top_changes_next_unit(self, api):
        await seeded(api)
        moved = await call_tool(gtasks.tasks, action='move', list='coding', task='COD-01', top=True)
        assert moved['task']['id']
        view = await call_tool(gtasks.board, lane='coding')
        assert view['lanes'][0]['next']['unit'] == 'COD-01'

    async def test_add_revisit_subtask_under_unit(self, api):
        await seeded(api)
        added = await call_tool(gtasks.tasks, action='add', list='coding', title='Revisit: pointer invariant', parent='COD-03')
        assert added['task']['parent']
        got = await call_tool(gtasks.tasks, action='get', list='coding', task='COD-03')
        assert any(sub['title'].startswith('Revisit:') for sub in got['subtasks'])

    async def test_ambiguous_and_missing_refs_are_typed_errors(self, api):
        await seeded(api)
        missing = await call_tool(gtasks.tasks, action='get', list='coding', task='COD-77')
        assert missing['code'] == 'not_found'
        ambiguous = await call_tool(gtasks.tasks, action='get', list='coding', task='COD')
        assert ambiguous['code'] in {'not_found', 'ambiguous'}
        bad_list = await call_tool(gtasks.tasks, action='list', list='Warm paths')
        assert bad_list['code'] == 'not_found' and 'existing_lists' in bad_list

    async def test_invalid_action_and_due_are_rejected_before_any_call(self, api):
        bad = await call_tool(gtasks.tasks, action='delete', list='coding', task='COD-03')
        assert bad['code'] == 'invalid_argument'
        bad_due = await call_tool(gtasks.tasks, action='add', list='coding', title='x', due='next week')
        assert bad_due['code'] == 'invalid_argument'
        assert api.calls == []

    async def test_quota_errors_are_retried_with_backoff(self, api, monkeypatch):
        await seeded(api)
        real = api.request
        state = {'calls': 0}

        async def flaky(method, url, **kwargs):
            state['calls'] += 1
            if state['calls'] <= 2:
                return MockResponse(403, {'error': {'message': 'Quota Exceeded', 'errors': [{'reason': 'quotaExceeded'}]}})
            return await real(method, url, **kwargs)

        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(gtasks.asyncio, 'sleep', fake_sleep)
        api.request = flaky
        result = await call_tool(gtasks.tasks, action='get', list='coding', task='COD-03')
        assert 'error' not in result
        assert sleeps == [2, 4]

    async def test_persistent_quota_error_is_reported_after_retries(self, api, monkeypatch):
        async def always(method, url, **kwargs):
            return MockResponse(403, {'error': {'message': 'Quota Exceeded'}})

        async def fake_sleep(seconds):
            return None

        monkeypatch.setattr(gtasks.asyncio, 'sleep', fake_sleep)
        await call_tool(gtasks.lists)  # warm the token
        api.request = always
        result = await call_tool(gtasks.lists)
        assert result['code'] == 'upstream_error' and result['status'] == 403

    async def test_upstream_timeout_is_reported(self, api):
        async def boom(*args, **kwargs):
            raise httpx.TimeoutException('slow')

        await call_tool(gtasks.lists)  # warms the token
        api.request = boom
        result = await call_tool(gtasks.lists)
        assert result['code'] == 'timeout'
