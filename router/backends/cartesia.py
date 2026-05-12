"""Cartesia TTS backend.

Exposes a simple text-to-speech wrapper over Cartesia's /tts/bytes endpoint
in two shapes:

  - MCP tool: cartesia_tts(text) -> {audio_base64, format, sample_rate, bytes}
  - HTTP route: GET /tts?text=...  -> raw audio/wav bytes

The HTTP route is intended for Tailscale-only use; it's added to
AuthMiddleware.EXEMPT_PREFIXES in server.py so callers don't need the
MCP secret.
"""

import base64
import os

import aiohttp
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

mcp = FastMCP('cartesia')

CARTESIA_API_KEY = os.environ.get('CARTESIA_API_KEY', '')
CARTESIA_VOICE_ID = os.environ.get(
    'CARTESIA_VOICE_ID', '71a7ad14-091c-4e8e-a314-022ece01c121'
)
CARTESIA_MODEL_ID = os.environ.get('CARTESIA_MODEL_ID', 'sonic-2')
CARTESIA_VERSION = '2026-03-01'
CARTESIA_URL = 'https://api.cartesia.ai/tts/bytes'

OUTPUT_FORMAT = {
    'container': 'wav',
    'encoding': 'pcm_s16le',
    'sample_rate': 44100,
}


async def _synthesize(text: str) -> bytes:
    """Call Cartesia /tts/bytes and return raw WAV audio."""
    if not CARTESIA_API_KEY:
        raise RuntimeError('CARTESIA_API_KEY not set')
    if not text or not text.strip():
        raise ValueError('text must be non-empty')

    payload = {
        'model_id': CARTESIA_MODEL_ID,
        'transcript': text,
        'voice': {'mode': 'id', 'id': CARTESIA_VOICE_ID},
        'output_format': OUTPUT_FORMAT,
        'language': 'en',
    }
    headers = {
        'Authorization': f'Bearer {CARTESIA_API_KEY}',
        'Cartesia-Version': CARTESIA_VERSION,
        'Content-Type': 'application/json',
    }

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(CARTESIA_URL, json=payload, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f'Cartesia API {resp.status}: {body[:500]}')
            return await resp.read()


@mcp.tool()
async def tts(text: str) -> dict:
    """Synthesize speech from text using Cartesia.

    Returns the audio as base64-encoded WAV plus format metadata. Decode
    `audio_base64` to get a playable .wav file.

    Args:
        text: The text to speak.
    """
    audio = await _synthesize(text)
    return {
        'audio_base64': base64.b64encode(audio).decode('ascii'),
        'format': 'wav',
        'sample_rate': OUTPUT_FORMAT['sample_rate'],
        'bytes': len(audio),
    }


async def http_tts(request: Request) -> Response:
    """GET /tts?text=...  -> raw audio/wav bytes."""
    text = request.query_params.get('text', '')
    if not text.strip():
        return JSONResponse({'error': 'text query parameter required'}, status_code=400)
    try:
        audio = await _synthesize(text)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=502)
    return Response(audio, media_type='audio/wav')


cartesia_http_routes = [
    Route('/tts', http_tts, methods=['GET']),
]
