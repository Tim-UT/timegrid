#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from generate_sample_store import build_store


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault('MASTODON_CLIENT_ID', 'dummy')
os.environ.setdefault('MASTODON_CLIENT_SECRET', 'dummy')
os.environ.setdefault('TIMEGRID_ENABLE_TEST_LOGIN', 'true')
os.environ.setdefault('SUPABASE_URL', 'https://example.supabase.co')
os.environ.setdefault('SUPABASE_ANON_KEY', 'dummy-anon-key')
os.environ.setdefault('TIMEGRID_ENABLE_EMAIL_AUTH', 'false')
os.environ.setdefault('TIMEGRID_ENABLE_EXTERNAL_AUTH', 'false')
os.environ.setdefault('GOOGLE_CLIENT_ID', 'dummy-google-client')
os.environ.setdefault('GOOGLE_CLIENT_SECRET', 'dummy-google-secret')
os.environ.setdefault('APPLE_CLIENT_ID', 'dummy-apple-client')
os.environ.setdefault('APPLE_CLIENT_SECRET', 'dummy-apple-secret')
os.environ.setdefault('UOFT_OIDC_DISCOVERY_URL', 'https://example.invalid/.well-known/openid-configuration')
os.environ.setdefault('UOFT_CLIENT_ID', 'dummy-uoft-client')
os.environ.setdefault('UOFT_CLIENT_SECRET', 'dummy-uoft-secret')

import app  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip('/')
        self.cookies = ''

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, bytes, dict[str, str]]:
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if self.cookies:
            headers['Cookie'] = self.cookies
        req = urllib.request.Request(f'{self.base_url}{path}', data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                response_headers = dict(resp.headers)
                cookie = response_headers.get('Set-Cookie')
                if cookie:
                    self.cookies = cookie.split(';', 1)[0]
                return resp.status, resp.read(), response_headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def text(self, path: str) -> str:
        status, body, _headers = self.request('GET', path)
        if status >= 400:
            raise AssertionError(f'GET {path} failed {status}: {body[:500]!r}')
        return body.decode('utf-8')

    def json(self, method: str, path: str, payload: dict | None = None) -> dict:
        status, body, _headers = self.request(method, path, payload)
        if status >= 400:
            raise AssertionError(f'{method} {path} failed {status}: {body[:500]!r}')
        return json.loads(body.decode('utf-8'))


def main() -> int:
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        app.DATA_DIR = tmp_path
        app.DATA_FILE = tmp_path / 'store.json'
        app.AUTH_STATE_FILE = tmp_path / 'auth-state.json'
        app.APP_BASE_URL = 'http://127.0.0.1'
        app.write_json_file(app.DATA_FILE, build_store(users=2, timelines_per_user=3, events_per_timeline=3))
        app.pending_auth = {}
        app.sessions = {
            'ui_smoke_session': {
                'acct': 'sample1',
                'account_id': '',
                'display_name': 'Sample User 1',
                'avatar': '',
                'role': 'admin',
                'created_at': time.time(),
                'auth_provider': 'smoke',
                'access_token': '',
                'max_age': 3600,
            }
        }

        port = free_port()
        app.APP_BASE_URL = f'http://127.0.0.1:{port}'
        server = ThreadingHTTPServer(('127.0.0.1', port), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = Client(app.APP_BASE_URL)
        client.cookies = f'{app.SESSION_COOKIE}=ui_smoke_session'
        try:
            auth_html = client.text('/auth?next=%2F')
            assert 'Continue with Mastodon' in auth_html
            assert 'auth-email-form' not in auth_html
            assert 'type="email"' not in auth_html
            assert 'Continue with Google' not in auth_html
            assert 'Continue with Apple' not in auth_html

            auth_options = client.json('GET', '/api/auth/options?next=%2F')
            providers = auth_options.get('providers') or []
            assert [item.get('id') for item in providers] == ['mastodon'], providers

            status, _body, _headers = client.request('POST', '/api/auth/email/signup', {
                'email': 'student@example.com',
                'password': 'correct horse battery staple',
                'display_name': 'Student Example',
            })
            assert status == 404, f'email signup should be disabled in Mastodon-only phase, got {status}'
            status, _body, _headers = client.request('POST', '/api/auth/email/login', {
                'email': 'student@example.com',
                'password': 'correct horse battery staple',
            })
            assert status == 404, f'email login should be disabled in Mastodon-only phase, got {status}'
            status, _body, _headers = client.request('POST', '/api/auth/supabase/session', {
                'access_token': 'dummy-token',
            })
            assert status == 404, f'Supabase token session should be disabled in Mastodon-only phase, got {status}'

            app_js = client.text('/app.js')
            for marker in [
                'data-action="export-calendar"',
                'data-action="switch-calendar"',
                'data-action="create-calendar"',
                'data-draggable-subscription="true"',
                'Generate link',
            ]:
                assert marker in app_js, f'missing frontend UI marker: {marker}'

            created = client.json('POST', '/api/personal/sample1/calendars', {
                'title': 'UI export chooser',
                'workspace': 'personal',
            })['calendar']
            workspace = client.json('GET', f'/api/personal/sample1?calendar_id={urllib.parse.quote(created["id"])}')
            assert workspace['active_calendar_id'] == created['id']
            assert len(workspace.get('calendars') or []) >= 2
            assert any(item['id'] == created['id'] for item in workspace['calendars'])

            print({
                'ok': True,
                'auth': 'mastodon-only',
                'calendars': len(workspace['calendars']),
                'active_calendar_id': workspace['active_calendar_id'],
            })
        finally:
            server.shutdown()
            thread.join(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
