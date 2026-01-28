"""Email backend for ProtonMail Bridge."""

import os
from email.header import decode_header
from email import message_from_bytes
from email.utils import parsedate_to_datetime

from fastmcp import FastMCP
from aioimaplib import IMAP4

IMAP_HOST = os.environ.get('PROTON_BRIDGE_HOST', '127.0.0.1')
IMAP_PORT = int(os.environ.get('PROTON_BRIDGE_IMAP_PORT', '1143'))
IMAP_USER = os.environ.get('PROTON_BRIDGE_USER', '')
IMAP_PASS = os.environ.get('PROTON_BRIDGE_PASSWORD', '')

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
    """Parse email date string to datetime for sorting."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


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
    client = IMAP4(host=IMAP_HOST, port=IMAP_PORT, timeout=30)
    await client.wait_hello_from_server()
    await client.login(IMAP_USER, IMAP_PASS)
    return client


@mcp.tool()
async def list_emails(mailbox: str = 'INBOX', limit: int = 10) -> list[dict]:
    """List recent emails with subject, sender, and date (newest first)."""
    client = await get_imap_client()
    await client.select(mailbox)

    result = await client.search('ALL')
    if result.result != 'OK':
        await client.logout()
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
                    except:
                        pass

    await client.logout()
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
    client = await get_imap_client()
    await client.select(mailbox)

    # Build IMAP search criteria
    # Search in FROM, SUBJECT, and optionally BODY
    search_criteria = f'OR FROM "{query}" SUBJECT "{query}"'
    if search_body:
        search_criteria = f'OR ({search_criteria}) BODY "{query}"'

    result = await client.search(search_criteria)
    if result.result != 'OK':
        await client.logout()
        return []

    msg_ids = result.lines[0].decode().split()
    if not msg_ids:
        await client.logout()
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
                    except:
                        pass

    await client.logout()
    # Sort by date (newest first) and limit results
    return sort_emails_by_date(emails)[:limit]


@mcp.tool()
async def get_email(message_id: str, mailbox: str = 'INBOX') -> dict:
    """Get full email content by message ID."""
    client = await get_imap_client()
    await client.select(mailbox)

    result = await client.fetch(message_id, '(RFC822)')
    if result.result != 'OK':
        await client.logout()
        return {'error': 'Message not found'}

    raw_email = None
    for line in result.lines:
        if isinstance(line, (bytes, bytearray)) and len(line) > 500:
            raw_email = bytes(line)
            break

    if not raw_email:
        await client.logout()
        return {'error': 'Could not find message body'}

    try:
        msg = message_from_bytes(raw_email)
    except Exception as e:
        await client.logout()
        return {'error': f'Parse error: {e}'}

    body = ''
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/plain':
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode('utf-8', errors='replace')
                    break
            elif ct == 'text/html' and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    import re
                    html = payload.decode('utf-8', errors='replace')
                    body = re.sub(r'<[^>]+>', '', html)[:3000]
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode('utf-8', errors='replace')

    await client.logout()
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
async def send_email(to: str, subject: str, body: str) -> dict:
    """Send an email via SMTP."""
    import aiosmtplib
    from email.message import EmailMessage

    smtp_host = os.environ.get('PROTON_BRIDGE_HOST', '127.0.0.1')
    smtp_port = int(os.environ.get('PROTON_BRIDGE_SMTP_PORT', '1025'))
    smtp_user = os.environ.get('PROTON_BRIDGE_USER', '')
    smtp_pass = os.environ.get('PROTON_BRIDGE_PASSWORD', '')

    msg = EmailMessage()
    msg['From'] = smtp_user
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
