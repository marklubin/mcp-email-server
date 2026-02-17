"""Notifications backend - general-purpose notification queue with SQLite storage.

Provides MCP tools for pushing/reading notifications from any service,
plus HTTP API endpoints for non-MCP consumers (terminal clients, dashboards, etc.).
"""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

import aiosqlite
from fastmcp import FastMCP

mcp = FastMCP('notifications')

NOTIFY_DB_PATH = os.environ.get('NOTIFY_DB_PATH', os.path.expanduser('~/notifications.db'))

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

async def _get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(NOTIFY_DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute('PRAGMA journal_mode=WAL')
    return db


async def _init_db():
    db = await _get_db()
    try:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                level TEXT NOT NULL DEFAULT 'info',
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                read_at TIMESTAMP,
                expires_at TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_notifications_unread
                ON notifications (read_at) WHERE read_at IS NULL;
            CREATE INDEX IF NOT EXISTS idx_notifications_source
                ON notifications (source);
            CREATE INDEX IF NOT EXISTS idx_notifications_created
                ON notifications (created_at DESC);
        ''')
        await db.commit()
    finally:
        await db.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _auto_cleanup(db: aiosqlite.Connection):
    """Prune expired notifications and old read notifications (>24h)."""
    try:
        now = _now()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        await db.execute(
            "DELETE FROM notifications WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )
        await db.execute(
            "DELETE FROM notifications WHERE read_at IS NOT NULL AND read_at < ?",
            (cutoff,),
        )
        await db.commit()
    except Exception:
        logger.warning("Auto-cleanup failed", exc_info=True)


def _row_to_dict(row) -> dict:
    if row is None:
        return None
    d = dict(row)
    if d.get('metadata_json'):
        try:
            d['metadata'] = json.loads(d['metadata_json'])
        except json.JSONDecodeError:
            pass
    return d


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def push(
    level: str,
    source: str,
    title: str,
    body: Optional[str] = None,
    metadata: Optional[dict] = None,
    expires_at: Optional[str] = None,
) -> dict:
    """Push a notification.

    Args:
        level: Severity level (info, warning, error)
        source: Origin service (e.g. 'blah-radar', 'lab', 'system')
        title: Short summary line
        body: Longer details (optional)
        metadata: Arbitrary JSON metadata (report_id, error info, etc.)
        expires_at: ISO timestamp for auto-cleanup (optional)

    Returns:
        Created notification with id
    """
    if level not in ('info', 'warning', 'error'):
        return {'error': f'Invalid level: {level}. Must be info, warning, or error'}

    await _init_db()
    notification_id = str(uuid.uuid4())[:8]

    db = await _get_db()
    try:
        await db.execute(
            '''INSERT INTO notifications (id, level, source, title, body, metadata_json, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (notification_id, level, source, title, body,
             json.dumps(metadata) if metadata else None, expires_at),
        )
        await db.commit()
    finally:
        await db.close()

    return {'id': notification_id, 'level': level, 'source': source, 'title': title}


@mcp.tool(name="list")
async def list_notifications(
    unread_only: bool = False,
    source: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """List notifications with filters.

    Args:
        unread_only: Only show unread notifications
        source: Filter by source (e.g. 'blah-radar', 'lab')
        level: Filter by level (info, warning, error)
        limit: Maximum results (default: 20)

    Returns:
        List of notifications
    """
    await _init_db()
    limit = min(max(1, limit), 100)

    query = 'SELECT * FROM notifications WHERE 1=1'
    params = []

    if unread_only:
        query += ' AND read_at IS NULL'
    if source:
        query += ' AND source = ?'
        params.append(source)
    if level:
        query += ' AND level = ?'
        params.append(level)

    query += ' ORDER BY created_at DESC LIMIT ?'
    params.append(limit)

    db = await _get_db()
    try:
        await _auto_cleanup(db)
        rows = await db.execute_fetchall(query, params)
        notifications = [_row_to_dict(r) for r in rows]
        return {'notifications': notifications, 'count': len(notifications)}
    finally:
        await db.close()


@mcp.tool()
async def get(notification_id: str) -> dict:
    """Get a single notification with full detail.

    Args:
        notification_id: The notification ID

    Returns:
        Full notification details
    """
    await _init_db()
    db = await _get_db()
    try:
        rows = await db.execute_fetchall(
            'SELECT * FROM notifications WHERE id = ?', (notification_id,),
        )
        if not rows:
            return {'error': f'Notification {notification_id} not found'}
        return {'notification': _row_to_dict(rows[0])}
    finally:
        await db.close()


@mcp.tool()
async def mark_read(notification_ids: list[str]) -> dict:
    """Mark one or more notifications as read.

    Args:
        notification_ids: List of notification IDs to mark as read

    Returns:
        Count of notifications marked
    """
    if not notification_ids:
        return {'error': 'No notification IDs provided'}

    await _init_db()
    now = _now()
    db = await _get_db()
    try:
        placeholders = ','.join('?' for _ in notification_ids)
        cursor = await db.execute(
            f'UPDATE notifications SET read_at = ? WHERE id IN ({placeholders}) AND read_at IS NULL',
            [now] + notification_ids,
        )
        await db.commit()
        return {'marked': cursor.rowcount}
    finally:
        await db.close()


@mcp.tool()
async def clear(
    before: Optional[str] = None,
    source: Optional[str] = None,
    read_only: bool = True,
) -> dict:
    """Delete old/read notifications.

    Args:
        before: Delete notifications created before this ISO timestamp
        source: Only delete notifications from this source
        read_only: Only delete read notifications (default: True)

    Returns:
        Count of deleted notifications
    """
    await _init_db()

    query = 'DELETE FROM notifications WHERE 1=1'
    params = []

    if read_only:
        query += ' AND read_at IS NOT NULL'
    if before:
        query += ' AND created_at < ?'
        params.append(before)
    if source:
        query += ' AND source = ?'
        params.append(source)

    db = await _get_db()
    try:
        cursor = await db.execute(query, params)
        await db.commit()
        return {'deleted': cursor.rowcount}
    finally:
        await db.close()


@mcp.tool()
async def summary() -> dict:
    """Get notification counts by source and level, plus total unread count.

    Returns:
        Unread counts by source and level, total unread count
    """
    await _init_db()
    db = await _get_db()
    try:
        # Total unread
        rows = await db.execute_fetchall(
            'SELECT COUNT(*) as count FROM notifications WHERE read_at IS NULL',
        )
        total_unread = rows[0]['count']

        # By source
        rows = await db.execute_fetchall(
            'SELECT source, COUNT(*) as count FROM notifications WHERE read_at IS NULL GROUP BY source',
        )
        by_source = {r['source']: r['count'] for r in rows}

        # By level
        rows = await db.execute_fetchall(
            'SELECT level, COUNT(*) as count FROM notifications WHERE read_at IS NULL GROUP BY level',
        )
        by_level = {r['level']: r['count'] for r in rows}

        return {
            'total_unread': total_unread,
            'by_source': by_source,
            'by_level': by_level,
        }
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def http_list_notifications(request: Request) -> JSONResponse:
    """List notifications with query param filters."""
    await _init_db()

    unread_only = request.query_params.get('unread_only', '').lower() in ('true', '1', 'yes')
    source = request.query_params.get('source')
    level = request.query_params.get('level')
    limit = min(int(request.query_params.get('limit', '20')), 100)

    query = 'SELECT * FROM notifications WHERE 1=1'
    params = []

    if unread_only:
        query += ' AND read_at IS NULL'
    if source:
        query += ' AND source = ?'
        params.append(source)
    if level:
        query += ' AND level = ?'
        params.append(level)

    query += ' ORDER BY created_at DESC LIMIT ?'
    params.append(limit)

    db = await _get_db()
    try:
        await _auto_cleanup(db)
        rows = await db.execute_fetchall(query, params)
        notifications = [_row_to_dict(r) for r in rows]
        return JSONResponse({'notifications': notifications, 'count': len(notifications)})
    finally:
        await db.close()


async def http_get_notification(request: Request) -> JSONResponse:
    """Get a single notification."""
    notification_id = request.path_params['notification_id']
    await _init_db()
    db = await _get_db()
    try:
        rows = await db.execute_fetchall(
            'SELECT * FROM notifications WHERE id = ?', (notification_id,),
        )
        if not rows:
            return JSONResponse({'error': 'Not found'}, status_code=404)
        return JSONResponse({'notification': _row_to_dict(rows[0])})
    finally:
        await db.close()


async def http_mark_read(request: Request) -> JSONResponse:
    """Mark notifications as read. Body: {"ids": [...]}"""
    await _init_db()
    body = await request.json()
    ids = body.get('ids', [])
    if not ids:
        return JSONResponse({'error': 'No IDs provided'}, status_code=400)

    now = _now()
    db = await _get_db()
    try:
        placeholders = ','.join('?' for _ in ids)
        cursor = await db.execute(
            f'UPDATE notifications SET read_at = ? WHERE id IN ({placeholders}) AND read_at IS NULL',
            [now] + ids,
        )
        await db.commit()
        return JSONResponse({'marked': cursor.rowcount})
    finally:
        await db.close()


async def http_summary(request: Request) -> JSONResponse:
    """Unread counts by source/level."""
    await _init_db()
    db = await _get_db()
    try:
        rows = await db.execute_fetchall(
            'SELECT COUNT(*) as count FROM notifications WHERE read_at IS NULL',
        )
        total_unread = rows[0]['count']

        rows = await db.execute_fetchall(
            'SELECT source, COUNT(*) as count FROM notifications WHERE read_at IS NULL GROUP BY source',
        )
        by_source = {r['source']: r['count'] for r in rows}

        rows = await db.execute_fetchall(
            'SELECT level, COUNT(*) as count FROM notifications WHERE read_at IS NULL GROUP BY level',
        )
        by_level = {r['level']: r['count'] for r in rows}

        return JSONResponse({
            'total_unread': total_unread,
            'by_source': by_source,
            'by_level': by_level,
        })
    finally:
        await db.close()


async def http_push(request: Request) -> JSONResponse:
    """Push a notification via HTTP. Body: {level, source, title, body?, metadata?, expires_at?}"""
    await _init_db()
    body = await request.json()

    required = ['level', 'source', 'title']
    missing = [f for f in required if f not in body]
    if missing:
        return JSONResponse({'error': f'Missing fields: {missing}'}, status_code=400)

    level = body['level']
    if level not in ('info', 'warning', 'error'):
        return JSONResponse({'error': f'Invalid level: {level}'}, status_code=400)

    notification_id = str(uuid.uuid4())[:8]
    metadata = body.get('metadata')

    db = await _get_db()
    try:
        await db.execute(
            '''INSERT INTO notifications (id, level, source, title, body, metadata_json, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (notification_id, level, body['source'], body['title'],
             body.get('body'), json.dumps(metadata) if metadata else None,
             body.get('expires_at')),
        )
        await db.commit()
    finally:
        await db.close()

    return JSONResponse({
        'id': notification_id,
        'level': level,
        'source': body['source'],
        'title': body['title'],
    })


# Starlette routes for the notification HTTP API
notify_http_routes = [
    Route('/notifications', http_list_notifications, methods=['GET']),
    Route('/notifications/summary', http_summary, methods=['GET']),
    Route('/notifications/read', http_mark_read, methods=['POST']),
    Route('/notifications/push', http_push, methods=['POST']),
    Route('/notifications/{notification_id}', http_get_notification, methods=['GET']),
]
