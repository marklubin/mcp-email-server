"""Discord backend - REST API v9 with user token, browser token extraction.

Provides MCP tools and HTTP API for Discord operations.
Token comes from DISCORD_TOKEN env var, set_token(), or extract_token() (browser CDP).
"""

import logging
import os
from typing import Optional

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

DISCORD_API = 'https://discord.com/api/v9'
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', '')

_token: str = DISCORD_TOKEN  # Mutable at runtime

mcp = FastMCP('discord')


# ---------------------------------------------------------------------------
# API helper
# ---------------------------------------------------------------------------

async def _discord_api(
    method: str,
    endpoint: str,
    params: dict | None = None,
    json_body: dict | None = None,
) -> tuple[dict | list | None, str | None]:
    """Make a request to Discord REST API v9.

    Returns:
        Tuple of (response_data, error_message)
    """
    if not _token:
        return None, 'No Discord token configured. Use set_token() or extract_token().'

    url = f'{DISCORD_API}/{endpoint}'

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method,
                url,
                headers={'Authorization': _token},
                params=params,
                json=json_body,
                timeout=10,
            )

            if resp.status_code == 204:
                return None, None

            if resp.status_code >= 400:
                return None, f'Discord API error {resp.status_code}: {resp.text}'

            return resp.json(), None

        except httpx.TimeoutException:
            return None, 'Request timed out'
        except Exception as e:
            logger.exception('Discord API request failed: %s %s', method, endpoint)
            return None, str(e)


# ---------------------------------------------------------------------------
# Message normalizer
# ---------------------------------------------------------------------------

def _message_to_dict(
    msg: dict, guild_id: str, channel_id: str, channel_name: str = '',
) -> dict:
    """Convert Discord API message JSON to pipeline-standard format."""
    msg_id = msg['id']
    author = msg.get('author', {})
    reactions = msg.get('reactions', [])
    reaction_count = sum(r.get('count', 0) for r in reactions)

    return {
        'uri': f'discord:{guild_id}/{channel_id}/{msg_id}',
        'external_id': f'discord:{guild_id}/{channel_id}/{msg_id}',
        'url': f'https://discord.com/channels/{guild_id}/{channel_id}/{msg_id}',
        'author': {
            'handle': author.get('username', 'unknown'),
            'display_name': author.get('global_name') or author.get('username', 'unknown'),
        },
        'text': msg.get('content', ''),
        'created_at': msg.get('timestamp', ''),
        'like_count': reaction_count,
        'reply_count': 0,
        'channel_name': channel_name,
    }


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def validate_token() -> dict:
    """Validate the current Discord token by calling /users/@me.

    Returns:
        Username, user ID, and status, or error if token is invalid.
    """
    data, err = await _discord_api('GET', 'users/@me')
    if err:
        return {'error': err}
    return {
        'status': 'ok',
        'username': data.get('username'),
        'user_id': data.get('id'),
    }


@mcp.tool()
async def list_guilds() -> dict:
    """List Discord servers the user is a member of.

    Returns:
        List of guilds with id, name, and owner status.
    """
    data, err = await _discord_api('GET', 'users/@me/guilds')
    if err:
        return {'error': err}
    guilds = [
        {'id': g['id'], 'name': g.get('name', ''), 'owner': g.get('owner', False)}
        for g in data
    ]
    return {'guilds': guilds}


@mcp.tool()
async def list_channels(guild_id: str) -> dict:
    """List channels in a Discord server.

    Args:
        guild_id: The server/guild ID.

    Returns:
        List of text/announcement channels sorted by position.
    """
    data, err = await _discord_api('GET', f'guilds/{guild_id}/channels')
    if err:
        return {'error': err}
    channels = [
        {
            'id': ch['id'],
            'name': ch.get('name', ''),
            'type': ch.get('type', 0),
            'position': ch.get('position', 0),
            'parent_id': ch.get('parent_id'),
        }
        for ch in data
        if ch.get('type') in (0, 5)
    ]
    channels.sort(key=lambda c: c['position'])
    return {'channels': channels}


@mcp.tool()
async def get_messages(
    channel_id: str,
    limit: int = 50,
    guild_id: str = '',
    channel_name: str = '',
) -> dict:
    """Fetch messages from a Discord channel.

    Args:
        channel_id: The channel ID.
        limit: Number of messages to fetch (max 100).
        guild_id: Server ID (for building URIs).
        channel_name: Channel name (for metadata).

    Returns:
        List of messages in pipeline-standard format.
    """
    limit = min(max(1, limit), 100)
    data, err = await _discord_api('GET', f'channels/{channel_id}/messages', params={'limit': limit})
    if err:
        return {'error': err}
    messages = [_message_to_dict(m, guild_id, channel_id, channel_name) for m in data]
    return {'messages': messages}


@mcp.tool()
async def send_message(channel_id: str, content: str) -> dict:
    """Send a message to a Discord channel.

    Args:
        channel_id: The channel ID to post into.
        content: Message text.

    Returns:
        The sent message in pipeline-standard format.
    """
    data, err = await _discord_api('POST', f'channels/{channel_id}/messages', json_body={'content': content})
    if err:
        return {'error': err}
    return {'message': data}


@mcp.tool()
async def delete_message(channel_id: str, message_id: str) -> dict:
    """Delete a message from a Discord channel.

    Args:
        channel_id: The channel ID.
        message_id: The message ID to delete.

    Returns:
        Success status.
    """
    _, err = await _discord_api('DELETE', f'channels/{channel_id}/messages/{message_id}')
    if err:
        return {'error': err}
    return {'success': True}


