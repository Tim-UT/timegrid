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

import app  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


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
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        app.DATA_DIR = tmp_path
        app.DATA_FILE = tmp_path / 'store.json'
        app.AUTH_STATE_FILE = tmp_path / 'auth-state.json'
        app.APP_BASE_URL = 'http://127.0.0.1'
        app.write_json_file(app.DATA_FILE, build_store(users=4, timelines_per_user=4, events_per_timeline=5))
        app.pending_auth = {}
        app.sessions = {
            'smoke_session': {
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
        client = Client(app.APP_BASE_URL, 'smoke_session')
        try:
            workspace = client.json('GET', '/api/personal/sample1')
            assert workspace['subscriptions'], 'expected seeded personal subscriptions'
            assert workspace['calendars'], 'expected default calendars'

            created_calendar = client.json('POST', '/api/personal/sample1/calendars', {
                'title': 'Assignments',
                'workspace': 'personal',
            })['calendar']
            calendar_id = created_calendar['id']
            duplicate_work_calendar = client.json('POST', '/api/personal/sample1/calendars', {
                'title': 'Work',
                'workspace': 'personal',
            })['calendar']
            duplicate_work_calendar_2 = client.json('POST', '/api/personal/sample1/calendars', {
                'title': 'Work',
                'workspace': 'personal',
            })['calendar']
            assert duplicate_work_calendar['title'] == 'Work'
            assert duplicate_work_calendar_2['title'] == 'Work 2'
            duplicate_workspace = client.json('GET', f'/api/personal/sample1?calendar_id={urllib.parse.quote(duplicate_work_calendar_2["id"])}')
            assert duplicate_workspace['active_calendar_id'] == duplicate_work_calendar_2['id']
            assert any(item['id'] == duplicate_work_calendar_2['id'] for item in duplicate_workspace['calendars']), 'duplicate calendar should be visible immediately after reload'
            url_subscription = client.json('POST', '/api/personal/sample1/subscriptions', {
                'title': 'Plain URL source',
                'url': 'https://example.com/plain-url-source.ics',
                'calendar_id': calendar_id,
                'workspace': 'personal',
            })
            reordered_url_subscription = client.json('PATCH', f'/api/personal/sample1/subscriptions/{urllib.parse.quote(url_subscription["id"])}', {
                'position': 1,
            })
            assert reordered_url_subscription['id'] == url_subscription['id'], 'plain URL subscription reorder should not fail'
            overflow_calendar = client.json('POST', '/api/personal/sample1/calendars', {
                'title': 'Overflow',
                'workspace': 'personal',
            })['calendar']
            creator_calendar = client.json('POST', '/api/personal/sample1/calendars', {
                'title': 'Creator releases',
                'workspace': 'creator',
            })['calendar']
            moved_calendar = client.json('PATCH', f'/api/personal/sample1/calendars/{urllib.parse.quote(overflow_calendar["id"])}', {
                'position': 0,
            })['calendar']
            assert moved_calendar['id'] == overflow_calendar['id']

            imported = client.json('POST', '/api/personal/sample1/timelines', {
                'title': 'Imported exams',
                'description': 'Imported through smoke test',
                'calendar_id': calendar_id,
                'workspace': 'personal',
                'events': [
                    {
                        'id': 'evt_midterm',
                        'title': 'Midterm',
                        'start': '2026-06-10T14:00:00Z',
                        'end': '2026-06-10T15:00:00Z',
                        'description': '',
                        'location': 'Room 101',
                        'url': '',
                        'recurrence': None,
                        'exdates': [],
                        'overrides': [],
                    }
                ],
            })
            timeline = imported['timeline']
            subscription = imported['subscription']
            assert timeline['calendar_id'] == calendar_id

            selected_workspace = client.json('GET', f'/api/personal/sample1?calendar_id={urllib.parse.quote(calendar_id)}')
            assert selected_workspace['active_calendar_id'] == calendar_id
            assert len(selected_workspace['timelines']) == 1

            creator_imported = client.json('POST', '/api/personal/sample1/timelines', {
                'title': 'Creator launch plan',
                'description': 'Creator calendar smoke test',
                'calendar_id': creator_calendar['id'],
                'workspace': 'creator',
                'events': [
                    {
                        'id': 'evt_creator_launch',
                        'title': 'Creator launch',
                        'start': '2026-07-01T14:00:00Z',
                        'end': '2026-07-01T15:00:00Z',
                        'description': '',
                        'location': 'Studio',
                        'url': '',
                        'recurrence': None,
                        'exdates': [],
                        'overrides': [],
                    }
                ],
            })
            assert creator_imported['timeline']['calendar_id'] == creator_calendar['id']
            creator_workspace = client.json('GET', f'/api/creator/sample1?calendar_id={urllib.parse.quote(creator_calendar["id"])}')
            assert creator_workspace['active_calendar_id'] == creator_calendar['id']
            assert any(item['id'] == creator_imported['timeline']['id'] for item in creator_workspace['timelines']), 'creator calendar should contain its new timeline'
            creator_subscription_id = creator_imported['subscription']['id']

            published = client.json('POST', '/api/personal/sample1/published', {
                'title': 'Creator release bundle',
                'subscription_ids': [creator_subscription_id],
                'visibility': 'invited',
                'invited': ['sample2@example.com'],
                'hashtags': '#release #smoke',
                'calendar_id': creator_calendar['id'],
            })
            assert published['slug'], 'published bundle should return a slug'
            assert published['calendar_id'] == creator_calendar['id']
            assert published['visibility'] == 'invited'
            assert 'release' in published['hashtags']

            managed = client.json('PATCH', f'/api/personal/sample1/published/{urllib.parse.quote(published["slug"])}', {
                'visibility': 'private',
                'invited': [],
                'hashtags': '#private #qa',
            })
            assert managed['visibility'] == 'private'
            assert managed['invited'] == []
            assert 'qa' in managed['hashtags']

            archived_bundle = client.json('DELETE', f'/api/personal/sample1/published/{urllib.parse.quote(published["slug"])}?mode=archive')
            assert archived_bundle['mode'] == 'archive'
            archive_workspace = client.json('GET', '/api/archive/sample1')
            assert any(item['slug'] == published['slug'] for item in archive_workspace.get('archived_published', [])), 'archived published bundle should appear in archive workspace'

            dynamic = client.json('POST', '/api/personal/sample1/exports', {
                'mode': 'dynamic',
                'calendar_id': calendar_id,
            })
            static = client.json('POST', '/api/personal/sample1/exports', {
                'mode': 'static',
                'calendar_id': calendar_id,
            })
            dynamic_path = urllib.parse.urlparse(dynamic['url']).path
            static_path = urllib.parse.urlparse(static['url']).path

            status, body, _ = client.request('GET', dynamic_path)
            assert status == 200 and b'Midterm' in body

            edited_events = timeline['events'] + [{
                'id': 'evt_final',
                'title': 'Final exam',
                'start': '2026-06-20T14:00:00Z',
                'end': '2026-06-20T16:00:00Z',
                'description': '',
                'location': 'Room 202',
                'url': '',
                'recurrence': None,
                'exdates': [],
                'overrides': [],
            }]
            updated = client.json('PATCH', f'/api/personal/sample1/timelines/{timeline["id"]}', {
                'title': timeline['title'],
                'description': timeline.get('description') or '',
                'calendar_id': calendar_id,
                'workspace': 'personal',
                'events': edited_events,
            })
            assert len(updated['timeline']['events']) == 2

            status, dynamic_body, _ = client.request('GET', dynamic_path)
            assert status == 200 and b'Final exam' in dynamic_body, 'dynamic export should update after edit'

            status, static_body, _ = client.request('GET', static_path)
            assert status == 200 and b'Midterm' in static_body and b'Final exam' not in static_body, 'static export should stay frozen'

            status, csv_body, _ = client.request('GET', f'/api/personal/sample1/exports/current.csv?calendar_id={urllib.parse.quote(calendar_id)}')
            assert status == 200 and b'Final exam' in csv_body

            moved = client.json('PATCH', f'/api/personal/sample1/subscriptions/{urllib.parse.quote(subscription["id"])}', {
                'calendar_id': overflow_calendar['id'],
                'workspace': 'personal',
                'position': 0,
            })
            assert moved['calendar_id'] == overflow_calendar['id']
            moved_workspace = client.json('GET', f'/api/personal/sample1?calendar_id={urllib.parse.quote(overflow_calendar["id"])}')
            assert moved_workspace['active_calendar_id'] == overflow_calendar['id']
            assert any(item['id'] == timeline['id'] for item in moved_workspace['timelines']), 'timeline should move with dragged subscription'

            status, dynamic_after_move_body, _ = client.request('GET', dynamic_path)
            assert status == 200 and b'Final exam' not in dynamic_after_move_body, 'dynamic export should follow the selected calendar after a timeline moves away'

            status, moved_csv_body, _ = client.request('GET', f'/api/personal/sample1/exports/current.csv?calendar_id={urllib.parse.quote(overflow_calendar["id"])}')
            assert status == 200 and b'Final exam' in moved_csv_body, 'moved calendar export should include the moved timeline'

            detached = client.json('DELETE', f'/api/personal/sample1/subscriptions/{urllib.parse.quote(subscription["id"])}')
            assert detached['mode'] == 'detach'
            detached_workspace = client.json('GET', f'/api/personal/sample1?calendar_id={urllib.parse.quote(overflow_calendar["id"])}')
            assert all(item['id'] != timeline['id'] for item in detached_workspace['timelines']), 'detached timeline should disappear from workspace'

            print(json.dumps({
                'ok': True,
                'calendar_id': calendar_id,
                'creator_calendar_id': creator_calendar['id'],
                'published_slug': published['slug'],
                'moved_calendar_id': overflow_calendar['id'],
                'timeline_id': timeline['id'],
                'dynamic_export': dynamic['url'],
                'static_export': static['url'],
            }, indent=2))
        finally:
            server.shutdown()
            thread.join(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
