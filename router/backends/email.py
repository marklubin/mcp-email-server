"""Email backend for ProtonMail Bridge."""

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from email.header import decode_header
from email import message_from_bytes
from email.utils import formataddr, parsedate_to_datetime

import html2text
from fastmcp import FastMCP
from aioimaplib import IMAP4

_html_converter = html2text.HTML2Text()
_html_converter.body_width = 0
_html_converter.ignore_images = True
_html_converter.ignore_emphasis = False
_html_converter.protect_links = True

IMAP_HOST = os.environ.get('PROTON_BRIDGE_HOST', '127.0.0.1')
IMAP_PORT = int(os.environ.get('PROTON_BRIDGE_IMAP_PORT', '1143'))
IMAP_USER = os.environ.get('PROTON_BRIDGE_USER', '')
IMAP_PASS = os.environ.get('PROTON_BRIDGE_PASSWORD', '')
IMAP_CONNECT_TIMEOUT_SECONDS = float(os.environ.get('PROTON_BRIDGE_IMAP_CONNECT_TIMEOUT', '5'))
IMAP_COMMAND_TIMEOUT_SECONDS = 30

mcp = FastMCP('email')


def decode_mime_header(header):
    if not header:
        return ''
    parts = decode_header(header)
    decoded = []
    for content, charset in parts:
        if isinstance(content, bytes):
            decoded.append(content.decode(charset or 'utf-8', errors='replace'))
        else:
            decoded.append(content)
    return ''.join(decoded)


def parse_email_date(date_str):
    """Parse email date string to a tz-aware UTC datetime for sorting.

    ``email.utils.parsedate_to_datetime`` returns an offset-naive
    ``datetime`` when the input RFC 2822 ``Date`` header lacks a timezone
    (e.g. ``Wed, 09 Sep 2026 14:00:11``). Mixing those naive values with
    the offset-aware epoch fallback used by :func:`sort_emails_by_date`
    raised ``TypeError: can't compare offset-naive and offset-aware
    datetimes`` during the final ``sorted(...)`` call, which surfaced to
    callers as an empty ``list_emails`` result once the fetch window
    (limit * 2) grew large enough to include such a header.

    The narrowest durable fix is to coerce every successfully parsed
    datetime to UTC-aware here, so the sort key is always comparable.
    Naive values are treated as UTC (RFC 2822 leaves the default
    unspecified; ProtonMail Bridge occasionally omits the timezone for
    older messages and the mailbox client is the source of truth).
    """
    if not date_str:
        return None
    try:
        parsed = parsedate_to_datetime(date_str)
    except Exception:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        from datetime import timezone
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_local_time(date_str):
    """Convert email date to local time string."""
    parsed = parse_email_date(date_str)
    if not parsed:
        return None
    # Convert to local time
    local_dt = parsed.astimezone()
    return local_dt.strftime('%Y-%m-%d %H:%M')


def sort_emails_by_date(emails, newest_first=True):
    """Sort emails by date, newest first by default."""
    return sorted(
        emails,
        key=lambda e: parse_email_date(e.get('date')) or parsedate_to_datetime('1 Jan 1970 00:00:00 +0000'),
        reverse=newest_first
    )