@mcp.tool()
async def set_token(token: str) -> dict:
    """Set the Discord token at runtime and validate it.

    Args:
        token: The Discord user token.

    Returns:
        Validation result with username if successful.
    """
    global _token
    _token = token

    data, err = await _discord_api('GET', 'users/@me')
    if err:
        _token = ''
        return {'error': f'Token validation failed: {err}'}

    return {
        'status': 'ok',
        'username': data.get('username'),
        'user_id': data.get('id'),
    }


@mcp.tool()
async def extract_token() -> dict:
    """Extract Discord token from the browser's logged-in Discord session.

    Uses the router's Chrome CDP instance to pull the token from Discord's
    webpack internals. Requires Discord to be logged in via the browser.

    Returns:
        Token extraction result with username if successful.
    """
    try:
        from backends.browser import get_browser

        browser = await get_browser()
        context = browser.contexts[0] if browser.contexts else None
        if not context:
            return {'error': 'No browser context available'}

        # Look for existing Discord tab
        page = None
        for p in context.pages:
            if 'discord.com' in p.url:
                page = p
                break

        # If no Discord tab, navigate a new one
        if not page:
            page = await context.new_page()
            await page.goto('https://discord.com/channels/@me', wait_until='networkidle', timeout=15000)

        # Extract token via webpack internals
        token = await page.evaluate("""() => {
            try {
                let m;
                webpackChunkdiscord_app.push([[''],{},e=>{m=[];for(let c in e.c)m.push(e.c[c])}]);
                return m.find(m=>m?.exports?.default?.getToken!==void 0).exports.default.getToken();
            } catch(e) { return null; }
        }""")

        if not token:
            return {'error': 'Could not extract token. Is Discord logged in?'}

        global _token
        _token = token

        # Validate it
        data, err = await _discord_api('GET', 'users/@me')
        if err:
            return {'error': f'Extracted token but validation failed: {err}'}

        return {
            'status': 'ok',
            'username': data.get('username'),
            'source': 'browser',
        }

    except Exception as e:
        logger.exception('Failed to extract Discord token from browser')
        return {'error': f'Browser token extraction failed: {e}'}


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def http_validate(request: Request) -> JSONResponse:
    """Validate the current Discord token."""
    data, err = await _discord_api('GET', 'users/@me')
    if err:
        return JSONResponse({'error': err}, status_code=502)
    return JSONResponse({
        'status': 'ok',
        'username': data.get('username'),
        'user_id': data.get('id'),
    })


async def http_list_guilds(request: Request) -> JSONResponse:
    """List servers the user is in."""
    data, err = await _discord_api('GET', 'users/@me/guilds')
    if err:
        return JSONResponse({'error': err}, status_code=502)
    guilds = [
        {'id': g['id'], 'name': g.get('name', ''), 'owner': g.get('owner', False)}
        for g in data
    ]
    return JSONResponse({'guilds': guilds})


async def http_list_channels(request: Request) -> JSONResponse:
    """List text channels in a guild."""
    guild_id = request.path_params['guild_id']
    data, err = await _discord_api('GET', f'guilds/{guild_id}/channels')
    if err:
        return JSONResponse({'error': err}, status_code=502)
    channels = [
        {
            'id': ch['id'],
            'name': ch.get('name', ''),
            'type': ch.get('type', 0),
            'position': ch.get('position', 0),
            'parent_id': ch.get('parent_id'),
        }
        for ch in data
        if ch.get('type') in (0, 5)
    ]
    channels.sort(key=lambda c: c['position'])
    return JSONResponse({'channels': channels})


async def http_get_messages(request: Request) -> JSONResponse:
    """Fetch messages from a channel."""
    channel_id = request.path_params['channel_id']
    limit = min(int(request.query_params.get('limit', '50')), 100)
    guild_id = request.query_params.get('guild_id', '')
    channel_name = request.query_params.get('channel_name', '')

    data, err = await _discord_api(
        'GET', f'channels/{channel_id}/messages', params={'limit': limit},
    )
    if err:
        return JSONResponse({'error': err}, status_code=502)

    messages = [_message_to_dict(m, guild_id, channel_id, channel_name) for m in data]
    return JSONResponse({'messages': messages})


async def http_send_message(request: Request) -> JSONResponse:
    """Send a message to a channel. Body: {"content": "..."}"""
    channel_id = request.path_params['channel_id']
    body = await request.json()
    content = body.get('content')
    if not content:
        return JSONResponse({'error': 'Missing "content" field'}, status_code=400)

    data, err = await _discord_api(
        'POST', f'channels/{channel_id}/messages', json_body={'content': content},
    )
    if err:
        return JSONResponse({'error': err}, status_code=502)
    return JSONResponse({'message': data})


async def http_delete_message(request: Request) -> JSONResponse:
    """Delete a message."""
    channel_id = request.path_params['channel_id']
    message_id = request.path_params['message_id']

    _, err = await _discord_api('DELETE', f'channels/{channel_id}/messages/{message_id}')
    if err:
        return JSONResponse({'error': err}, status_code=502)
    return JSONResponse({'success': True})


# Starlette routes for the Discord HTTP API
discord_http_routes = [
    Route('/discord/validate', http_validate, methods=['GET']),
    Route('/discord/guilds', http_list_guilds, methods=['GET']),
    Route('/discord/guilds/{guild_id}/channels', http_list_channels, methods=['GET']),
    Route('/discord/channels/{channel_id}/messages', http_get_messages, methods=['GET']),
    Route('/discord/channels/{channel_id}/messages', http_send_message, methods=['POST']),
    Route('/discord/channels/{channel_id}/messages/{message_id}', http_delete_message, methods=['DELETE']),
]
