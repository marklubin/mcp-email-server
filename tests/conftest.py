"""Test fixtures for MCP router integration tests."""

import sys
from pathlib import Path

# Add router directory to path for imports
router_path = Path(__file__).parent.parent / "router"
sys.path.insert(0, str(router_path))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from email.message import EmailMessage
from datetime import datetime, timezone


# Sample email data for mocking
# Note: body must be > 500 chars for get_email to parse it
MOCK_EMAILS = [
    {
        'id': '1',
        'from': 'alice@example.com',
        'subject': 'Meeting tomorrow',
        'date': 'Mon, 27 Jan 2026 10:00:00 -0800',
        'body': 'Hi, can we meet tomorrow at 2pm? ' + 'x' * 500,
    },
    {
        'id': '2',
        'from': 'bob@example.com',
        'subject': 'Project update',
        'date': 'Mon, 27 Jan 2026 14:30:00 -0800',
        'body': 'Here is the latest project status update. ' + 'x' * 500,
    },
    {
        'id': '3',
        'from': 'carol@example.com',
        'subject': 'Invoice #1234',
        'date': 'Tue, 28 Jan 2026 09:15:00 -0800',
        'body': 'Please find attached invoice #1234. ' + 'x' * 500,
    },
]


def create_mock_email_bytes(email_data: dict, headers_only: bool = False) -> bytes:
    """Create mock email bytes for IMAP responses."""
    msg = EmailMessage()
    msg['From'] = email_data['from']
    msg['Subject'] = email_data['subject']
    msg['Date'] = email_data['date']
    msg['To'] = 'test@example.com'

    if not headers_only:
        msg.set_content(email_data.get('body', ''))

    return msg.as_bytes()


class MockIMAPResponse:
    """Mock IMAP command response."""

    def __init__(self, result: str = 'OK', lines: list = None):
        self.result = result
        self.lines = lines or []


class MockIMAPClient:
    """Mock aioimaplib IMAP4 client."""

    def __init__(self, emails: list = None):
        self.emails = {e['id']: e for e in (emails or MOCK_EMAILS)}
        self.selected_mailbox = None
        self.logged_in = False

    async def wait_hello_from_server(self):
        pass

    async def login(self, user: str, password: str):
        self.logged_in = True
        return MockIMAPResponse('OK')

    async def logout(self):
        self.logged_in = False
        return MockIMAPResponse('OK')

    async def select(self, mailbox: str = 'INBOX'):
        self.selected_mailbox = mailbox
        return MockIMAPResponse('OK')

    async def search(self, criteria: str = 'ALL'):
        # Simple search simulation
        if criteria == 'ALL':
            ids = list(self.emails.keys())
        else:
            # Search in FROM and SUBJECT
            ids = []
            for eid, email in self.emails.items():
                criteria_lower = criteria.lower()
                if (email['from'].lower() in criteria_lower or
                    criteria_lower in email['from'].lower() or
                    email['subject'].lower() in criteria_lower or
                    criteria_lower in email['subject'].lower()):
                    ids.append(eid)
            # If no specific match, return all (simplified)
            if not ids and 'OR' in criteria:
                # Extract search term from OR FROM "term" SUBJECT "term"
                import re
                match = re.search(r'"([^"]+)"', criteria)
                if match:
                    term = match.group(1).lower()
                    for eid, email in self.emails.items():
                        if term in email['from'].lower() or term in email['subject'].lower():
                            ids.append(eid)

        return MockIMAPResponse('OK', [' '.join(ids).encode()])

    async def fetch(self, msg_id: str, parts: str):
        email = self.emails.get(msg_id)
        if not email:
            return MockIMAPResponse('NO', [])

        headers_only = 'HEADER' in parts
        email_bytes = create_mock_email_bytes(email, headers_only=headers_only)

        return MockIMAPResponse('OK', [email_bytes])


@pytest.fixture
def mock_emails():
    """Provide mock email data."""
    return MOCK_EMAILS.copy()


@pytest.fixture
def mock_imap_client(mock_emails):
    """Create a mock IMAP client with test data."""
    return MockIMAPClient(mock_emails)


@pytest.fixture
def patch_imap(mock_imap_client):
    """Patch aioimaplib.IMAP4 to return mock client."""
    with patch('router.backends.email.IMAP4') as mock_class:
        mock_class.return_value = mock_imap_client
        yield mock_imap_client


@pytest.fixture
def patch_smtp():
    """Patch aiosmtplib.send to capture sent emails."""
    sent_emails = []

    async def mock_send(msg, **kwargs):
        sent_emails.append({
            'to': msg['To'],
            'from': msg['From'],
            'subject': msg['Subject'],
            'body': msg.get_content(),
            'kwargs': kwargs,
        })

    with patch('aiosmtplib.send', side_effect=mock_send):
        yield sent_emails


@pytest.fixture
def env_vars(monkeypatch):
    """Set required environment variables for tests."""
    monkeypatch.setenv('PROTON_BRIDGE_HOST', '127.0.0.1')
    monkeypatch.setenv('PROTON_BRIDGE_IMAP_PORT', '1143')
    monkeypatch.setenv('PROTON_BRIDGE_SMTP_PORT', '1025')
    monkeypatch.setenv('PROTON_BRIDGE_USER', 'test@example.com')
    monkeypatch.setenv('PROTON_BRIDGE_PASSWORD', 'testpass')
    monkeypatch.setenv('MCP_SECRET', 'test-secret')
