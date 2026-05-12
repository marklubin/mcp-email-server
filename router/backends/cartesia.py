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
import logging
import os
import struct
import time

import aiohttp

logger = logging.getLogger(__name__)
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

mcp = FastMCP('cartesia')

CARTESIA_API_KEY = os.environ.get('CARTESIA_API_KEY', '')
CARTESIA_VOICE_ID = os.environ.get(
    'CARTESIA_VOICE_ID', '71a7ad14-091c-4e8e-a314-022ece01c121'
)
CARTESIA_MODEL_ID = os.environ.get('CARTESIA_MODEL_ID', 'sonic-3')
CARTESIA_DEFAULT_SPEED = float(os.environ.get('CARTESIA_SPEED', '0.85'))
CARTESIA_VERSION = '2026-03-01'
CARTESIA_URL = 'https://api.cartesia.ai/tts/bytes'

# Cartesia accepts generation_config.speed in [0.6, 1.5]; clamp to be safe.
SPEED_MIN, SPEED_MAX = 0.6, 1.5

OUTPUT_FORMAT = {
    'container': 'wav',
    'encoding': 'pcm_s16le',
    'sample_rate': 44100,
}


def _fix_wav_sizes(audio: bytes) -> bytes:
    # Cartesia streams audio and leaves RIFF and `data` chunk sizes as
    # 0xFFFFFFFF, which makes many players stop after a fraction of a
    # second. Rewrite both with real sizes derived from the buffer length.
    if len(audio) < 12 or audio[:4] != b'RIFF' or audio[8:12] != b'WAVE':
        return audio
    data_idx = audio.find(b'data')
    if data_idx < 0 or data_idx + 8 > len(audio):
        return audio
    buf = bytearray(audio)
    buf[4:8] = struct.pack('<I', len(audio) - 8)
    buf[data_idx + 4:data_idx + 8] = struct.pack('<I', len(audio) - (data_idx + 8))
    return bytes(buf)


async def _synthesize(text: str, speed: float | None = None) -> bytes:
    """Call Cartesia /tts/bytes and return raw WAV audio."""
    if not CARTESIA_API_KEY:
        raise RuntimeError('CARTESIA_API_KEY not set')
    if not text or not text.strip():
        raise ValueError('text must be non-empty')

    eff_speed = CARTESIA_DEFAULT_SPEED if speed is None else float(speed)
    eff_speed = max(SPEED_MIN, min(SPEED_MAX, eff_speed))

    payload = {
        'model_id': CARTESIA_MODEL_ID,
        'transcript': text,
        'voice': {'mode': 'id', 'id': CARTESIA_VOICE_ID},
        'output_format': OUTPUT_FORMAT,
        'language': 'en',
        'generation_config': {'speed': eff_speed},
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
            audio = await resp.read()
    return _fix_wav_sizes(audio)


@mcp.tool()
async def tts(text: str, speed: float | None = None) -> dict:
    """Synthesize speech from text using Cartesia.

    Returns the audio as base64-encoded WAV plus format metadata. Decode
    `audio_base64` to get a playable .wav file.

    Args:
        text: The text to speak.
        speed: Optional playback speed in [0.6, 1.5]. 1.0 is normal pace,
            lower is slower. Defaults to the CARTESIA_SPEED env var (0.85).
    """
    audio = await _synthesize(text, speed=speed)
    return {
        'audio_base64': base64.b64encode(audio).decode('ascii'),
        'format': 'wav',
        'sample_rate': OUTPUT_FORMAT['sample_rate'],
        'bytes': len(audio),
    }


async def http_tts(request: Request) -> Response:
    """GET /tts?text=...  -> raw audio/wav bytes."""
    started = time.monotonic()
    ua = request.headers.get('user-agent', '-')
    accept = request.headers.get('accept', '-')
    rng = request.headers.get('range', '-')
    client = f'{request.client.host}:{request.client.port}' if request.client else '-'
    text = request.query_params.get('text', '')
    speed_param = request.query_params.get('speed')
    try:
        speed = float(speed_param) if speed_param else None
    except ValueError:
        return JSONResponse({'error': f'speed must be a number, got: {speed_param!r}'}, status_code=400)
    logger.info(
        'tts request method=%s client=%s ua=%r accept=%r range=%r text_len=%d speed=%s',
        request.method, client, ua, accept, rng, len(text), speed,
    )
    if not text.strip():
        logger.warning('tts 400: empty text')
        return JSONResponse({'error': 'text query parameter required'}, status_code=400)
    try:
        audio = await _synthesize(text, speed=speed)
    except Exception as e:
        logger.exception('tts 502: cartesia call failed')
        return JSONResponse({'error': str(e)}, status_code=502)
    elapsed = time.monotonic() - started
    riff_size = struct.unpack('<I', audio[4:8])[0] if len(audio) >= 8 else -1
    logger.info(
        'tts 200 bytes=%d riff_size=%d elapsed=%.2fs',
        len(audio), riff_size, elapsed,
    )
    headers = {
        'Content-Length': str(len(audio)),
        'Content-Disposition': 'inline; filename="speech.wav"',
        'Accept-Ranges': 'none',
        'Cache-Control': 'no-store',
    }
    return Response(audio, media_type='audio/wav', headers=headers)


cartesia_http_routes = [
    Route('/tts', http_tts, methods=['GET']),
]
