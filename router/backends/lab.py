"""Lab backend - hypothesis validation queue with SQLite storage.

Provides MCP tools for submitting hypotheses and retrieving reports,
plus HTTP API endpoints for the lab server to poll for work and post results.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP('lab')

LAB_DB_PATH = os.environ.get('LAB_DB_PATH', os.path.expanduser('~/lab.db'))

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

async def _get_db() -> aiosqlite.Connection:
    """Get a connection to the lab database."""
    db = await aiosqlite.connect(LAB_DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute('PRAGMA journal_mode=WAL')
    await db.execute('PRAGMA foreign_keys=ON')
    return db


async def _init_db():
    """Create tables if they don't exist."""
    db = await _get_db()
    try:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS hypotheses (
                id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                hypothesis TEXT NOT NULL,
                motivation TEXT,
                domain TEXT DEFAULT 'general',
                context_json TEXT,
                submitter TEXT,
                callback_email TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                hypothesis_id TEXT REFERENCES hypotheses(id),
                title TEXT,
                content TEXT,
                doc_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                hypothesis_id TEXT REFERENCES hypotheses(id),
                verdict TEXT,
                confidence INTEGER,
                decision_readiness TEXT,
                executive_summary TEXT,
                full_report TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        await db.commit()
    finally:
        await db.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict:
    """Convert an aiosqlite.Row to a plain dict."""
    if row is None:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# MCP Tools (used by Claude sessions in the main workspace)
# ---------------------------------------------------------------------------

@mcp.tool()
async def submit_hypothesis(
    hypothesis: str,
    motivation: str,
    domain: str = 'general',
    documents: Optional[list[dict]] = None,
    prior_evidence: Optional[list[dict]] = None,
    time_budget_minutes: int = 30,
    cost_budget_usd: float = 2.00,
    model: str = 'sonnet',
    scope_limits: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    callback_email: Optional[str] = None,
) -> dict:
    """Submit a hypothesis to the research lab for autonomous validation.

    Args:
        hypothesis: The falsifiable claim to test
        motivation: Why this matters / what decision it informs
        domain: Research domain (general, competitive-intel, technical, market, etc.)
        documents: Attached docs [{title, content, type}] for context
        prior_evidence: Known evidence [{claim, source, direction}]
        time_budget_minutes: Max time for research (default: 30)
        cost_budget_usd: Max API spend (default: 2.00)
        model: Claude model to use (sonnet, opus, haiku)
        scope_limits: Things the lab should NOT do
        tags: Labels for organization
        callback_email: Email for report notification

    Returns:
        Hypothesis ID and status confirmation
    """
    await _init_db()
    hypothesis_id = str(uuid.uuid4())[:8]

    context = {
        'motivation': motivation,
        'domain': domain,
        'prior_evidence': prior_evidence or [],
        'time_budget_minutes': time_budget_minutes,
        'cost_budget_usd': cost_budget_usd,
        'model': model,
        'scope_limits': scope_limits or [],
        'tags': tags or [],
    }

    db = await _get_db()
    try:
        await db.execute(
            '''INSERT INTO hypotheses (id, hypothesis, motivation, domain, context_json,
               submitter, callback_email)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (hypothesis_id, hypothesis, motivation, domain,
             json.dumps(context), 'mcp-session', callback_email),
        )

        # Insert attached documents
        if documents:
            for doc in documents:
                doc_id = str(uuid.uuid4())[:8]
                await db.execute(
                    '''INSERT INTO documents (id, hypothesis_id, title, content, doc_type)
                       VALUES (?, ?, ?, ?, ?)''',
                    (doc_id, hypothesis_id, doc.get('title', 'Untitled'),
                     doc.get('content', ''), doc.get('type', 'reference')),
                )

        await db.commit()
    finally:
        await db.close()

    return {
        'hypothesis_id': hypothesis_id,
        'status': 'pending',
        'hypothesis': hypothesis,
        'message': 'Hypothesis submitted to lab queue. Use list_hypotheses() to track status.',
    }


@mcp.tool()
async def list_hypotheses(
    status: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """List hypotheses and their current status.

    Args:
        status: Filter by status (pending, processing, completed, failed)
        limit: Maximum results (default: 20)

    Returns:
        List of hypotheses with id, status, hypothesis text, and timestamps
    """
    await _init_db()
    limit = min(max(1, limit), 100)

    db = await _get_db()
    try:
        if status:
            rows = await db.execute_fetchall(
                'SELECT * FROM hypotheses WHERE status = ? ORDER BY created_at DESC LIMIT ?',
                (status, limit),
            )
        else:
            rows = await db.execute_fetchall(
                'SELECT * FROM hypotheses ORDER BY created_at DESC LIMIT ?',
                (limit,),
            )

        hypotheses = [_row_to_dict(r) for r in rows]

        # Attach report summary if completed
        for h in hypotheses:
            if h['status'] == 'completed':
                report = await db.execute_fetchall(
                    'SELECT id, verdict, confidence, decision_readiness, executive_summary '
                    'FROM reports WHERE hypothesis_id = ? LIMIT 1',
                    (h['id'],),
                )
                if report:
                    h['report_summary'] = _row_to_dict(report[0])

        return {'hypotheses': hypotheses, 'count': len(hypotheses)}
    finally:
        await db.close()


@mcp.tool()
async def get_hypothesis(hypothesis_id: str) -> dict:
    """Get hypothesis details including attached documents.

    Args:
        hypothesis_id: The hypothesis ID

    Returns:
        Full hypothesis details with documents
    """
    await _init_db()
    db = await _get_db()
    try:
        row = await db.execute_fetchall(
            'SELECT * FROM hypotheses WHERE id = ?', (hypothesis_id,),
        )
        if not row:
            return {'error': f'Hypothesis {hypothesis_id} not found'}

        h = _row_to_dict(row[0])

        docs = await db.execute_fetchall(
            'SELECT * FROM documents WHERE hypothesis_id = ?', (hypothesis_id,),
        )
        h['documents'] = [_row_to_dict(d) for d in docs]

        # Parse context_json for readability
        if h.get('context_json'):
            try:
                h['context'] = json.loads(h['context_json'])
            except json.JSONDecodeError:
                pass

        return {'hypothesis': h}
    finally:
        await db.close()


@mcp.tool()
async def list_reports(
    verdict: Optional[str] = None,
    min_confidence: Optional[int] = None,
    limit: int = 20,
) -> dict:
    """List completed lab reports with verdict and confidence.

    Args:
        verdict: Filter by verdict (SUPPORTED, REFUTED, INCONCLUSIVE, OUT_OF_SCOPE)
        min_confidence: Minimum confidence level (1-5)
        limit: Maximum results (default: 20)

    Returns:
        List of reports with verdict, confidence, and executive summary
    """
    await _init_db()
    limit = min(max(1, limit), 100)

    db = await _get_db()
    try:
        query = 'SELECT r.*, h.hypothesis FROM reports r JOIN hypotheses h ON r.hypothesis_id = h.id WHERE 1=1'
        params = []

        if verdict:
            query += ' AND r.verdict = ?'
            params.append(verdict)
        if min_confidence is not None:
            query += ' AND r.confidence >= ?'
            params.append(min_confidence)

        query += ' ORDER BY r.created_at DESC LIMIT ?'
        params.append(limit)

        rows = await db.execute_fetchall(query, params)
        reports = [_row_to_dict(r) for r in rows]

        return {'reports': reports, 'count': len(reports)}
    finally:
        await db.close()


@mcp.tool()
async def get_report(report_id: str) -> dict:
    """Get a full lab report by ID.

    Args:
        report_id: The report ID

    Returns:
        Full report including markdown content, verdict, and metadata
    """
    await _init_db()
    db = await _get_db()
    try:
        rows = await db.execute_fetchall(
            '''SELECT r.*, h.hypothesis, h.motivation, h.domain
               FROM reports r JOIN hypotheses h ON r.hypothesis_id = h.id
               WHERE r.id = ?''',
            (report_id,),
        )
        if not rows:
            return {'error': f'Report {report_id} not found'}

        report = _row_to_dict(rows[0])
        if report.get('metadata_json'):
            try:
                report['metadata'] = json.loads(report['metadata_json'])
            except json.JSONDecodeError:
                pass

        return {'report': report}
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# HTTP API (used by the lab server to poll for work and submit results)
# ---------------------------------------------------------------------------
# These are exposed as Starlette routes, mounted by server.py

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def http_get_pending(request: Request) -> JSONResponse:
    """Lab server polls this for pending hypotheses."""
    await _init_db()
    db = await _get_db()
    try:
        rows = await db.execute_fetchall(
            'SELECT id, hypothesis, motivation, domain, context_json, callback_email, created_at '
            'FROM hypotheses WHERE status = ? ORDER BY created_at ASC LIMIT 10',
            ('pending',),
        )
        hypotheses = []
        for r in rows:
            h = _row_to_dict(r)
            if h.get('context_json'):
                try:
                    h['context'] = json.loads(h['context_json'])
                except json.JSONDecodeError:
                    pass
            hypotheses.append(h)

        return JSONResponse({'hypotheses': hypotheses, 'count': len(hypotheses)})
    finally:
        await db.close()


async def http_claim_hypothesis(request: Request) -> JSONResponse:
    """Lab server claims a hypothesis (atomically sets status=processing)."""
    hypothesis_id = request.path_params['hypothesis_id']
    await _init_db()
    db = await _get_db()
    try:
        # Atomic claim: only update if still pending
        cursor = await db.execute(
            "UPDATE hypotheses SET status = 'processing', updated_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (_now(), hypothesis_id),
        )
        await db.commit()

        if cursor.rowcount == 0:
            return JSONResponse(
                {'error': 'Hypothesis not available (already claimed or not found)'},
                status_code=409,
            )

        return JSONResponse({'claimed': hypothesis_id, 'status': 'processing'})
    finally:
        await db.close()


async def http_submit_report(request: Request) -> JSONResponse:
    """Lab server submits a completed report."""
    await _init_db()
    body = await request.json()

    required = ['hypothesis_id', 'verdict', 'confidence', 'full_report']
    missing = [f for f in required if f not in body]
    if missing:
        return JSONResponse({'error': f'Missing fields: {missing}'}, status_code=400)

    report_id = str(uuid.uuid4())[:8]
    db = await _get_db()
    try:
        await db.execute(
            '''INSERT INTO reports (id, hypothesis_id, verdict, confidence,
               decision_readiness, executive_summary, full_report, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (report_id, body['hypothesis_id'], body['verdict'],
             body['confidence'], body.get('decision_readiness', 'DIRECTIONAL'),
             body.get('executive_summary', ''), body['full_report'],
             json.dumps(body.get('metadata', {}))),
        )

        # Mark hypothesis as completed
        status = 'completed' if body['verdict'] != 'FAILED' else 'failed'
        await db.execute(
            'UPDATE hypotheses SET status = ?, updated_at = ? WHERE id = ?',
            (status, _now(), body['hypothesis_id']),
        )

        await db.commit()

        # Fetch hypothesis text for the notification
        row = await db.execute_fetchall(
            'SELECT hypothesis FROM hypotheses WHERE id = ?', (body['hypothesis_id'],),
        )
        hyp_text = row[0]['hypothesis'][:80] if row else body['hypothesis_id']
    finally:
        await db.close()

    # Push notification
    verdict = body['verdict']
    confidence = body['confidence']
    level = 'error' if verdict == 'FAILED' else 'info'
    title = f"Lab report: {verdict} (confidence {confidence}/5)"
    try:
        port = os.environ.get('ROUTER_PORT', '8080')
        httpx.post(
            f'http://127.0.0.1:{port}/notifications/push',
            json={
                'level': level,
                'source': 'lab',
                'title': title,
                'body': hyp_text,
                'metadata': {'report_id': report_id, 'hypothesis_id': body['hypothesis_id'], 'verdict': verdict},
            },
            timeout=2,
        )
    except Exception:
        logger.warning("Failed to push lab report notification: %s", title, exc_info=True)

    return JSONResponse({'report_id': report_id, 'status': status})


async def http_get_documents(request: Request) -> JSONResponse:
    """Lab server fetches documents attached to a hypothesis."""
    hypothesis_id = request.path_params['hypothesis_id']
    await _init_db()
    db = await _get_db()
    try:
        rows = await db.execute_fetchall(
            'SELECT * FROM documents WHERE hypothesis_id = ?',
            (hypothesis_id,),
        )
        documents = [_row_to_dict(r) for r in rows]
        return JSONResponse({'documents': documents, 'count': len(documents)})
    finally:
        await db.close()


async def http_update_status(request: Request) -> JSONResponse:
    """Lab server updates hypothesis status (e.g. to 'failed')."""
    hypothesis_id = request.path_params['hypothesis_id']
    body = await request.json()
    new_status = body.get('status')
    if new_status not in ('processing', 'completed', 'failed'):
        return JSONResponse({'error': 'Invalid status'}, status_code=400)

    await _init_db()
    db = await _get_db()
    try:
        await db.execute(
            'UPDATE hypotheses SET status = ?, updated_at = ? WHERE id = ?',
            (new_status, _now(), hypothesis_id),
        )
        await db.commit()
        return JSONResponse({'hypothesis_id': hypothesis_id, 'status': new_status})
    finally:
        await db.close()


async def http_digest(request: Request) -> JSONResponse:
    """Digest-friendly summary: outstanding work + recent completed reports."""
    await _init_db()
    db = await _get_db()
    try:
        # Outstanding: pending + processing
        outstanding = await db.execute_fetchall(
            "SELECT id, status, hypothesis, created_at FROM hypotheses "
            "WHERE status IN ('pending', 'processing') ORDER BY created_at ASC",
        )

        # Recently completed reports (last 24h, max 3)
        recent = await db.execute_fetchall(
            "SELECT r.id, r.verdict, r.confidence, r.executive_summary, "
            "h.hypothesis, r.created_at "
            "FROM reports r JOIN hypotheses h ON r.hypothesis_id = h.id "
            "WHERE r.created_at > datetime('now', '-1 day') "
            "ORDER BY r.created_at DESC LIMIT 3",
        )

        return JSONResponse({
            'outstanding': [_row_to_dict(r) for r in outstanding],
            'outstanding_count': len(outstanding),
            'recent_reports': [_row_to_dict(r) for r in recent],
            'recent_count': len(recent),
        })
    finally:
        await db.close()


# Starlette routes for the lab HTTP API
lab_http_routes = [
    Route('/lab/pending', http_get_pending, methods=['GET']),
    Route('/lab/claim/{hypothesis_id}', http_claim_hypothesis, methods=['POST']),
    Route('/lab/report', http_submit_report, methods=['POST']),
    Route('/lab/documents/{hypothesis_id}', http_get_documents, methods=['GET']),
    Route('/lab/status/{hypothesis_id}', http_update_status, methods=['POST']),
    Route('/lab/digest', http_digest, methods=['GET']),
]