async def get_imap_client():
    """Create and authenticate IMAP client."""
    client = IMAP4(host=IMAP_HOST, port=IMAP_PORT, timeout=IMAP_COMMAND_TIMEOUT_SECONDS)

    connection_task = getattr(client, '_client_task', None)
    if connection_task is not None:
        try:
            await asyncio.wait_for(connection_task, IMAP_CONNECT_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            await _force_close_imap_client(client)
            raise RuntimeError(
                f'Proton Bridge IMAP connection timed out after '
                f'{IMAP_CONNECT_TIMEOUT_SECONDS:g}s at {IMAP_HOST}:{IMAP_PORT}; '
                'the Bridge service may be unresponsive'
            ) from exc
        except Exception as exc:
            await _force_close_imap_client(client)
            raise RuntimeError(
                f'Proton Bridge IMAP connection failed at {IMAP_HOST}:{IMAP_PORT} '
                f'({type(exc).__name__}); verify that the Bridge service is running'
            ) from exc

    try:
        await asyncio.wait_for(
            client.wait_hello_from_server(),
            IMAP_CONNECT_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        await _force_close_imap_client(client)
        raise RuntimeError(
            f'Proton Bridge IMAP greeting timed out after '
            f'{IMAP_CONNECT_TIMEOUT_SECONDS:g}s at {IMAP_HOST}:{IMAP_PORT}; '
            'the Bridge service accepted a connection but is not responding'
        ) from exc
    except Exception as exc:
        await _force_close_imap_client(client)
        raise RuntimeError(
            f'Proton Bridge IMAP greeting failed at {IMAP_HOST}:{IMAP_PORT} '
            f'({type(exc).__name__}); verify that the Bridge service is healthy'
        ) from exc

    try:
        result = await asyncio.wait_for(
            client.login(IMAP_USER, IMAP_PASS),
            IMAP_CONNECT_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        await _force_close_imap_client(client)
        raise RuntimeError(
            f'Proton Bridge IMAP authentication timed out after '
            f'{IMAP_CONNECT_TIMEOUT_SECONDS:g}s at {IMAP_HOST}:{IMAP_PORT}; '
            'the Bridge service may be unresponsive'
        ) from exc
    except Exception as exc:
        await _force_close_imap_client(client)
        raise RuntimeError(
            f'Proton Bridge IMAP authentication failed at {IMAP_HOST}:{IMAP_PORT} '
            f'({type(exc).__name__}); verify the Bridge account session and credentials'
        ) from exc

    if result.result != 'OK':
        await _force_close_imap_client(client)
        raise RuntimeError(
            f'Proton Bridge IMAP authentication was rejected at {IMAP_HOST}:{IMAP_PORT}; '
            'verify the Bridge account session and credentials'
        )

    return client


async def _force_close_imap_client(client):
    """Close the IMAP transport without waiting for a responsive server."""
    connection_task = getattr(client, '_client_task', None)
    if connection_task is not None and not connection_task.done():
        connection_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await connection_task

    protocol = getattr(client, 'protocol', None)
    transport = getattr(protocol, 'transport', None)
    if transport is not None:
        transport.abort()


async def _logout_imap_client(client):
    """Best-effort logout followed by an unconditional transport close."""
    try:
        await asyncio.wait_for(
            client.logout(),
            IMAP_CONNECT_TIMEOUT_SECONDS,
        )
    except Exception:
        pass
    finally:
        await _force_close_imap_client(client)


@asynccontextmanager
async def authenticated_imap_client():
    """Yield an authenticated client and always release its connection."""
    client = await get_imap_client()
    try:
        yield client
    finally:
        await _logout_imap_client(client)


@mcp.tool()
async def list_emails(mailbox: str = 'INBOX', limit: int = 10) -> list[dict]:
    """List recent emails with subject, sender, and date (newest first)."""
    async with authenticated_imap_client() as client:
        await client.select(mailbox)

        result = await client.search('ALL')
        if result.result != 'OK':
            return []

        msg_ids = result.lines[0].decode().split()
        # Fetch more than limit since we'll sort by date
        fetch_count = min(len(msg_ids), limit * 2)
        msg_ids = msg_ids[-fetch_count:]

        emails = []
        for msg_id in msg_ids:
            result = await client.fetch(msg_id, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
            if result.result == 'OK':
                for line in result.lines:
                    raw = bytes(line) if isinstance(line, (bytes, bytearray)) else None
                    if raw and len(raw) > 20:
                        try:
                            msg = message_from_bytes(raw)
                            if msg.get('From') or msg.get('Subject'):
                                date_raw = msg.get('Date', '')
                                emails.append({
                                    'id': msg_id,
                                    'from': decode_mime_header(msg.get('From', '')),
                                    'subject': decode_mime_header(msg.get('Subject', '')),
                                    'date': date_raw,
                                    'local_time': format_local_time(date_raw),
                                })
                                break
                        except Exception:
                            pass

        # Sort by date (newest first) and limit results
        return sort_emails_by_date(emails)[:limit]


@mcp.tool()
async def search_emails(
    query: str,
    mailbox: str = 'INBOX',
    limit: int = 20,
    search_body: bool = False
) -> list[dict]:
    """Search emails by subject, sender, or body content.

    Args:
        query: Search term to find in emails
        mailbox: Mailbox to search (default: INBOX)
        limit: Maximum results to return (default: 20)
        search_body: Also search email body content (slower)

    Returns:
        List of matching emails with id, from, subject, date
    """
    async with authenticated_imap_client() as client:
        await client.select(mailbox)

        # Build IMAP search criteria
        # Search in FROM, SUBJECT, and optionally BODY
        search_criteria = f'OR FROM "{query}" SUBJECT "{query}"'
        if search_body:
            search_criteria = f'OR ({search_criteria}) BODY "{query}"'

        result = await client.search(search_criteria)
        if result.result != 'OK':
            return []

        msg_ids = result.lines[0].decode().split()
        if not msg_ids:
            return []

        # Fetch more than limit since we'll sort by date
        fetch_count = min(len(msg_ids), limit * 2)
        msg_ids = msg_ids[-fetch_count:]

        emails = []
        for msg_id in msg_ids:
            result = await client.fetch(msg_id, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
            if result.result == 'OK':
                for line in result.lines:
                    raw = bytes(line) if isinstance(line, (bytes, bytearray)) else None
                    if raw and len(raw) > 20:
                        try:
                            msg = message_from_bytes(raw)
                            if msg.get('From') or msg.get('Subject'):
                                date_raw = msg.get('Date', '')
                                emails.append({
                                    'id': msg_id,
                                    'from': decode_mime_header(msg.get('From', '')),
                                    'subject': decode_mime_header(msg.get('Subject', '')),
                                    'date': date_raw,
                                    'local_time': format_local_time(date_raw),
                                })
                                break
                        except Exception:
                            pass

        # Sort by date (newest first) and limit results
        return sort_emails_by_date(emails)[:limit]


@mcp.tool()
async def get_email(message_id: str, mailbox: str = 'INBOX') -> dict:
    """Get full email content by message ID."""
    async with authenticated_imap_client() as client:
        await client.select(mailbox)

        result = await client.fetch(message_id, '(BODY.PEEK[])')
        if result.result != 'OK':
            return {'error': 'Message not found'}

        raw_email = None
        for line in result.lines:
            if isinstance(line, (bytes, bytearray)) and len(line) > 500:
                raw_email = bytes(line)
                break

        if not raw_email:
            return {'error': 'Could not find message body'}

        try:
            msg = message_from_bytes(raw_email)
        except Exception as e:
            return {'error': f'Parse error: {e}'}

        body = ''
        plain_body = ''
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == 'text/html':
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = _html_converter.handle(payload.decode('utf-8', errors='replace')).strip()
                        break
                elif ct == 'text/plain' and not plain_body:
                    payload = part.get_payload(decode=True)
                    if payload:
                        plain_body = payload.decode('utf-8', errors='replace')
            if not body:
                body = plain_body
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode('utf-8', errors='replace')
                if msg.get_content_type() == 'text/html':
                    body = _html_converter.handle(body).strip()

        date_raw = msg.get('Date', '')
        return {
            'id': message_id,
            'from': decode_mime_header(msg.get('From', '')),
            'to': decode_mime_header(msg.get('To', '')),
            'subject': decode_mime_header(msg.get('Subject', '')),
            'date': date_raw,
            'local_time': format_local_time(date_raw),
            'body': body[:5000],
        }


@mcp.tool()
async def send_email(
    to: str,
    subject: str,
    body: str,
    from_name: str | None = None,
) -> dict:
    """Send an email via SMTP with an optional display name.

    The authenticated Proton address remains the sender address. Existing
    callers that omit ``from_name`` retain the original bare-address header.
    """
    import aiosmtplib

    if from_name is not None and ('\r' in from_name or '\n' in from_name):
        raise ValueError('from_name must not contain newline characters')
    from email.message import EmailMessage

    smtp_host = os.environ.get('PROTON_BRIDGE_HOST', '127.0.0.1')
    smtp_port = int(os.environ.get('PROTON_BRIDGE_SMTP_PORT', '1025'))
    smtp_user = os.environ.get('PROTON_BRIDGE_USER', '')
    smtp_pass = os.environ.get('PROTON_BRIDGE_PASSWORD', '')

    msg = EmailMessage()
    msg['From'] = (
        formataddr((from_name.strip(), smtp_user))
        if from_name and from_name.strip()
        else smtp_user
    )
    msg['To'] = to
    msg['Subject'] = subject
    msg.set_content(body)

    await aiosmtplib.send(
        msg,
        hostname=smtp_host,
        port=smtp_port,
        username=smtp_user,
        password=smtp_pass,
        start_tls=True,
        validate_certs=False,
    )

    return {'status': 'sent', 'to': to, 'subject': subject}
