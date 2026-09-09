"""Integration tests for the email backend."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest


# Helper to call FastMCP tool functions
async def call_tool(tool, **kwargs):
    """Call a FastMCP tool's underlying function."""
    return await tool.fn(**kwargs)


class TestIMAPConnection:
    """Tests for bounded connection failures and cleanup."""

    async def test_greeting_timeout_fails_fast_and_closes_transport(self, monkeypatch):
        from router.backends import email

        async def hang_forever():
            await asyncio.Future()

        transport = MagicMock()
        client = SimpleNamespace(
            protocol=SimpleNamespace(transport=transport),
            wait_hello_from_server=hang_forever,
        )
        constructor = MagicMock(return_value=client)
        monkeypatch.setattr(email, 'IMAP4', constructor)
        monkeypatch.setattr(email, 'IMAP_CONNECT_TIMEOUT_SECONDS', 0.01)

        with pytest.raises(RuntimeError, match='greeting timed out after 0.01s'):
            await email.get_imap_client()

        assert constructor.call_args.kwargs['timeout'] == email.IMAP_COMMAND_TIMEOUT_SECONDS
        transport.abort.assert_called_once_with()

    async def test_connection_failure_has_bridge_diagnostic(self, monkeypatch):
        from router.backends import email

        connection_task = asyncio.get_running_loop().create_future()
        connection_task.set_exception(ConnectionRefusedError())
        client = SimpleNamespace(
            _client_task=connection_task,
            protocol=SimpleNamespace(transport=None),
        )
        monkeypatch.setattr(email, 'IMAP4', MagicMock(return_value=client))

        with pytest.raises(RuntimeError, match='connection failed.*Bridge service is running'):
            await email.get_imap_client()

    async def test_authentication_timeout_fails_fast_and_closes_transport(self, monkeypatch):
        from router.backends import email

        async def hang_forever(*args):
            await asyncio.Future()

        transport = MagicMock()
        client = SimpleNamespace(
            protocol=SimpleNamespace(transport=transport),
            wait_hello_from_server=AsyncMock(),
            login=hang_forever,
        )
        monkeypatch.setattr(email, 'IMAP4', MagicMock(return_value=client))
        monkeypatch.setattr(email, 'IMAP_CONNECT_TIMEOUT_SECONDS', 0.01)

        with pytest.raises(RuntimeError, match='authentication timed out after 0.01s'):
            await email.get_imap_client()

        transport.abort.assert_called_once_with()

    async def test_rejected_login_has_authentication_diagnostic(self, monkeypatch):
        from router.backends import email

        transport = MagicMock()
        client = SimpleNamespace(
            protocol=SimpleNamespace(transport=transport),
            wait_hello_from_server=AsyncMock(),
            login=AsyncMock(return_value=SimpleNamespace(result='NO')),
        )
        monkeypatch.setattr(email, 'IMAP4', MagicMock(return_value=client))

        with pytest.raises(RuntimeError, match='authentication was rejected'):
            await email.get_imap_client()

        transport.abort.assert_called_once_with()

    async def test_tool_error_still_logs_out(self, patch_imap, env_vars):
        from router.backends.email import list_emails

        patch_imap.select = AsyncMock(side_effect=RuntimeError('select failed'))

        with pytest.raises(RuntimeError, match='select failed'):
            await call_tool(list_emails)

        assert patch_imap.logged_in is False


class TestListEmails:
    """Tests for list_emails tool."""

    async def test_list_emails_returns_emails(self, patch_imap, env_vars):
        """Should return list of emails with expected fields."""
        from router.backends.email import list_emails

        result = await call_tool(list_emails, mailbox='INBOX', limit=10)

        assert isinstance(result, list)
        assert len(result) > 0

        # Check email structure
        email = result[0]
        assert 'id' in email
        assert 'from' in email
        assert 'subject' in email
        assert 'date' in email
        assert 'local_time' in email

    async def test_list_emails_respects_limit(self, patch_imap, env_vars):
        """Should respect the limit parameter."""
        from router.backends.email import list_emails

        result = await call_tool(list_emails, limit=2)

        assert len(result) <= 2

    async def test_list_emails_sorted_newest_first(self, patch_imap, env_vars):
        """Should return emails sorted by date, newest first."""
        from router.backends.email import list_emails

        result = await call_tool(list_emails, limit=10)

        # Verify descending date order
        if len(result) >= 2:
            from router.backends.email import parse_email_date
            dates = [parse_email_date(e['date']) for e in result]
            dates = [d for d in dates if d]  # Filter None
            for i in range(len(dates) - 1):
                assert dates[i] >= dates[i + 1], "Emails should be sorted newest first"


