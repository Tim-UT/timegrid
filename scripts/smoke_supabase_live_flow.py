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
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault('MASTODON_CLIENT_ID', 'dummy')
os.environ.setdefault('MASTODON_CLIENT_SECRET', 'dummy')
os.environ.setdefault('TIMEGRID_ENABLE_TEST_LOGIN', 'true')


def require_supabase_env() -> None:
    missing = [
        name
        for name in ('SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY')
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(f'missing required env: {", ".join(missing)}')
    os.environ['TIMEGRID_STORAGE'] = 'supabase'


require_supabase_env()

import app  # noqa: E402
from timegrid_storage import quote_filter, table_url  # noqa: E402


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip('/')
        self.cookies = ''

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, bytes, dict[str, str]]:
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if self.cookies:
            headers['Cookie'] = self.cookies
        req = urllib.request.Request(
            f'{self.base_url}{path}',
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
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


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def supabase_count(table: str, query: str) -> int:
    assert app.STORAGE is not None
    resp = app.STORAGE.session.get(
        table_url(app.STORAGE.url, table, query=f'select=*&{query}'),
        headers={'Prefer': 'count=exact'},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise AssertionError(f'{table} count failed {resp.status_code}: {resp.text[:500]}')
    content_range = resp.headers.get('Content-Range') or ''
    if '/' in content_range:
        return int(content_range.rsplit('/', 1)[1])
    return len(resp.json())


def delete_temp_user(acct: str) -> None:
    assert app.STORAGE is not None
    app.STORAGE.delete_key('timegrid_users', ('acct',), (acct,))
    app.STORE_CACHE = None
    app.pending_auth = {}
    app.sessions = {}


def assert_no_temp_rows(acct: str) -> None:
    owner = f'owner_acct=eq.{quote_filter(acct)}'
    acct_filter = f'acct=eq.{quote_filter(acct)}'
    assert supabase_count('timegrid_users', acct_filter) == 0
    assert supabase_count('timegrid_calendars', owner) == 0
    assert supabase_count('timegrid_timelines', owner) == 0
    assert supabase_count('timegrid_subscriptions', owner) == 0
    assert supabase_count('timegrid_exports', acct_filter) == 0
    assert supabase_count('timegrid_auth_sessions', acct_filter) == 0


def main() -> int:
    if app.STORAGE is None:
        raise AssertionError('TIMEGRID_STORAGE=supabase is required')

    acct = f'codexlive{int(time.time() * 1000)}'
    port = free_port()
    app.APP_BASE_URL = f'http://127.0.0.1:{port}'
    app.STORE_CACHE = None
    app.pending_auth = {}
    app.sessions = {}
    server = ThreadingHTTPServer(('127.0.0.1', port), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = Client(app.APP_BASE_URL)

    try:
        login = client.json('POST', '/api/dev/test-login', {
            'acct': acct,
            'display_name': 'Codex Live Supabase Smoke',
        })
        assert login.get('user', {}).get('acct') == acct

        calendar = client.json('POST', f'/api/personal/{acct}/calendars', {
            'title': 'Live Supabase Smoke',
            'workspace': 'personal',
        })['calendar']
        target_calendar = client.json('POST', f'/api/personal/{acct}/calendars', {
            'title': 'Moved Smoke',
            'workspace': 'personal',
        })['calendar']
        created = client.json('POST', f'/api/personal/{acct}/timelines', {
            'title': 'Live smoke timeline',
            'description': 'Created by smoke_supabase_live_flow.py',
            'calendar_id': calendar['id'],
            'workspace': 'personal',
            'events': [{
                'id': 'evt_live_smoke_1',
                'title': 'Live smoke event',
                'start': '2026-08-01T14:00:00Z',
                'end': '2026-08-01T15:00:00Z',
                'description': '',
                'location': 'Browser Lab',
                'url': '',
                'recurrence': None,
                'exdates': [],
                'overrides': [],
            }],
        })
        timeline = created['timeline']
        subscription = created['subscription']
        assert supabase_count('timegrid_timelines', f'id=eq.{quote_filter(timeline["id"])}') == 1
        assert supabase_count('timegrid_subscriptions', f'id=eq.{quote_filter(subscription["id"])}') == 1

        dynamic = client.json('POST', f'/api/personal/{acct}/exports', {
            'mode': 'dynamic',
            'calendar_id': calendar['id'],
        })
        export_path = urllib.parse.urlparse(dynamic['url']).path
        status, body, _headers = client.request('GET', export_path)
        assert status == 200 and b'Live smoke event' in body

        edited = dict(timeline)
        edited['events'] = list(timeline.get('events') or []) + [{
            'id': 'evt_live_smoke_2',
            'title': 'Live smoke edited event',
            'start': '2026-08-02T14:00:00Z',
            'end': '2026-08-02T15:00:00Z',
            'description': '',
            'location': 'Browser Lab',
            'url': '',
            'recurrence': None,
            'exdates': [],
            'overrides': [],
        }]
        client.json('PATCH', f'/api/personal/{acct}/timelines/{urllib.parse.quote(timeline["id"])}', edited)
        status, body, _headers = client.request('GET', export_path)
        assert status == 200 and b'Live smoke edited event' in body

        moved = client.json('PATCH', f'/api/personal/{acct}/subscriptions/{urllib.parse.quote(subscription["id"])}', {
            'calendar_id': target_calendar['id'],
            'workspace': 'personal',
            'position': 0,
        })
        assert moved['calendar_id'] == target_calendar['id']
        status, body, _headers = client.request('GET', export_path)
        assert status == 200 and b'Live smoke edited event' not in body
        status, csv_body, _headers = client.request('GET', f'/api/personal/{acct}/exports/current.csv?calendar_id={urllib.parse.quote(target_calendar["id"])}')
        assert status == 200 and b'Live smoke edited event' in csv_body

        client.json('DELETE', f'/api/personal/{acct}/subscriptions/{urllib.parse.quote(subscription["id"])}?mode=permanent')
        assert supabase_count('timegrid_subscriptions', f'id=eq.{quote_filter(subscription["id"])}') == 0
        assert supabase_count('timegrid_timelines', f'id=eq.{quote_filter(timeline["id"])}') == 0

        print(json.dumps({
            'ok': True,
            'acct': acct,
            'calendar_id': calendar['id'],
            'target_calendar_id': target_calendar['id'],
            'dynamic_export': dynamic['url'],
        }, indent=2))
    finally:
        try:
            delete_temp_user(acct)
            assert_no_temp_rows(acct)
        finally:
            server.shutdown()
            thread.join(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
