"""Integration tests for the email backend."""

import pytest


# Helper to call FastMCP tool functions
async def call_tool(tool, **kwargs):
    """Call a FastMCP tool's underlying function."""
    return await tool.fn(**kwargs)


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

        result = await call_tool(get_email, message_id='1')

        assert isinstance(result, dict)
        assert 'id' in result
        assert 'from' in result
        assert 'to' in result
        assert 'subject' in result
        assert 'body' in result
        assert 'date' in result
        assert 'local_time' in result

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
