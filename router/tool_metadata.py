"""ChatGPT-compatible titles and safety annotations for router tools.

FastMCP's mounted servers retain each backend's tool metadata.  This middleware
adds the presentation and safety fields at the aggregate router boundary, where
the fully-prefixed tool name is available.  Existing backend implementations and
direct calls remain unchanged.
"""

from dataclasses import dataclass

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations


@dataclass(frozen=True)
class SafetyProfile:
    """The four MCP safety hints advertised for a tool."""

    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool


READ_PRIVATE = SafetyProfile(True, False, True, False)
READ_OPEN = SafetyProfile(True, False, True, True)
WRITE_PRIVATE = SafetyProfile(False, False, False, False)
IDEMPOTENT_MUTATION_PRIVATE = SafetyProfile(False, True, True, False)
DESTRUCTIVE_PRIVATE = SafetyProfile(False, True, False, False)
WRITE_OPEN = SafetyProfile(False, False, False, True)
DESTRUCTIVE_OPEN = SafetyProfile(False, True, False, True)


_PROFILE_TOOLS: dict[SafetyProfile, frozenset[str]] = {
    READ_PRIVATE: frozenset({
        "health",
        "logs",
        "email_list_emails",
        "email_search_emails",
        "email_get_email",
        "finance_summary",
        "finance_accounts",
        "finance_transactions",
        "finance_changes",
        "finance_sync_status",
        "gtasks_lists",
        "gtasks_board",
        "cartesia_tts",
    }),
    READ_OPEN: frozenset({
        "web_search",
        "web_get_contents",
    }),
    IDEMPOTENT_MUTATION_PRIVATE: frozenset({
        "gtasks_seed",
    }),
    DESTRUCTIVE_PRIVATE: frozenset({
        "gtasks_tasks",
    }),
    WRITE_OPEN: frozenset({
        "email_send_email",
    }),
}

KNOWN_TOOL_NAMES = frozenset(
    tool_name
    for tool_names in _PROFILE_TOOLS.values()
    for tool_name in tool_names
)

_PREFIX_LABELS = {
    "email": "Email",
    "web": "Web",
    "cartesia": "Cartesia",
    "finance": "Finance",
    "gtasks": "Google Tasks",
}

_ACTION_TITLES = {
    "health": "Check Router Health",
    "logs": "Read Router Logs",
    "summary": "Get Summary",
    "accounts": "List Accounts",
    "transactions": "List Transactions",
    "changes": "List Changes",
    "sync_status": "Get Sync Status",
    "lists": "List Task Lists",
    "board": "View Preparation Board",
    "tasks": "Manage Tasks",
    "projects": "Manage Projects and Sections",
    "seed": "Seed Preparation Curriculum",
    "tts": "Synthesize Speech",
}

_TOKEN_LABELS = {
    "id": "ID",
    "ids": "IDs",
    "tts": "TTS",
    "url": "URL",
}


def tool_title(tool_name: str) -> str:
    """Create a stable, human-readable title from a mounted tool name."""

    if tool_name in _ACTION_TITLES:
        return _ACTION_TITLES[tool_name]

    prefix, separator, action = tool_name.partition("_")
    if not separator:
        return " ".join(
            _TOKEN_LABELS.get(token, token.capitalize())
            for token in tool_name.split("_")
        )

    action_title = _ACTION_TITLES.get(action)
    if action_title is None:
        action_title = " ".join(
            _TOKEN_LABELS.get(token, token.capitalize())
            for token in action.split("_")
        )
    return f"{_PREFIX_LABELS.get(prefix, prefix.capitalize())}: {action_title}"


def safety_profile(tool_name: str) -> SafetyProfile:
    """Return explicit hints, with a conservative fallback for future tools."""

    for profile, tool_names in _PROFILE_TOOLS.items():
        if tool_name in tool_names:
            return profile
    return DESTRUCTIVE_OPEN


def add_tool_metadata(tool: Tool) -> Tool:
    """Return a copy with missing ChatGPT-facing metadata populated."""

    title = tool.title or tool_title(tool.name)
    if tool.annotations is not None:
        annotations = tool.annotations
    else:
        profile = safety_profile(tool.name)
        annotations = ToolAnnotations(
            title=title,
            readOnlyHint=profile.read_only,
            destructiveHint=profile.destructive,
            idempotentHint=profile.idempotent,
            openWorldHint=profile.open_world,
        )
    return tool.model_copy(update={"title": title, "annotations": annotations})


class ToolMetadataMiddleware(Middleware):
    """Decorate the complete mounted tool catalog before it is serialized."""

    async def on_list_tools(
        self,
        context: MiddlewareContext,
        call_next: CallNext,
    ) -> list[Tool]:
        tools = await call_next(context)
        return [add_tool_metadata(tool) for tool in tools]
