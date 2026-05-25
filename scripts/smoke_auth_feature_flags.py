#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import argparse
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
    if access_token == 'token_apple_browser':
        return {
            'id': 'supabase-apple-student',
            'email': 'apple.student@example.edu',
            'email_confirmed_at': '2026-05-24T00:00:00Z',
            'app_metadata': {'provider': 'apple'},
            'user_metadata': {'name': 'Apple Student'},
            'identities': [{'provider': 'apple', 'id': 'supabase-apple-student'}],
        }
    raise RuntimeError('unexpected access token')


def install_fake_supabase_auth() -> tuple[Any, Any]:
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
    return original_post, original_get


def restore_requests(original_post: Any, original_get: Any) -> None:
    app.requests.post = original_post  # type: ignore[assignment]
    app.requests.get = original_get  # type: ignore[assignment]


def prepare_fixture_store(tmp_path: Path) -> None:
    app.DATA_DIR = tmp_path
    app.DATA_FILE = tmp_path / 'store.json'
    app.AUTH_STATE_FILE = tmp_path / 'auth-state.json'
    app.STORE_CACHE = None
    app.pending_auth = {}
    app.sessions = {}
    app.write_json_file(app.DATA_FILE, build_store(users=1, timelines_per_user=1, events_per_timeline=1))


def start_fixture_server() -> tuple[ThreadingHTTPServer, threading.Thread, int]:
    port = free_port()
    app.APP_BASE_URL = f'http://127.0.0.1:{port}'
    server = ThreadingHTTPServer(('127.0.0.1', port), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, port


def run_contract(client: Client) -> None:
    options = client.json('GET', '/api/auth/options?next=%2F')
    provider_ids = [item.get('id') for item in options.get('providers') or []]
    assert provider_ids == ['mastodon', 'email', 'google', 'apple'], provider_ids
    assert next(item for item in options['providers'] if item['id'] == 'email')['native_email_auth'] is True
    assert next(item for item in options['providers'] if item['id'] == 'google')['login_href'].startswith('/auth/provider/google/login')
    assert next(item for item in options['providers'] if item['id'] == 'apple')['login_href'].startswith('/auth/provider/apple/login')

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

    client.request('POST', '/auth/logout')
    apple = client.json('POST', '/api/auth/supabase/session', {
        'access_token': 'token_apple_browser',
        'provider': 'apple',
        'next': '/u/apple-student',
    })
    assert apple['ok'] is True
    assert apple['user']['acct'] == 'apple-student'

    store = app.load_store()
    student = store['users']['student-example']
    google_student = store['users']['google-student']
    apple_student = store['users']['apple-student']
    assert any(item.get('provider') == 'email' and item.get('email') == 'student@example.edu' for item in student['linked_identities'])
    assert any(item.get('provider') == 'google' and item.get('email') == 'google.student@example.edu' for item in google_student['linked_identities'])
    assert any(item.get('provider') == 'apple' and item.get('email') == 'apple.student@example.edu' for item in apple_student['linked_identities'])
    assert any(item.get('workspace') == 'personal' for item in student['calendars'])
    assert any(item.get('workspace') == 'creator' for item in student['calendars'])

    print(json.dumps({
        'ok': True,
        'providers': provider_ids,
        'email_example': 'student@example.edu',
        'google_example': 'google.student@example.edu',
        'apple_example': 'apple.student@example.edu',
        'users': ['student-example', 'google-student', 'apple-student'],
    }, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Smoke test feature-flagged Supabase auth.')
    parser.add_argument('--serve-fixture', action='store_true', help='serve the fake Supabase auth fixture for Browser verification')
    args = parser.parse_args(argv)
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        prepare_fixture_store(tmp_path)
        original_post, original_get = install_fake_supabase_auth()
        server, thread, port = start_fixture_server()
        client = Client(app.APP_BASE_URL)
        try:
            if args.serve_fixture:
                print(json.dumps({
                    'ok': True,
                    'fixture': 'auth_feature_flags',
                    'base_url': app.APP_BASE_URL,
                    'email_example': 'student@example.edu',
                    'google_token': 'token_google_browser',
                    'apple_token': 'token_apple_browser',
                }), flush=True)
                while True:
                    time.sleep(3600)
            run_contract(client)
        finally:
            restore_requests(original_post, original_get)
            server.shutdown()
            thread.join(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