class TestSearchEmails:
    """Tests for search_emails tool."""

    async def test_search_emails_by_sender(self, patch_imap, env_vars):
        """Should find emails by sender."""
        from router.backends.email import search_emails

        result = await call_tool(search_emails, query='alice', limit=10)

        assert isinstance(result, list)
        # Mock should return matching emails
        for email in result:
            assert 'id' in email
            assert 'from' in email

    async def test_search_emails_by_subject(self, patch_imap, env_vars):
        """Should find emails by subject."""
        from router.backends.email import search_emails

        result = await call_tool(search_emails, query='meeting', limit=10)

        assert isinstance(result, list)

    async def test_search_emails_respects_limit(self, patch_imap, env_vars):
        """Should respect the limit parameter."""
        from router.backends.email import search_emails

        result = await call_tool(search_emails, query='example', limit=1)

        assert len(result) <= 1

    async def test_search_emails_empty_query_returns_results(self, patch_imap, env_vars):
        """Should handle searches that find nothing gracefully."""
        from router.backends.email import search_emails

        result = await call_tool(search_emails, query='nonexistent12345', limit=10)

        assert isinstance(result, list)


class TestGetEmail:
    """Tests for get_email tool."""

    async def test_get_email_returns_full_content(self, patch_imap, env_vars):
        """Should return full email with body."""
        from router.backends.email import get_email

        patch_imap.fetch = AsyncMock(side_effect=patch_imap.fetch)
        result = await call_tool(get_email, message_id='1')

        assert isinstance(result, dict)
        assert 'id' in result
        assert 'from' in result
        assert 'to' in result
        assert 'subject' in result
        assert 'body' in result
        assert 'date' in result
        assert 'local_time' in result
        patch_imap.fetch.assert_awaited_once_with('1', '(BODY.PEEK[])')

    async def test_get_email_not_found(self, patch_imap, env_vars):
        """Should return error for non-existent message."""
        from router.backends.email import get_email

        result = await call_tool(get_email, message_id='99999')

        assert 'error' in result


class TestSendEmail:
    """Tests for send_email tool."""

    async def test_send_email_success(self, patch_smtp, env_vars):
        """Should send email via SMTP."""
        from router.backends.email import send_email

        result = await call_tool(
            send_email,
            to='recipient@example.com',
            subject='Test Subject',
            body='Test body content'
        )

        assert result['status'] == 'sent'
        assert result['to'] == 'recipient@example.com'
        assert result['subject'] == 'Test Subject'

        # Verify SMTP was called
        assert len(patch_smtp) == 1
        sent = patch_smtp[0]
        assert sent['to'] == 'recipient@example.com'
        assert sent['subject'] == 'Test Subject'

    async def test_send_email_uses_correct_smtp_settings(self, patch_smtp, env_vars):
        """Should use environment SMTP settings."""
        from router.backends.email import send_email

        await call_tool(
            send_email,
            to='recipient@example.com',
            subject='Test',
            body='Body'
        )

        sent = patch_smtp[0]
        assert sent['kwargs']['hostname'] == '127.0.0.1'
        assert sent['kwargs']['port'] == 1025


    async def test_send_email_accepts_optional_display_name(self, patch_smtp, env_vars):
        """Should add a display name without changing the sender address."""
        from router.backends.email import send_email

        result = await call_tool(
            send_email,
            to='recipient@example.com',
            subject='Test Subject',
            body='Test body content',
            from_name='Job Search Agent',
        )

        assert result['status'] == 'sent'
        assert patch_smtp[0]['from'] == 'Job Search Agent <test@example.com>'

    async def test_send_email_rejects_display_name_header_injection(self, patch_smtp, env_vars):
        """Should reject newlines before constructing or sending a header."""
        from router.backends.email import send_email

        with pytest.raises(ValueError, match='newline'):
            await call_tool(
                send_email,
                to='recipient@example.com',
                subject='Test Subject',
                body='Test body content',
                from_name='Job Search Agent\r\nBcc: attacker@example.com',
            )

        assert patch_smtp == []


