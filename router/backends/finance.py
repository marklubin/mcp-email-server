"""Read-only gateway to the local finance data service.

The finance service owns Plaid credentials, synchronization, and storage. This
backend deliberately exposes only bounded GET operations so MCP clients never
receive provider credentials or gain access to administrative actions.
"""

import ipaddress
import os
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from fastmcp import FastMCP

mcp = FastMCP('finance')

DEFAULT_SERVICE_URL = 'http://127.0.0.1:8090'
DEFAULT_TRANSACTION_DAYS = 30
MAX_DATE_RANGE_DAYS = 366
MAX_ACCOUNTS = 100
MAX_TRANSACTIONS = 500
MAX_CHANGES = 500
MAX_CURSOR_LENGTH = 2048
MAX_ACCOUNT_ID_LENGTH = 200
MAX_RESPONSE_BYTES = 1_000_000
REQUEST_TIMEOUT_SECONDS = 15.0


def _error(message: str, code: str, **details: Any) -> dict:
    """Return a consistent, agent-friendly error response."""
    return {'error': message, 'code': code, **details}


def _service_config() -> tuple[str, str] | dict:
    """Read and validate the loopback service configuration."""
    base_url = os.environ.get('FINANCE_SERVICE_URL', DEFAULT_SERVICE_URL).strip().rstrip('/')
    token = os.environ.get('FINANCE_SERVICE_TOKEN', '').strip()

    if not token:
        return _error(
            'Finance service bearer token is not configured',
            'not_configured',
        )

    parsed = urlparse(base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return _error('FINANCE_SERVICE_URL is invalid', 'invalid_configuration')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return _error('FINANCE_SERVICE_URL is invalid', 'invalid_configuration')

    hostname = parsed.hostname
    try:
        is_loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname.lower() == 'localhost'

    if not is_loopback:
        return _error(
            'FINANCE_SERVICE_URL must point to a loopback address',
            'invalid_configuration',
        )

    return base_url, token


def _safe_upstream_detail(response: httpx.Response) -> str | None:
    """Extract a short, printable error detail without echoing large bodies."""
    detail = None
    try:
        body = response.json()
        if isinstance(body, dict):
            candidate = body.get('detail') or body.get('error')
            if isinstance(candidate, str):
                detail = candidate
    except ValueError:
        detail = response.text

    if not detail:
        return None
    return ''.join(character for character in detail if character.isprintable())[:300]


async def _get(endpoint: str, params: dict | None = None) -> dict:
    """Perform one authenticated, non-redirecting GET against the service."""
    config = _service_config()
    if isinstance(config, dict):
        return config
    base_url, token = config

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = await client.get(
                f'{base_url}{endpoint}',
                headers={
                    'Authorization': f'Bearer {token}',
                    'Accept': 'application/json',
                },
                params=params,
            )
    except httpx.TimeoutException:
        return _error('Finance service request timed out', 'service_timeout')
    except httpx.RequestError:
        return _error('Finance service is unavailable', 'service_unavailable')
    except Exception:
        # Avoid reflecting exception strings, which can contain credentials or URLs.
        return _error('Finance service request failed', 'service_unavailable')

    if response.status_code in (401, 403):
        return _error(
            'Finance service rejected the configured bearer token',
            'service_auth_failed',
            status=response.status_code,
        )
    if response.status_code != 200:
        result = _error(
            'Finance service returned an error',
            'service_error',
            status=response.status_code,
        )
        detail = _safe_upstream_detail(response)
        if detail:
            result['detail'] = detail
        return result

    if len(response.content) > MAX_RESPONSE_BYTES:
        return _error(
            'Finance service response exceeded the size limit',
            'response_too_large',
        )

    try:
        data = response.json()
    except ValueError:
        return _error(
            'Finance service returned invalid JSON',
            'invalid_service_response',
        )

    if not isinstance(data, dict):
        return _error(
            'Finance service returned an unexpected response shape',
            'invalid_service_response',
        )
    return data


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return min(max(value, minimum), maximum)


def _bounded_collection(data: dict, key: str, limit: int) -> dict:
    """Reject an upstream response that violates the requested result limit."""
    collection = data.get(key)
    if not isinstance(collection, list):
        return _error(
            f'Finance service response is missing a valid {key} list',
            'invalid_service_response',
        )
    if len(collection) > limit:
        return _error(
            f'Finance service returned more than {limit} {key}',
            'service_limit_violation',
        )
    return data


def _parse_date(value: str, field: str) -> tuple[date | None, dict | None]:
    try:
        return date.fromisoformat(value), None
    except (TypeError, ValueError):
        return None, _error(
            f'{field} must be an ISO date in YYYY-MM-DD format',
            'invalid_date',
        )


def _transaction_dates(
    start_date: str | None,
    end_date: str | None,
) -> tuple[str | None, str | None, dict | None]:
    """Apply the default window and enforce the maximum inclusive span."""
    parsed_end, error = _parse_date(end_date, 'end_date') if end_date else (date.today(), None)
    if error:
        return None, None, error

    parsed_start, error = (
        _parse_date(start_date, 'start_date')
        if start_date
        else (parsed_end - timedelta(days=DEFAULT_TRANSACTION_DAYS - 1), None)
    )
    if error:
        return None, None, error

    if parsed_start > parsed_end:
        return None, None, _error(
            'start_date must be on or before end_date',
            'invalid_date_range',
        )

    inclusive_days = (parsed_end - parsed_start).days + 1
    if inclusive_days > MAX_DATE_RANGE_DAYS:
        return None, None, _error(
            f'Transaction date range cannot exceed {MAX_DATE_RANGE_DAYS} days',
            'date_range_too_large',
            max_days=MAX_DATE_RANGE_DAYS,
        )

    return parsed_start.isoformat(), parsed_end.isoformat(), None


@mcp.tool(name='summary')
async def finance_summary(days: int = 30) -> dict:
    """Get a read-only financial summary.

    Args:
        days: Summary period in days (clamped to 1-366).

    Returns:
        Provider-neutral balances, cash-flow totals, and ``data_as_of`` from
        the finance service, or a structured error.
    """
    days = _clamp(days, 1, MAX_DATE_RANGE_DAYS)
    return await _get('/v1/summary', params={'days': days})


@mcp.tool(name='accounts')
async def finance_accounts(
    include_inactive: bool = False,
    limit: int = 50,
) -> dict:
    """List read-only normalized financial accounts.

    Args:
        include_inactive: Include disconnected or inactive accounts.
        limit: Maximum accounts to return (clamped to 1-100).
    """
    limit = _clamp(limit, 1, MAX_ACCOUNTS)
    data = await _get('/v1/accounts', params={
        'include_inactive': include_inactive,
        'limit': limit,
    })
    if 'error' in data:
        return data
    return _bounded_collection(data, 'accounts', limit)


@mcp.tool(name='transactions')
async def finance_transactions(
    start_date: str | None = None,
    end_date: str | None = None,
    account_id: str | None = None,
    limit: int = 100,
) -> dict:
    """List transactions within a bounded date window.

    Args:
        start_date: Inclusive ISO date (defaults to 29 days before end_date).
        end_date: Inclusive ISO date (defaults to today).
        account_id: Optional normalized account ID filter.
        limit: Maximum transactions to return (clamped to 1-500).

    The inclusive date range cannot exceed 366 days.
    """
    normalized_start, normalized_end, error = _transaction_dates(start_date, end_date)
    if error:
        return error
    if account_id is not None and len(account_id) > MAX_ACCOUNT_ID_LENGTH:
        return _error('account_id is too long', 'invalid_account_id')

    limit = _clamp(limit, 1, MAX_TRANSACTIONS)
    params = {
        'start_date': normalized_start,
        'end_date': normalized_end,
        'limit': limit,
    }
    if account_id:
        params['account_id'] = account_id

    data = await _get('/v1/transactions', params=params)
    if 'error' in data:
        return data
    return _bounded_collection(data, 'transactions', limit)


@mcp.tool(name='changes')
async def finance_changes(
    cursor: str | None = None,
    limit: int = 100,
) -> dict:
    """Read a bounded page of normalized transaction changes.

    Args:
        cursor: Opaque cursor returned by a previous call.
        limit: Maximum total added, modified, and removed entries (1-500).
    """
    if cursor is not None and len(cursor) > MAX_CURSOR_LENGTH:
        return _error('cursor is too long', 'invalid_cursor')

    limit = _clamp(limit, 1, MAX_CHANGES)
    params: dict[str, str | int] = {'limit': limit}
    if cursor:
        params['cursor'] = cursor

    data = await _get('/v1/changes', params=params)
    if 'error' in data:
        return data

    if 'changes' in data:
        return _bounded_collection(data, 'changes', limit)

    change_keys = ('added', 'modified', 'removed')
    if not all(isinstance(data.get(key), list) for key in change_keys):
        return _error(
            'Finance service response is missing valid change lists',
            'invalid_service_response',
        )
    if sum(len(data[key]) for key in change_keys) > limit:
        return _error(
            f'Finance service returned more than {limit} changes',
            'service_limit_violation',
        )
    return data


@mcp.tool(name='sync_status')
async def finance_sync_status() -> dict:
    """Get read-only sync health and data-freshness information."""
    return await _get('/v1/sync-status')
