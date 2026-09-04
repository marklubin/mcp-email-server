#!/usr/bin/env python3
"""One-time Google Tasks authorization for the router's gtasks backend.

Runs the OAuth "Desktop app" loopback flow with PKCE against Mark's Google account and
writes an env snippet the router reads. Standard library only, so it runs anywhere with
Python 3.

    python3 scripts/gtasks_auth.py ~/.config/job-search-gtasks/client_secret.json

Prints a consent URL, waits for the redirect on 127.0.0.1:<port>, exchanges the code,
then writes GOOGLE_TASKS_CLIENT_ID / GOOGLE_TASKS_CLIENT_SECRET / GOOGLE_TASKS_REFRESH_TOKEN
to --out (default ~/.config/job-search-gtasks/gtasks.env, mode 0600). It never prints the
token. Append that file's lines to the router's .env and restart the router.

The OAuth app must be published ("In production") in the Google Cloud console; a
"Testing" app's refresh tokens expire after seven days.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

AUTH_URI = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URI = 'https://oauth2.googleapis.com/token'
SCOPE = 'https://www.googleapis.com/auth/tasks'


def load_client(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding='utf-8'))
    client = raw.get('installed') or raw.get('web') or raw
    if 'client_id' not in client or 'client_secret' not in client:
        raise SystemExit(f'{path}: not an OAuth client JSON (no client_id/client_secret)')
    return client


def build_auth_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPE,
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    return AUTH_URI + '?' + urllib.parse.urlencode(params)


def exchange(client: dict[str, str], code: str, redirect_uri: str, verifier: str) -> dict:
    body = urllib.parse.urlencode(
        {
            'code': code,
            'client_id': client['client_id'],
            'client_secret': client['client_secret'],
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
            'code_verifier': verifier,
        }
    ).encode()
    request = urllib.request.Request(client.get('token_uri', TOKEN_URI), data=body, method='POST')
    request.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f'token exchange failed ({exc.code}): {exc.read().decode(errors="replace")}')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('client_secret_json', type=Path)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--out', type=Path, default=Path('~/.config/job-search-gtasks/gtasks.env').expanduser())
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()

    client = load_client(args.client_secret_json)
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b'=').decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    state = secrets.token_urlsafe(16)
    redirect_uri = f'http://127.0.0.1:{args.port}/'
    url = build_auth_url(client['client_id'], redirect_uri, state, challenge)

    received: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            received.update({key: values[0] for key, values in query.items()})
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'Google Tasks authorization received. You can close this tab.\n')
            done.set()

        def log_message(self, *_: object) -> None:
            return

    server = http.server.HTTPServer(('127.0.0.1', args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print('Open this URL in a browser signed in to the Google account that owns the task lists:\n')
    print(url + '\n')
    print(f'The consent page redirects to {redirect_uri}. Open it on this machine, or forward the port '
          f'first with: ssh -L {args.port}:127.0.0.1:{args.port} <this-host>')
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception as exc:  # the URL is printed anyway
            print(f'(could not open a browser automatically: {exc})', file=sys.stderr)
    done.wait(timeout=600)
    server.shutdown()
    if not done.is_set():
        raise SystemExit('no authorization response within 10 minutes')
    if received.get('state') != state:
        raise SystemExit('authorization response had a mismatched state; run again')
    if 'code' not in received:
        raise SystemExit(f"authorization failed: {received.get('error', 'no code returned')}")

    token = exchange(client, received['code'], redirect_uri, verifier)
    if 'refresh_token' not in token:
        raise SystemExit('Google returned no refresh token; revoke the app under the Google account\'s '
                         'third-party access and run again')

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        f"GOOGLE_TASKS_CLIENT_ID={client['client_id']}\n"
        f"GOOGLE_TASKS_CLIENT_SECRET={client['client_secret']}\n"
        f"GOOGLE_TASKS_REFRESH_TOKEN={token['refresh_token']}\n",
        encoding='utf-8',
    )
    os.chmod(args.out, 0o600)
    print(f'\nWrote {args.out} (mode 0600). Append its three lines to the router .env and restart the router.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