class TestHelperFunctions:
    """Tests for email helper functions."""

    def test_decode_mime_header_plain(self):
        """Should decode plain headers."""
        from router.backends.email import decode_mime_header

        result = decode_mime_header('Simple Subject')
        assert result == 'Simple Subject'

    def test_decode_mime_header_empty(self):
        """Should handle empty headers."""
        from router.backends.email import decode_mime_header

        result = decode_mime_header('')
        assert result == ''

        result = decode_mime_header(None)
        assert result == ''

    def test_parse_email_date_valid(self):
        """Should parse valid email dates."""
        from router.backends.email import parse_email_date

        result = parse_email_date('Mon, 27 Jan 2026 10:00:00 -0800')
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 27

    def test_parse_email_date_invalid(self):
        """Should return None for invalid dates."""
        from router.backends.email import parse_email_date

        result = parse_email_date('not a date')
        assert result is None

        result = parse_email_date('')
        assert result is None

        result = parse_email_date(None)
        assert result is None

    def test_format_local_time_valid(self):
        """Should format dates to local time string."""
        from router.backends.email import format_local_time

        result = format_local_time('Mon, 27 Jan 2026 10:00:00 +0000')
        assert result is not None
        assert '2026-01-27' in result

    def test_format_local_time_invalid(self):
        """Should return None for invalid dates."""
        from router.backends.email import format_local_time

        result = format_local_time('invalid')
        assert result is None

    def test_format_local_time_uses_configured_timezone(self):
        """Regression: local_time must reflect LOCAL_TIMEZONE, not host TZ.

        On Oxnard the host runs in UTC, so a header with ``-0700`` was
        previously rendered as the UTC wall-clock (16:14) rather than
        the canonical consumer's expected America/Los_Angeles wall-clock
        (09:14). This pins the corrected behaviour for message 7521.
        """
        from router.backends import email

        previous_name = email.LOCAL_TIMEZONE_NAME
        previous_tz = email.LOCAL_TIMEZONE
        try:
            email.LOCAL_TIMEZONE_NAME = 'America/Los_Angeles'
            email.LOCAL_TIMEZONE = ZoneInfo('America/Los_Angeles')
            result = email.format_local_time(
                'Wed, 09 Sep 2026 09:14:06 -0700'
            )
        finally:
            email.LOCAL_TIMEZONE_NAME = previous_name
            email.LOCAL_TIMEZONE = previous_tz

        assert result == '2026-09-09 09:14'

    def test_format_local_time_defaults_to_utc(self, monkeypatch):
        """Without an env override, local_time should render UTC."""
        from importlib import reload
        from router.backends import email as email_module

        monkeypatch.delenv('PROTON_BRIDGE_LOCAL_TIMEZONE', raising=False)
        reload(email_module)

        result = email_module.format_local_time(
            'Wed, 09 Sep 2026 09:14:06 -0700'
        )
        assert result == '2026-09-09 16:14'

    def test_format_local_time_invalid_timezone_falls_back_to_utc(
        self, monkeypatch
    ):
        """An unrecognised TZ name must not crash the service."""
        from importlib import reload
        from router.backends import email as email_module

        monkeypatch.setenv('PROTON_BRIDGE_LOCAL_TIMEZONE', 'Mars/Olympus')
        reload(email_module)

        assert email_module.LOCAL_TIMEZONE_NAME == 'Mars/Olympus'
        # Falls back to UTC for rendering, so no crash and stable output
        result = email_module.format_local_time(
            'Wed, 09 Sep 2026 09:14:06 -0700'
        )
        assert result == '2026-09-09 16:14'

    def test_sort_emails_by_date(self):
        """Should sort emails newest first."""
        from router.backends.email import sort_emails_by_date

        emails = [
            {'date': 'Mon, 27 Jan 2026 10:00:00 +0000'},
            {'date': 'Tue, 28 Jan 2026 10:00:00 +0000'},
            {'date': 'Sun, 26 Jan 2026 10:00:00 +0000'},
        ]

        result = sort_emails_by_date(emails, newest_first=True)

        assert result[0]['date'] == 'Tue, 28 Jan 2026 10:00:00 +0000'
        assert result[2]['date'] == 'Sun, 26 Jan 2026 10:00:00 +0000'

    def test_sort_emails_by_date_handles_naive_dates(self):
        """Regression: tz-naive Date headers must not crash sorting.

        Mixing offset-aware and offset-naive datetimes raises
        ``TypeError: can't compare offset-naive and offset-aware datetimes``.
        The live ``list_emails`` tool was returning empty for limits >= 40
        because the fetch window then included messages whose Date header
        arrived without a timezone. Sorting must coerce every key to a
        timezone-aware datetime.
        """
        from router.backends.email import sort_emails_by_date

        emails = [
            {'date': 'Wed, 09 Sep 2026 17:54:56 +0000'},  # aware
            {'date': 'Wed, 09 Sep 2026 14:00:11'},         # naive
            {'date': 'Wed, 09 Sep 2026 16:12:35 +0000'},  # aware
            {'date': 'not a date'},                        # unparseable
        ]

        result = sort_emails_by_date(emails, newest_first=True)

        # Should not raise and should keep all entries
        assert len(result) == 4
        # Newest aware date is first
        assert result[0]['date'] == 'Wed, 09 Sep 2026 17:54:56 +0000'
        # The naive-date message lands ahead of the second aware date
        # because it was treated as UTC, which sorts between them
        aware_dates = [e['date'] for e in result if '+0000' in e['date']]
        assert aware_dates == [
            'Wed, 09 Sep 2026 17:54:56 +0000',
            'Wed, 09 Sep 2026 16:12:35 +0000',
        ]
        # The naive-date message is present and not at the bottom
        assert {'date': 'Wed, 09 Sep 2026 14:00:11'} in result
        # Unparseable dates fall back to the epoch and sort to the end
        assert result[-1]['date'] == 'not a date'

    def test_parse_email_date_returns_aware_datetime(self):
        """Regression: parse_email_date must always return tz-aware UTC.

        Without this, the sort key for a tz-naive Date header raises
        TypeError when compared against the aware epoch fallback.
        """
        from router.backends.email import parse_email_date

        aware = parse_email_date('Wed, 09 Sep 2026 17:54:56 +0000')
        naive_input = parse_email_date('Wed, 09 Sep 2026 14:00:11')
        epoch = parse_email_date('1 Jan 1970 00:00:00 +0000')

        assert aware is not None and aware.tzinfo is not None
        assert naive_input is not None and naive_input.tzinfo is not None
        assert epoch is not None and epoch.tzinfo is not None
        # Comparable without raising
        assert naive_input < aware

    async def test_list_emails_handles_mixed_naive_and_aware_dates(
        self, patch_imap, env_vars
    ):
        """Regression: list_emails must not crash on tz-naive Date headers.

        With a wider fetch window (limit * 2), the result inevitably
        contains messages whose Date header arrived without a timezone.
        Earlier versions of the code raised TypeError during the final
        ``sort_emails_by_date(emails)[:limit]`` and the tool returned
        ``[]`` (the FastMCP layer swallowed the exception). This test
        pins the corrected behaviour.
        """
        from router.backends.email import list_emails

        # Inject an extra message whose Date header has no timezone
        naive_email = {
            'id': '4',
            'from': 'naive@example.com',
            'subject': 'No timezone',
            'date': 'Wed, 09 Sep 2026 14:00:11',
            'body': 'x' * 600,
        }
        patch_imap.emails[naive_email['id']] = naive_email

        result = await call_tool(list_emails, mailbox='INBOX', limit=20)

        assert isinstance(result, list)
        assert len(result) >= 4
        # The naive-date message is among the returned headers
        ids = [e['id'] for e in result]
        assert '4' in ids

    async def test_list_emails_limit_above_fetch_window_does_not_crash(
        self, patch_imap, env_vars
    ):
        """Regression: a limit that fetches many naive-date messages.

        In production, ``limit >= 40`` consistently surfaced an empty
        result because ProtonMail Bridge returns at least one
        timezone-naive Date header once the fetch window exceeds 80
        messages, and ``sort_emails_by_date`` then raised TypeError.
        """
        from router.backends.email import list_emails

        # Seed enough messages that some have naive Date headers
        for i in range(5, 45):
            # Half aware, half naive — mirrors the live mailbox mix
            date = (
                f'Tue, {28 + (i % 5):02d} Jan 2026 10:00:00 +0000'
                if i % 2 == 0
                else f'Tue, {28 + (i % 5):02d} Jan 2026 10:00:00'
            )
            patch_imap.emails[str(i)] = {
                'id': str(i),
                'from': f'sender{i}@example.com',
                'subject': f'Message {i}',
                'date': date,
                'body': 'x' * 600,
            }

        for limit in (20, 40, 50, 100):
            result = await call_tool(list_emails, mailbox='INBOX', limit=limit)
            assert isinstance(result, list), f'limit={limit} returned non-list'
            assert len(result) > 0, f'limit={limit} returned empty list'
            assert len(result) <= limit, f'limit={limit} returned too many'
