#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from generate_sample_store import build_store
from import_json_to_supabase import transform


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault('MASTODON_CLIENT_ID', 'dummy')
os.environ.setdefault('MASTODON_CLIENT_SECRET', 'dummy')

import app  # noqa: E402


def main() -> int:
    started = time.perf_counter()
    store = build_store(users=40, timelines_per_user=24, events_per_timeline=24)
    acct = 'sample1'
    user = app.ensure_user(store, acct)
    extra_calendar = {
        'id': 'cal_sample1_assignments',
        'workspace': 'personal',
        'title': 'Assignments',
        'color': '#8f5d5d',
        'position': 2,
        'is_default': False,
        'archived': False,
        'created_at': app.now_iso(),
        'updated_at': app.now_iso(),
    }
    user['calendars'].append(extra_calendar)

    moved_titles: list[str] = []
    for timeline in user['timelines'][:4]:
        sub = app.find_subscription(user, timeline.get('subscription_id') or '')
        if sub and sub.get('workspace') == 'personal':
            timeline['calendar_id'] = extra_calendar['id']
            sub['calendar_id'] = extra_calendar['id']
            moved_titles.append(timeline['title'])
    assert moved_titles, 'expected personal timelines moved into extra calendar'

    session = {'acct': acct, 'role': 'admin'}
    default_payload = app.build_workspace_payload(acct, user, store, session, mode='personal', calendar_id=app.default_calendar_id(acct, 'personal'))
    extra_payload = app.build_workspace_payload(acct, user, store, session, mode='personal', calendar_id=extra_calendar['id'])
    assert default_payload['active_calendar_id'] == app.default_calendar_id(acct, 'personal')
    assert extra_payload['active_calendar_id'] == extra_calendar['id']
    assert all(item['calendar_id'] == extra_calendar['id'] for item in extra_payload['timelines'])
    assert moved_titles[0] in {item['title'] for item in extra_payload['timelines']}
    assert moved_titles[0] not in {item['title'] for item in default_payload['timelines']}

    dynamic_record = app.ensure_export_record(store, acct, mode='dynamic', calendar_id=extra_calendar['id'])['record']
    static_snapshot = app.build_personal_export_snapshot(acct, user, store, calendar_id=extra_calendar['id'])
    static_record = app.ensure_export_record(store, acct, mode='static', snapshot=static_snapshot, calendar_id=extra_calendar['id'])['record']
    target_timeline = next(item for item in user['timelines'] if item.get('calendar_id') == extra_calendar['id'])
    target_timeline['events'].append({
        'id': 'evt_large_dynamic_after_edit',
        'title': 'Large dynamic after edit',
        'start': '2026-09-01T14:00:00Z',
        'end': '2026-09-01T15:00:00Z',
        'description': '',
        'location': 'QA room',
        'url': '',
        'recurrence': None,
        'exdates': [],
        'overrides': [],
    })
    dynamic_after = app.build_personal_export_snapshot(acct, user, store, calendar_id=dynamic_record['calendar_id'])
    assert dynamic_after['metadata']['calendar_id'] == extra_calendar['id']
    assert target_timeline['title'] in {item.get('title') for item in dynamic_after['sources']}
    assert 'Large dynamic after edit' not in static_record['ics_text']

    rows = transform(store)
    calendar_ids = {row['id'] for row in rows['calendars']}
    assert extra_calendar['id'] in calendar_ids, 'custom calendar must be preserved for Supabase writes'
    extra_timeline_rows = [row for row in rows['timelines_initial'] if row.get('calendar_id') == extra_calendar['id']]
    extra_subscription_rows = [row for row in rows['subscriptions_initial'] if row.get('calendar_id') == extra_calendar['id']]
    assert extra_timeline_rows, 'custom-calendar timelines must map to Supabase rows'
    assert extra_subscription_rows, 'custom-calendar subscriptions must map to Supabase rows'

    elapsed = time.perf_counter() - started
    total_events = sum(len(item.get('events') or []) for raw_user in store['users'].values() for item in raw_user.get('timelines') or [])
    print({
        'ok': True,
        'users': len(store['users']),
        'timelines': len(rows['timelines_initial']),
        'subscriptions': len(rows['subscriptions_initial']),
        'events': total_events,
        'calendars': len(rows['calendars']),
        'elapsed_seconds': round(elapsed, 3),
    })
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
