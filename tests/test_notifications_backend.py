"""Tests for the notifications backend."""

import json
import os
import pytest
from unittest.mock import patch

from backends import notifications


# The @mcp.tool() decorator wraps functions in FunctionTool objects.
# Access the raw async functions via .fn for direct testing.
_push = notifications.push.fn
_list = notifications.list_notifications.fn
_get = notifications.get.fn
_mark_read = notifications.mark_read.fn
_clear = notifications.clear.fn
_summary = notifications.summary.fn


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Use a temp database for each test."""
    db_path = str(tmp_path / "test_notifications.db")
    monkeypatch.setattr(notifications, "NOTIFY_DB_PATH", db_path)
    return db_path


# ---------------------------------------------------------------------------
# MCP Tool tests
# ---------------------------------------------------------------------------

class TestPush:
    """Tests for notify_push MCP tool."""

    async def test_push_creates_notification(self):
        result = await _push(
            level="info", source="test", title="Hello world",
        )
        assert "id" in result
        assert result["level"] == "info"
        assert result["source"] == "test"
        assert result["title"] == "Hello world"

    async def test_push_with_body_and_metadata(self):
        result = await _push(
            level="warning",
            source="blah-radar",
            title="Poll failed",
            body="Connection timeout after 30s",
            metadata={"source_id": "abc123"},
        )
        assert result["level"] == "warning"

        # Verify the metadata round-trips through get
        detail = await _get(result["id"])
        n = detail["notification"]
        assert n["body"] == "Connection timeout after 30s"
        assert n["metadata"]["source_id"] == "abc123"

    async def test_push_invalid_level(self):
        result = await _push(
            level="critical", source="test", title="Nope",
        )
        assert "error" in result

    async def test_push_all_levels(self):
        for level in ("info", "warning", "error"):
            result = await _push(
                level=level, source="test", title=f"Level {level}",
            )
            assert result["level"] == level


class TestList:
    """Tests for notify_list MCP tool."""

    async def test_list_empty(self):
        result = await _list()
        assert result["notifications"] == []
        assert result["count"] == 0

    async def test_list_returns_notifications(self):
        await _push(level="info", source="a", title="First")
        await _push(level="warning", source="b", title="Second")

        result = await _list()
        assert result["count"] == 2

    async def test_list_ordered_newest_first(self):
        r1 = await _push(level="info", source="a", title="First")
        r2 = await _push(level="info", source="a", title="Second")

        result = await _list()
        # Both created in same millisecond, so order by rowid (newest = last insert)
        ids = [n["id"] for n in result["notifications"]]
        assert r2["id"] in ids
        assert r1["id"] in ids
        assert result["count"] == 2

    async def test_list_filter_by_source(self):
        await _push(level="info", source="radar", title="Radar")
        await _push(level="info", source="lab", title="Lab")

        result = await _list(source="radar")
        assert result["count"] == 1
        assert result["notifications"][0]["source"] == "radar"

    async def test_list_filter_by_level(self):
        await _push(level="info", source="a", title="Info")
        await _push(level="error", source="a", title="Error")

        result = await _list(level="error")
        assert result["count"] == 1
        assert result["notifications"][0]["level"] == "error"

    async def test_list_unread_only(self):
        r1 = await _push(level="info", source="a", title="Unread")
        r2 = await _push(level="info", source="a", title="Will be read")

        await _mark_read([r2["id"]])

        result = await _list(unread_only=True)
        assert result["count"] == 1
        assert result["notifications"][0]["id"] == r1["id"]

    async def test_list_respects_limit(self):
        for i in range(5):
            await _push(level="info", source="a", title=f"N{i}")

        result = await _list(limit=2)
        assert result["count"] == 2


class TestGet:
    """Tests for notify_get MCP tool."""

    async def test_get_existing(self):
        pushed = await _push(level="info", source="test", title="Hello")
        result = await _get(pushed["id"])

        assert "notification" in result
        n = result["notification"]
        assert n["id"] == pushed["id"]
        assert n["title"] == "Hello"
        assert n["read_at"] is None

    async def test_get_not_found(self):
        result = await _get("nonexistent")
        assert "error" in result


class TestMarkRead:
    """Tests for notify_mark_read MCP tool."""

    async def test_mark_single_read(self):
        pushed = await _push(level="info", source="a", title="Test")
        result = await _mark_read([pushed["id"]])
        assert result["marked"] == 1

        detail = await _get(pushed["id"])
        assert detail["notification"]["read_at"] is not None

    async def test_mark_multiple_read(self):
        ids = []
        for i in range(3):
            r = await _push(level="info", source="a", title=f"N{i}")
            ids.append(r["id"])

        result = await _mark_read(ids)
        assert result["marked"] == 3

    async def test_mark_already_read_is_noop(self):
        pushed = await _push(level="info", source="a", title="Test")
        await _mark_read([pushed["id"]])
        result = await _mark_read([pushed["id"]])
        assert result["marked"] == 0

    async def test_mark_empty_list(self):
        result = await _mark_read([])
        assert "error" in result


class TestClear:
    """Tests for notify_clear MCP tool."""

    async def test_clear_read_only(self):
        r1 = await _push(level="info", source="a", title="Unread")
        r2 = await _push(level="info", source="a", title="Read")
        await _mark_read([r2["id"]])

        result = await _clear(read_only=True)
        assert result["deleted"] == 1

        # Unread one should still be there
        remaining = await _list()
        assert remaining["count"] == 1
        assert remaining["notifications"][0]["id"] == r1["id"]

    async def test_clear_by_source(self):
        await _push(level="info", source="radar", title="R")
        r2 = await _push(level="info", source="lab", title="L")
        # Mark both as read
        r1_list = await _list(source="radar")
        await _mark_read([r1_list["notifications"][0]["id"], r2["id"]])

        result = await _clear(source="radar", read_only=True)
        assert result["deleted"] == 1

        remaining = await _list()
        assert remaining["count"] == 1
        assert remaining["notifications"][0]["source"] == "lab"


class TestSummary:
    """Tests for notify_summary MCP tool."""

    async def test_summary_empty(self):
        result = await _summary()
        assert result["total_unread"] == 0
        assert result["by_source"] == {}
        assert result["by_level"] == {}

    async def test_summary_counts(self):
        await _push(level="info", source="radar", title="R1")
        await _push(level="info", source="radar", title="R2")
        await _push(level="error", source="lab", title="L1")

        result = await _summary()
        assert result["total_unread"] == 3
        assert result["by_source"] == {"radar": 2, "lab": 1}
        assert result["by_level"] == {"info": 2, "error": 1}

    async def test_summary_excludes_read(self):
        r1 = await _push(level="info", source="a", title="Read")
        await _push(level="info", source="a", title="Unread")
        await _mark_read([r1["id"]])

        result = await _summary()
        assert result["total_unread"] == 1


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------

from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

@pytest.fixture
def client():
    """Create a test client with notification HTTP routes mounted."""
    app = Starlette(routes=notifications.notify_http_routes)
    return TestClient(app)


class TestHTTPPush:
    """Tests for POST /notifications/push."""

    def test_push_ok(self, client):
        resp = client.post("/notifications/push", json={
            "level": "info", "source": "test", "title": "Hello",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["title"] == "Hello"

    def test_push_missing_fields(self, client):
        resp = client.post("/notifications/push", json={"level": "info"})
        assert resp.status_code == 400
        assert "Missing" in resp.json()["error"]

    def test_push_invalid_level(self, client):
        resp = client.post("/notifications/push", json={
            "level": "critical", "source": "test", "title": "Bad",
        })
        assert resp.status_code == 400

    def test_push_with_metadata(self, client):
        resp = client.post("/notifications/push", json={
            "level": "info", "source": "test", "title": "With meta",
            "metadata": {"report_id": "abc"},
        })
        assert resp.status_code == 200
        nid = resp.json()["id"]

        detail = client.get(f"/notifications/{nid}")
        assert detail.json()["notification"]["metadata"]["report_id"] == "abc"


class TestHTTPList:
    """Tests for GET /notifications."""

    def test_list_empty(self, client):
        resp = client.get("/notifications")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_list_with_filters(self, client):
        client.post("/notifications/push", json={
            "level": "info", "source": "radar", "title": "R",
        })
        client.post("/notifications/push", json={
            "level": "error", "source": "lab", "title": "L",
        })

        resp = client.get("/notifications?source=radar")
        assert resp.json()["count"] == 1
        assert resp.json()["notifications"][0]["source"] == "radar"

        resp = client.get("/notifications?level=error")
        assert resp.json()["count"] == 1


class TestHTTPGet:
    """Tests for GET /notifications/{id}."""

    def test_get_existing(self, client):
        push_resp = client.post("/notifications/push", json={
            "level": "info", "source": "test", "title": "Test",
        })
        nid = push_resp.json()["id"]

        resp = client.get(f"/notifications/{nid}")
        assert resp.status_code == 200
        assert resp.json()["notification"]["title"] == "Test"

    def test_get_not_found(self, client):
        resp = client.get("/notifications/nonexistent")
        assert resp.status_code == 404


class TestHTTPMarkRead:
    """Tests for POST /notifications/read."""

    def test_mark_read(self, client):
        push_resp = client.post("/notifications/push", json={
            "level": "info", "source": "test", "title": "Test",
        })
        nid = push_resp.json()["id"]

        resp = client.post("/notifications/read", json={"ids": [nid]})
        assert resp.status_code == 200
        assert resp.json()["marked"] == 1

        # Verify it's now read
        detail = client.get(f"/notifications/{nid}")
        assert detail.json()["notification"]["read_at"] is not None

    def test_mark_read_empty_ids(self, client):
        resp = client.post("/notifications/read", json={"ids": []})
        assert resp.status_code == 400


class TestHTTPSummary:
    """Tests for GET /notifications/summary."""

    def test_summary(self, client):
        client.post("/notifications/push", json={
            "level": "info", "source": "radar", "title": "R",
        })
        client.post("/notifications/push", json={
            "level": "error", "source": "lab", "title": "L",
        })

        resp = client.get("/notifications/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_unread"] == 2
        assert data["by_source"]["radar"] == 1
        assert data["by_level"]["error"] == 1


class TestHTTPEndToEnd:
    """Full round-trip through HTTP endpoints."""

    def test_push_list_read_clear(self, client):
        # Push two notifications
        r1 = client.post("/notifications/push", json={
            "level": "info", "source": "blah-radar",
            "title": "Report ready: 5 signals",
            "metadata": {"report_id": "rpt-001"},
        }).json()
        r2 = client.post("/notifications/push", json={
            "level": "warning", "source": "blah-radar",
            "title": "Failed to poll source abc",
        }).json()

        # List all
        all_notifs = client.get("/notifications").json()
        assert all_notifs["count"] == 2

        # Summary shows 2 unread
        summary = client.get("/notifications/summary").json()
        assert summary["total_unread"] == 2
        assert summary["by_source"]["blah-radar"] == 2

        # Filter unread only
        unread = client.get("/notifications?unread_only=true").json()
        assert unread["count"] == 2

        # Mark one as read
        client.post("/notifications/read", json={"ids": [r1["id"]]})

        # Summary now shows 1 unread
        summary = client.get("/notifications/summary").json()
        assert summary["total_unread"] == 1

        # Unread filter returns only the warning
        unread = client.get("/notifications?unread_only=true").json()
        assert unread["count"] == 1
        assert unread["notifications"][0]["level"] == "warning"
