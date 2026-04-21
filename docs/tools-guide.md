# MCP Router Tools — Quick Reference

Call tools as `mcp__<server>__<name>` with JSON args. Pass only documented parameters.

## Web search

- `web_search` — `{"query": "...", "num_results": 5, "include_text": true}` — set `include_text` to skip a follow-up fetch.
- `web_get_contents` — `{"urls": ["https://..."], "max_chars": 4000}` — only for URLs you didn't already get text for.

## Email

- `email_list_emails` — `{"mailbox": "INBOX", "limit": 20}`
- `email_search_emails` — `{"query": "invoice", "search_body": false}` — `search_body: true` is slower, use only when subject/sender aren't enough.
- `email_get_email` — `{"message_id": "<id>"}`
- `email_send_email` — `{"to": "...", "subject": "...", "body": "..."}`

## Notifications

- `notify_push` — `{"level": "info|warn|error|success", "source": "your-agent-name", "title": "...", "body": "..."}` — high-signal only.
- `notify_list` — `{"unread_only": true, "limit": 20}`

## Todoist (action-dispatched — first arg is always `action`)

`todoist_tasks` actions: `list`, `get`, `create`, `update`, `delete`, `complete`, `reopen`
- `{"action": "list", "filter": "today"}`
- `{"action": "create", "content": "Buy milk", "due_string": "tomorrow", "priority": 3}` — priority 1=low, 4=high
- `{"action": "complete", "task_id": "123"}`

`todoist_projects` actions: `list`, `get`, `create`, `update`, `delete`, `list_sections`, `add_section`, `delete_section`

Use `due_string` (natural language) OR `due_date` (ISO), never both.

## Memory

- `memory_search` — `{"query": "...", "limit": 10}`
- `memory_get_context` — `{"name": "context-doc"}`
- `memory_ingest` — `{"bucket": "notes", "content": "...", "filename": "2026-04-20.md"}`

## Browser (only when no API is available)

Strict sequence, refs expire on navigation/click:

1. `browser_navigate` → `{"url": "..."}`
2. `browser_get_rendered_content` → returns refs like `btn-0`, `input-2`
3. `browser_act` → `{"ref": "btn-0"}` or `{"ref": "input-2", "text": "hello"}`

Re-run step 2 after every act. Only use refs from the most recent step 2.

If a tool returns `{"error": "..."}`, read it. Don't retry unchanged.
