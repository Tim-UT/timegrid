#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from generate_sample_store import build_store


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault('MASTODON_CLIENT_ID', 'dummy')
os.environ.setdefault('MASTODON_CLIENT_SECRET', 'dummy')
os.environ.setdefault('SUPABASE_URL', 'https://example.supabase.co')
os.environ.setdefault('SUPABASE_ANON_KEY', 'dummy-anon-key')
os.environ['TIMEGRID_ENABLE_EMAIL_AUTH'] = 'true'
os.environ['TIMEGRID_ENABLE_EXTERNAL_AUTH'] = 'true'

import app  # noqa: E402


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.headers: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.text)


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

    def json(self, method: str, path: str, payload: dict | None = None) -> dict:
        status, body, _headers = self.request(method, path, payload)
        if status >= 400:
            raise AssertionError(f'{method} {path} failed {status}: {body[:500]!r}')
        return json.loads(body.decode('utf-8'))


def fake_auth_user(access_token: str) -> dict[str, Any]:
    if access_token.startswith('token_signup'):
        return {
            'id': 'supabase-email-student',
            'email': 'student@example.edu',
            'email_confirmed_at': '2026-05-24T00:00:00Z',
            'app_metadata': {'provider': 'email'},
            'user_metadata': {'display_name': 'Student Example', 'full_name': 'Student Example'},
            'identities': [{'provider': 'email', 'id': 'supabase-email-student'}],
        }
    if access_token.startswith('token_login'):
        return {
            'id': 'supabase-email-student',
            'email': 'student@example.edu',
            'email_confirmed_at': '2026-05-24T00:00:00Z',
            'app_metadata': {'provider': 'email'},
            'user_metadata': {'display_name': 'Student Example', 'full_name': 'Student Example'},
            'identities': [{'provider': 'email', 'id': 'supabase-email-student'}],
        }
    if access_token == 'token_google_browser':
        return {
            'id': 'supabase-google-student',
            'email': 'google.student@example.edu',
            'email_confirmed_at': '2026-05-24T00:00:00Z',
            'app_metadata': {'provider': 'google'},
            'user_metadata': {'name': 'Google Student', 'avatar_url': 'https://example.edu/avatar.png'},
            'identities': [{'provider': 'google', 'id': 'supabase-google-student'}],
        }
    raise RuntimeError('unexpected access token')


def main() -> int:
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        app.DATA_DIR = tmp_path
        app.DATA_FILE = tmp_path / 'store.json'
        app.AUTH_STATE_FILE = tmp_path / 'auth-state.json'
        app.STORE_CACHE = None
        app.pending_auth = {}
        app.sessions = {}
        app.write_json_file(app.DATA_FILE, build_store(users=1, timelines_per_user=1, events_per_timeline=1))

        original_post = app.requests.post
        original_get = app.requests.get

        def fake_post(url: str, headers: dict | None = None, json: dict | None = None, timeout: float = 20, **kwargs: object) -> FakeResponse:
            if url.endswith('/auth/v1/signup'):
                assert (json or {}).get('email') == 'student@example.edu'
                assert '/auth?next=' in (json or {}).get('redirect_to', '')
                return FakeResponse(200, {'session': {'access_token': 'token_signup_student'}, 'user': {'id': 'supabase-email-student'}})
            if url.endswith('/auth/v1/token?grant_type=password'):
                assert (json or {}).get('email') == 'student@example.edu'
                return FakeResponse(200, {'access_token': 'token_login_student'})
            return original_post(url, headers=headers, json=json, timeout=timeout, **kwargs)

        def fake_get(url: str, headers: dict | None = None, timeout: float = 20, **kwargs: object) -> FakeResponse:
            if url.endswith('/auth/v1/user'):
                token = str((headers or {}).get('Authorization') or '').replace('Bearer ', '')
                return FakeResponse(200, fake_auth_user(token))
            return original_get(url, headers=headers, timeout=timeout, **kwargs)

        app.requests.post = fake_post  # type: ignore[assignment]
        app.requests.get = fake_get  # type: ignore[assignment]

        port = free_port()
        app.APP_BASE_URL = f'http://127.0.0.1:{port}'
        server = ThreadingHTTPServer(('127.0.0.1', port), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = Client(app.APP_BASE_URL)
        try:
            signup = client.json('POST', '/api/auth/email/signup', {
                'email': 'student@example.edu',
                'password': 'correct horse battery staple',
                'display_name': 'Student Example',
                'next': '/u/student',
            })
            assert signup['ok'] is True
            assert signup['user']['acct'] == 'student-example'
            assert client.cookies.startswith(f'{app.SESSION_COOKIE}=')

            client.request('POST', '/auth/logout')
            login = client.json('POST', '/api/auth/email/login', {
                'email': 'student@example.edu',
                'password': 'correct horse battery staple',
                'next': '/u/student',
            })
            assert login['ok'] is True
            assert login['user']['acct'] == 'student-example'

            client.request('POST', '/auth/logout')
            external = client.json('POST', '/api/auth/supabase/session', {
                'access_token': 'token_google_browser',
                'provider': 'google',
                'next': '/u/google-student',
            })
            assert external['ok'] is True
            assert external['user']['acct'] == 'google-student'

            store = app.load_store()
            student = store['users']['student-example']
            google_student = store['users']['google-student']
            assert any(item.get('provider') == 'email' and item.get('email') == 'student@example.edu' for item in student['linked_identities'])
            assert any(item.get('provider') == 'google' and item.get('email') == 'google.student@example.edu' for item in google_student['linked_identities'])
            assert any(item.get('workspace') == 'personal' for item in student['calendars'])
            assert any(item.get('workspace') == 'creator' for item in student['calendars'])

            print(json.dumps({
                'ok': True,
                'email_example': 'student@example.edu',
                'google_example': 'google.student@example.edu',
                'users': ['student-example', 'google-student'],
            }, indent=2))
        finally:
            app.requests.post = original_post  # type: ignore[assignment]
            app.requests.get = original_get  # type: ignore[assignment]
            server.shutdown()
            thread.join(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
