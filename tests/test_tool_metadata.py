"""Contract tests for ChatGPT-facing MCP tool metadata."""

from fastmcp.tools import Tool

from tool_metadata import (
    DESTRUCTIVE_OPEN,
    KNOWN_TOOL_NAMES,
    ToolMetadataMiddleware,
    add_tool_metadata,
    safety_profile,
    tool_title,
)


async def test_complete_router_catalog_has_explicit_metadata():
    from router.server import router

    router_tools = await router.get_tools()
    assert set(router_tools) == KNOWN_TOOL_NAMES

    middleware = ToolMetadataMiddleware()

    async def list_tools(_context):
        return list(router_tools.values())

    decorated = await middleware.on_list_tools(None, list_tools)
    assert len(decorated) == len(router_tools)
    for tool in decorated:
        assert tool.title
        assert tool.annotations is not None
        assert tool.annotations.title == tool.title
        assert isinstance(tool.annotations.readOnlyHint, bool)
        assert isinstance(tool.annotations.destructiveHint, bool)
        assert isinstance(tool.annotations.idempotentHint, bool)
        assert isinstance(tool.annotations.openWorldHint, bool)


def test_titles_are_human_readable():
    assert tool_title("health") == "Check Router Health"
    assert tool_title("email_list_emails") == "Email: List Emails"
    assert tool_title("finance_sync_status") == "Finance: Get Sync Status"
    assert tool_title("gtasks_board") == "Google Tasks: View Preparation Board"
    assert tool_title("cartesia_tts") == "Cartesia: Synthesize Speech"


def test_existing_metadata_wins():
    original = Tool.from_function(
        lambda: None,
        name="future_tool",
        title="Explicit title",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    decorated = add_tool_metadata(original)
    assert decorated.title == "Explicit title"
    assert decorated.annotations == original.annotations


def test_unknown_tools_fail_safe():
    assert safety_profile("future_unknown_tool") == DESTRUCTIVE_OPEN
