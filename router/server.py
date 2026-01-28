#!/usr/bin/env python3
"""Minimal MCP Email Server for ProtonMail Bridge."""

import os
import json
from email.header import decode_header
from email import message_from_bytes

from fastmcp import FastMCP
from aioimaplib import IMAP4

IMAP_HOST = os.environ.get('PROTON_BRIDGE_HOST', '127.0.0.1')
IMAP_PORT = int(os.environ.get('PROTON_BRIDGE_IMAP_PORT', '1143'))
IMAP_USER = os.environ.get('PROTON_BRIDGE_USER', '')
IMAP_PASS = os.environ.get('PROTON_BRIDGE_PASSWORD', '')
MCP_SECRET = os.environ.get('MCP_SECRET', '')

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


@mcp.tool()
async def list_emails(mailbox: str = 'INBOX', limit: int = 10) -> list[dict]:
    """List recent emails with subject, sender, and date."""
    client = IMAP4(host=IMAP_HOST, port=IMAP_PORT, timeout=30)
    await client.wait_hello_from_server()
    await client.login(IMAP_USER, IMAP_PASS)
    await client.select(mailbox)
    
    result = await client.search('ALL')
    if result.result != 'OK':
        await client.logout()
        return []
    
    msg_ids = result.lines[0].decode().split()
    msg_ids = msg_ids[-limit:][::-1]
    
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
                            emails.append({
                                'id': msg_id,
                                'from': decode_mime_header(msg.get('From', '')),
                                'subject': decode_mime_header(msg.get('Subject', '')),
                                'date': msg.get('Date', ''),
                            })
                            break
                    except:
                        pass
    
    await client.logout()
    return emails


@mcp.tool()
async def get_email(message_id: str, mailbox: str = 'INBOX') -> dict:
    """Get full email content by message ID."""
    client = IMAP4(host=IMAP_HOST, port=IMAP_PORT, timeout=30)
    await client.wait_hello_from_server()
    await client.login(IMAP_USER, IMAP_PASS)
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
    return {
        'id': message_id,
        'from': decode_mime_header(msg.get('From', '')),
        'to': decode_mime_header(msg.get('To', '')),
        'subject': decode_mime_header(msg.get('Subject', '')),
        'date': msg.get('Date', ''),
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
        start_tls=True, validate_certs=False,
    )
    
    return {'status': 'sent', 'to': to, 'subject': subject}


@mcp.tool()
def health() -> dict:
    """Health check."""
    return {'status': 'ok'}


class AuthMiddleware:
    """Check X-MCP-Secret header."""
    def __init__(self, app, secret):
        self.app = app
        self.secret = secret
    
    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and self.secret:
            headers = {k.decode(): v.decode() for k, v in scope.get('headers', [])}
            if headers.get('x-mcp-secret') != self.secret:
                response = json.dumps({
                    'jsonrpc': '2.0',
                    'id': 'auth-error',
                    'error': {'code': -32001, 'message': 'Unauthorized'}
                }).encode()
                await send({'type': 'http.response.start', 'status': 401,
                           'headers': [(b'content-type', b'application/json')]})
                await send({'type': 'http.response.body', 'body': response})
                return
        await self.app(scope, receive, send)


def main():
    import uvicorn
    host = os.environ.get('ROUTER_HOST', '127.0.0.1')
    port = int(os.environ.get('ROUTER_PORT', '8080'))

    app = mcp.http_app()
    if MCP_SECRET:
        print('API key auth enabled')
        app = AuthMiddleware(app, MCP_SECRET)

    uvicorn.run(app, host=host, port=port)


if __name__ == '__main__':
    main()
