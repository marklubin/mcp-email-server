"""Integration tests for the MCP router."""

import pytest
from unittest.mock import patch, MagicMock


# Helper to call FastMCP tool functions
def call_tool(tool, **kwargs):
    """Call a FastMCP tool's underlying function."""
    return tool.fn(**kwargs)


class TestHealthTool:
    """Tests for health tool."""

    def test_health_returns_status(self):
        """Should return health status with backends."""
        from router.server import health

        result = call_tool(health)

        assert result['status'] == 'ok'
        assert 'backends' in result
        assert 'email' in result['backends']


class TestLogsTool:
    """Tests for logs tool."""

    def test_logs_default_service(self):
        """Should default to mcp-router service."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout='Jan 28 12:00:00 server log line',
                stderr=''
            )

            from router.server import logs
            result = call_tool(logs)

            assert result['service'] == 'mcp-router'
            assert result['lines'] == 50
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert 'mcp-router@mark' in call_args

    def test_logs_custom_lines(self):
        """Should respect custom line count."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout='log output',
                stderr=''
            )

            from router.server import logs
            result = call_tool(logs, lines=100)

            assert result['lines'] == 100
            call_args = mock_run.call_args[0][0]
            assert '100' in call_args

    def test_logs_max_lines_capped(self):
        """Should cap lines at 500."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout='log output',
                stderr=''
            )

            from router.server import logs
            result = call_tool(logs, lines=1000)

            assert result['lines'] == 500

    def test_logs_min_lines_capped(self):
        """Should ensure at least 1 line."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout='log output',
                stderr=''
            )

            from router.server import logs
            result = call_tool(logs, lines=-5)

            assert result['lines'] == 1

    def test_logs_cloudflared_service(self):
        """Should support cloudflared service."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout='cloudflared log',
                stderr=''
            )

            from router.server import logs
            result = call_tool(logs, service='cloudflared')

            assert result['service'] == 'cloudflared'
            call_args = mock_run.call_args[0][0]
            assert 'cloudflared' in call_args

    def test_logs_invalid_service(self):
        """Should return error for unknown service."""
        from router.server import logs
        result = call_tool(logs, service='unknown-service')

        assert 'error' in result
        assert 'Unknown service' in result['error']

    def test_logs_includes_output(self):
        """Should include journalctl output."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout='Jan 28 12:00:00 test log line\nJan 28 12:00:01 another line',
                stderr=''
            )

            from router.server import logs
            result = call_tool(logs)

            assert 'logs' in result
            assert 'test log line' in result['logs']

    def test_logs_timeout_handling(self):
        """Should handle subprocess timeout."""
        import subprocess

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd='journalctl', timeout=10)

            from router.server import logs
            result = call_tool(logs)

            assert 'error' in result
            assert 'Timeout' in result['error']

    def test_logs_exception_handling(self):
        """Should handle general exceptions."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = Exception('Some error')

            from router.server import logs
            result = call_tool(logs)

            assert 'error' in result


class TestAuthMiddleware:
    """Tests for authentication middleware."""

    @pytest.fixture
    def mock_app(self):
        """Create mock ASGI app."""
        async def app(scope, receive, send):
            await send({
                'type': 'http.response.start',
                'status': 200,
                'headers': [(b'content-type', b'application/json')]
            })
            await send({'type': 'http.response.body', 'body': b'{"ok": true}'})
        return app

    async def test_auth_allows_valid_secret(self, mock_app):
        """Should allow requests with valid secret."""
        from router.server import AuthMiddleware

        middleware = AuthMiddleware(mock_app, 'test-secret')

        responses = []
        async def mock_send(msg):
            responses.append(msg)

        scope = {
            'type': 'http',
            'headers': [(b'x-mcp-secret', b'test-secret')]
        }

        await middleware(scope, None, mock_send)

        assert any(r.get('status') == 200 for r in responses)

    async def test_auth_rejects_invalid_secret(self, mock_app):
        """Should reject requests with invalid secret."""
        from router.server import AuthMiddleware

        middleware = AuthMiddleware(mock_app, 'correct-secret')

        responses = []
        async def mock_send(msg):
            responses.append(msg)

        scope = {
            'type': 'http',
            'headers': [(b'x-mcp-secret', b'wrong-secret')]
        }

        await middleware(scope, None, mock_send)

        assert any(r.get('status') == 401 for r in responses)

    async def test_auth_rejects_missing_secret(self, mock_app):
        """Should reject requests without secret header."""
        from router.server import AuthMiddleware

        middleware = AuthMiddleware(mock_app, 'test-secret')

        responses = []
        async def mock_send(msg):
            responses.append(msg)

        scope = {
            'type': 'http',
            'headers': []
        }

        await middleware(scope, None, mock_send)

        assert any(r.get('status') == 401 for r in responses)

    async def test_auth_skipped_when_no_secret_configured(self, mock_app):
        """Should skip auth when no secret is configured."""
        from router.server import AuthMiddleware

        middleware = AuthMiddleware(mock_app, '')  # Empty secret

        responses = []
        async def mock_send(msg):
            responses.append(msg)

        scope = {
            'type': 'http',
            'headers': []
        }

        await middleware(scope, None, mock_send)

        assert any(r.get('status') == 200 for r in responses)

    async def test_auth_passes_non_http_requests(self, mock_app):
        """Should pass through non-HTTP requests."""
        from router.server import AuthMiddleware

        middleware = AuthMiddleware(mock_app, 'test-secret')

        called = []
        async def tracking_app(scope, receive, send):
            called.append(scope['type'])

        middleware = AuthMiddleware(tracking_app, 'test-secret')

        scope = {'type': 'websocket', 'headers': []}

        await middleware(scope, None, lambda x: None)

        assert 'websocket' in called
