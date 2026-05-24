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
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, TypeVar

from generate_sample_store import build_store


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault('MASTODON_CLIENT_ID', 'dummy')
os.environ.setdefault('MASTODON_CLIENT_SECRET', 'dummy')

import app  # noqa: E402


T = TypeVar('T')
BUDGET_MS = float(os.environ.get('TIMEGRID_PERF_BUDGET_MS', '500'))
REMOTE_CALENDAR_URL = 'https://example.com/timegrid-perf-cache.ics'
REMOTE_CALENDAR_TEXT = '''BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:remote-perf-1
SUMMARY:Remote cached performance event
DTSTART:20260704T140000Z
DTEND:20260704T150000Z
END:VEVENT
END:VCALENDAR
'''


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def generated_events(count: int) -> list[dict]:
    start = datetime(2026, 7, 1, 13, tzinfo=timezone.utc)
    events = []
    for index in range(count):
        event_start = start + timedelta(days=index % 30, hours=index // 30)
        events.append({
            'id': f'perf_evt_{index + 1}',
            'title': f'Performance event {index + 1}',
            'start': iso(event_start),
            'end': iso(event_start + timedelta(hours=1)),
            'description': 'Synthetic event for TimeGrid performance budget smoke.',
            'location': 'TimeGrid Lab',
            'url': '',
            'recurrence': None if index % 4 else {'freq': 'weekly', 'count': 3},
            'exdates': [],
            'overrides': [],
        })
    return events


class Client:
    def __init__(self, base_url: str, session_id: str) -> None:
        self.base_url = base_url.rstrip('/')
        self.session_id = session_id

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, bytes, dict[str, str]]:
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            f'{self.base_url}{path}',
            data=data,
            method=method,
            headers={
                'Content-Type': 'application/json',
                'Cookie': f'{app.SESSION_COOKIE}={self.session_id}',
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def json(self, method: str, path: str, payload: dict | None = None) -> dict:
        status, body, _headers = self.request(method, path, payload)
        if status >= 400:
            raise AssertionError(f'{method} {path} failed {status}: {body[:500]!r}')
        return json.loads(body.decode('utf-8'))


def main() -> int:
    timings: dict[str, float] = {}

    def timed(name: str, fn: Callable[[], T]) -> T:
        start = time.perf_counter()
        result = fn()
        elapsed_ms = (time.perf_counter() - start) * 1000
        timings[name] = round(elapsed_ms, 2)
        if elapsed_ms > BUDGET_MS:
            raise AssertionError(f'{name} took {elapsed_ms:.1f}ms, over budget {BUDGET_MS:.1f}ms')
        return result

    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        app.DATA_DIR = tmp_path
        app.DATA_FILE = tmp_path / 'store.json'
        app.AUTH_STATE_FILE = tmp_path / 'auth-state.json'
        app.APP_BASE_URL = 'http://127.0.0.1'
        app.write_json_file(app.DATA_FILE, build_store(users=12, timelines_per_user=18, events_per_timeline=18))
        app.pending_auth = {}
        app.sessions = {
            'perf_smoke_session': {
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
        app.CALENDAR_TEXT_CACHE.clear()
        original_requests_get = app.requests.get
        remote_calls: list[tuple[str, float]] = []

        class FakeRemoteCalendarResponse:
            text = REMOTE_CALENDAR_TEXT

            def raise_for_status(self) -> None:
                return None

        def fake_requests_get(url: str, timeout: float = 20, **kwargs: object) -> object:
            if url == REMOTE_CALENDAR_URL:
                remote_calls.append((url, timeout))
                time.sleep(0.12)
                return FakeRemoteCalendarResponse()
            return original_requests_get(url, timeout=timeout, **kwargs)

        app.requests.get = fake_requests_get  # type: ignore[assignment]

        port = free_port()
        app.APP_BASE_URL = f'http://127.0.0.1:{port}'
        server = ThreadingHTTPServer(('127.0.0.1', port), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = Client(app.APP_BASE_URL, 'perf_smoke_session')
        try:
            workspace = timed('initial_workspace_get', lambda: client.json('GET', '/api/personal/sample1'))
            assert workspace['subscriptions'], 'expected seeded subscriptions'

            primary_calendar = timed('create_calendar', lambda: client.json('POST', '/api/personal/sample1/calendars', {
                'title': 'Performance primary',
                'workspace': 'personal',
            })['calendar'])
            target_calendar = timed('create_target_calendar', lambda: client.json('POST', '/api/personal/sample1/calendars', {
                'title': 'Performance target',
                'workspace': 'personal',
            })['calendar'])

            active_workspace = timed(
                'switch_calendar_get',
                lambda: client.json('GET', f'/api/personal/sample1?calendar_id={urllib.parse.quote(primary_calendar["id"])}'),
            )
            assert active_workspace['active_calendar_id'] == primary_calendar['id']

            imported = timed('create_timeline_with_events', lambda: client.json('POST', '/api/personal/sample1/timelines', {
                'title': 'Performance timeline',
                'description': 'Synthetic timeline created by performance smoke.',
                'calendar_id': primary_calendar['id'],
                'workspace': 'personal',
                'events': generated_events(48),
            }))
            subscription = imported['subscription']
            timeline = imported['timeline']

            moved = timed('move_subscription_to_calendar', lambda: client.json(
                'PATCH',
                f'/api/personal/sample1/subscriptions/{urllib.parse.quote(subscription["id"])}',
                {
                    'calendar_id': target_calendar['id'],
                    'workspace': 'personal',
                    'position': 0,
                },
            ))
            assert moved['calendar_id'] == target_calendar['id']

            moved_workspace = timed(
                'moved_calendar_get',
                lambda: client.json('GET', f'/api/personal/sample1?calendar_id={urllib.parse.quote(target_calendar["id"])}'),
            )
            assert any(item['id'] == timeline['id'] for item in moved_workspace['timelines'])

            remote_subscription = timed('create_remote_subscription', lambda: client.json('POST', '/api/personal/sample1/subscriptions', {
                'title': 'Remote cached performance source',
                'url': REMOTE_CALENDAR_URL,
                'calendar_id': target_calendar['id'],
                'workspace': 'personal',
            }))
            source_path = f'/api/personal/sample1/subscriptions/{urllib.parse.quote(remote_subscription["id"])}/source'
            status, body, _headers = timed('first_source_proxy_fetch', lambda: client.request('GET', source_path))
            assert status == 200 and b'Remote cached performance event' in body
            assert len(remote_calls) == 1, remote_calls

            status, body, _headers = timed('cached_source_proxy_fetch', lambda: client.request('GET', source_path))
            assert status == 200 and b'Remote cached performance event' in body
            assert len(remote_calls) == 1, 'cached source proxy fetch should not hit the remote URL again'

            csv_path = f'/api/personal/sample1/exports/current.csv?calendar_id={urllib.parse.quote(target_calendar["id"])}'
            status, body, _headers = timed('current_csv_uses_source_cache', lambda: client.request('GET', csv_path))
            assert status == 200 and b'Remote cached performance event' in body
            assert len(remote_calls) == 1, 'CSV export should reuse the already cached source text'

            status, body, _headers = timed('repeated_current_csv_uses_source_cache', lambda: client.request('GET', csv_path))
            assert status == 200 and b'Remote cached performance event' in body
            assert len(remote_calls) == 1, 'repeated CSV export should not hit the remote URL again'

            export_record = timed('create_dynamic_export', lambda: client.json('POST', '/api/personal/sample1/exports', {
                'mode': 'dynamic',
                'calendar_id': target_calendar['id'],
            }))
            export_path = urllib.parse.urlparse(export_record['url']).path
            status, body, _headers = timed('download_dynamic_export', lambda: client.request('GET', export_path))
            assert status == 200 and b'Performance event 1' in body
            assert b'Remote cached performance event' in body
            assert len(remote_calls) == 1, 'dynamic export download should reuse the warmed remote source cache'

            print(json.dumps({
                'ok': True,
                'budget_ms': BUDGET_MS,
                'remote_fetch_calls': remote_calls,
                'timings_ms': timings,
            }, indent=2))
        finally:
            app.requests.get = original_requests_get  # type: ignore[assignment]
            app.CALENDAR_TEXT_CACHE.clear()
            server.shutdown()
            thread.join(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
