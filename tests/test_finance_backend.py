"""Tests for the read-only finance service gateway."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from router.backends import finance


async def call_tool(tool, **kwargs):
    """Call a FastMCP tool's underlying function."""
    return await tool.fn(**kwargs)


def mock_client(response=None, side_effect=None):
    """Create an async context-manager client with a mocked GET."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=response, side_effect=side_effect)
    return client


@pytest.fixture(autouse=True)
def finance_env(monkeypatch):
    monkeypatch.setenv('FINANCE_SERVICE_URL', 'http://127.0.0.1:8090')
    monkeypatch.setenv('FINANCE_SERVICE_TOKEN', 'service-secret')


class TestToolSurface:
    async def test_exposes_only_read_only_finance_tools(self):
        tools = await finance.mcp.get_tools()

        assert set(tools) == {
            'summary',
            'accounts',
            'transactions',
            'changes',
            'sync_status',
        }

    async def test_router_mount_uses_finance_prefix(self):
        from router.server import router

        tools = await router.get_tools()
        finance_tools = {name for name in tools if name.startswith('finance_')}

        assert finance_tools == {
            'finance_summary',
            'finance_accounts',
            'finance_transactions',
            'finance_changes',
            'finance_sync_status',
        }


class TestConfiguration:
    async def test_requires_service_token(self, monkeypatch):
        monkeypatch.delenv('FINANCE_SERVICE_TOKEN')

        result = await call_tool(finance.finance_sync_status)

        assert result['code'] == 'not_configured'

    async def test_rejects_non_loopback_service_url(self, monkeypatch):
        monkeypatch.setenv('FINANCE_SERVICE_URL', 'https://finance.example.com')

        result = await call_tool(finance.finance_sync_status)

        assert result['code'] == 'invalid_configuration'


class TestRequests:
    async def test_summary_clamps_days_and_authenticates(self):
        client = mock_client(httpx.Response(200, json={'data_as_of': '2026-08-29T12:00:00Z'}))

        with patch.object(finance.httpx, 'AsyncClient', return_value=client):
            result = await call_tool(finance.finance_summary, days=999)

        assert result['data_as_of'] == '2026-08-29T12:00:00Z'
        request = client.get.call_args
        assert request.args[0] == 'http://127.0.0.1:8090/v1/summary'
        assert request.kwargs['params'] == {'days': 366}
        assert request.kwargs['headers']['Authorization'] == 'Bearer service-secret'

    async def test_accounts_clamps_limit(self):
        client = mock_client(httpx.Response(200, json={'accounts': []}))

        with patch.object(finance.httpx, 'AsyncClient', return_value=client):
            result = await call_tool(
                finance.finance_accounts,
                include_inactive=True,
                limit=999,
            )

        assert result == {'accounts': []}
        assert client.get.call_args.kwargs['params'] == {
            'include_inactive': True,
            'limit': 100,
        }

    async def test_transactions_forwards_bounded_filters(self):
        client = mock_client(httpx.Response(200, json={'transactions': []}))

        with patch.object(finance.httpx, 'AsyncClient', return_value=client):
            result = await call_tool(
                finance.finance_transactions,
                start_date='2026-08-01',
                end_date='2026-08-29',
                account_id='account-1',
                limit=800,
            )

        assert result == {'transactions': []}
        assert client.get.call_args.kwargs['params'] == {
            'start_date': '2026-08-01',
            'end_date': '2026-08-29',
            'account_id': 'account-1',
            'limit': 500,
        }

    async def test_changes_forwards_cursor_and_limit(self):
        response = {'added': [], 'modified': [], 'removed': [], 'next_cursor': 'next'}
        client = mock_client(httpx.Response(200, json=response))

        with patch.object(finance.httpx, 'AsyncClient', return_value=client):
            result = await call_tool(finance.finance_changes, cursor='current', limit=20)

        assert result == response
        assert client.get.call_args.kwargs['params'] == {
            'cursor': 'current',
            'limit': 20,
        }

    async def test_sync_status_uses_read_only_status_endpoint(self):
        response = {'status': 'ok', 'data_as_of': '2026-08-29T12:00:00Z'}
        client = mock_client(httpx.Response(200, json=response))

        with patch.object(finance.httpx, 'AsyncClient', return_value=client):
            result = await call_tool(finance.finance_sync_status)

        assert result == response
        assert client.get.call_args.args[0].endswith('/v1/sync-status')
        assert client.get.await_count == 1


class TestBounds:
    async def test_rejects_invalid_transaction_date(self):
        result = await call_tool(
            finance.finance_transactions,
            start_date='08/01/2026',
            end_date='2026-08-29',
        )

        assert result['code'] == 'invalid_date'

    async def test_rejects_reversed_transaction_range(self):
        result = await call_tool(
            finance.finance_transactions,
            start_date='2026-08-30',
            end_date='2026-08-29',
        )

        assert result['code'] == 'invalid_date_range'

    async def test_rejects_transaction_range_over_366_days(self):
        result = await call_tool(
            finance.finance_transactions,
            start_date='2025-01-01',
            end_date='2026-01-02',
        )

        assert result['code'] == 'date_range_too_large'

    async def test_rejects_upstream_account_limit_violation(self):
        response = {'accounts': [{'id': str(index)} for index in range(3)]}
        client = mock_client(httpx.Response(200, json=response))

        with patch.object(finance.httpx, 'AsyncClient', return_value=client):
            result = await call_tool(finance.finance_accounts, limit=2)

        assert result['code'] == 'service_limit_violation'

    async def test_rejects_upstream_total_change_limit_violation(self):
        response = {
            'added': [{}, {}],
            'modified': [{}],
            'removed': [],
        }
        client = mock_client(httpx.Response(200, json=response))

        with patch.object(finance.httpx, 'AsyncClient', return_value=client):
            result = await call_tool(finance.finance_changes, limit=2)

        assert result['code'] == 'service_limit_violation'

    async def test_rejects_oversized_cursor_without_calling_service(self):
        result = await call_tool(
            finance.finance_changes,
            cursor='x' * (finance.MAX_CURSOR_LENGTH + 1),
        )

        assert result['code'] == 'invalid_cursor'


class TestErrors:
    async def test_maps_timeout_to_structured_error(self):
        request = httpx.Request('GET', 'http://127.0.0.1:8090/v1/sync-status')
        client = mock_client(side_effect=httpx.ReadTimeout('slow', request=request))

        with patch.object(finance.httpx, 'AsyncClient', return_value=client):
            result = await call_tool(finance.finance_sync_status)

        assert result == {
            'error': 'Finance service request timed out',
            'code': 'service_timeout',
        }

    async def test_maps_auth_failure_without_echoing_body(self):
        client = mock_client(httpx.Response(401, text='secret diagnostic'))

        with patch.object(finance.httpx, 'AsyncClient', return_value=client):
            result = await call_tool(finance.finance_sync_status)

        assert result['code'] == 'service_auth_failed'
        assert 'secret diagnostic' not in str(result)

    async def test_returns_short_service_error_detail(self):
        client = mock_client(httpx.Response(503, json={'detail': 'database unavailable'}))

        with patch.object(finance.httpx, 'AsyncClient', return_value=client):
            result = await call_tool(finance.finance_sync_status)

        assert result == {
            'error': 'Finance service returned an error',
            'code': 'service_error',
            'status': 503,
            'detail': 'database unavailable',
        }

    async def test_rejects_non_object_json(self):
        client = mock_client(httpx.Response(200, json=[]))

        with patch.object(finance.httpx, 'AsyncClient', return_value=client):
            result = await call_tool(finance.finance_sync_status)

        assert result['code'] == 'invalid_service_response'
