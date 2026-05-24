#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from timegrid_storage import SupabaseStorage
from scripts.generate_sample_store import build_store
from scripts.import_json_to_supabase import transform


class FakeWriter:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, list[dict[str, Any]], str]] = []
        self.patches: list[tuple[str, str, Any, dict[str, Any]]] = []

    def upsert(self, table: str, rows: list[dict[str, Any]], *, on_conflict: str) -> None:
        self.upserts.append((table, list(rows), on_conflict))

    def patch(self, table: str, column: str, value: Any, payload: dict[str, Any]) -> None:
        self.patches.append((table, column, value, dict(payload)))


class FakeStorage(SupabaseStorage):
    def __init__(self) -> None:
        self.writer = FakeWriter()
        self.existing: dict[str, set[tuple[Any, ...]]] = {
            'timegrid_calendars': {
                ('cal_keep',),
                ('cal_remove',),
            },
            'timegrid_published_bundle_items': {
                ('bundle_keep', 'sub_keep'),
                ('bundle_remove', 'sub_remove'),
            },
        }
        self.deleted: list[tuple[str, tuple[Any, ...]]] = []

    def table_keys(self, table: str, pk_columns: tuple[str, ...]) -> set[tuple[Any, ...]]:
        return set(self.existing.get(table, set()))

    def delete_key(self, table: str, pk_columns: tuple[str, ...], key: tuple[Any, ...]) -> None:
        self.deleted.append((table, key))


class FakeReadableStorage(SupabaseStorage):
    def __init__(self, table_rows: dict[str, list[dict[str, Any]]]) -> None:
        self.table_rows = table_rows

    def get_rows(self, table: str, *, order: str = '') -> list[dict[str, Any]]:
        rows = list(self.table_rows.get(table, []))
        if order:
            column = order.split('.', 1)[0]
            rows.sort(key=lambda row: row.get(column) or 0)
        return rows


def rows_as_imported(rows: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    table_rows = {
        'timegrid_users': list(rows['users']),
        'timegrid_auth_identities': list(rows['identities']),
        'timegrid_calendars': list(rows['calendars']),
        'timegrid_timelines': [dict(row) for row in rows['timelines_initial']],
        'timegrid_subscriptions': [dict(row) for row in rows['subscriptions_initial']],
        'timegrid_published_bundles': list(rows['published']),
        'timegrid_published_bundle_items': list(rows['published_items']),
        'timegrid_exports': list(rows['exports']),
        'timegrid_notifications': list(rows['notifications']),
        'timegrid_signup_intents': list(rows['signup_intents']),
    }
    timelines_by_id = {row['id']: row for row in table_rows['timegrid_timelines']}
    for link in rows['timeline_subscription_links']:
        if link['id'] in timelines_by_id:
            timelines_by_id[link['id']]['subscription_id'] = link['subscription_id']
    subscriptions_by_id = {row['id']: row for row in table_rows['timegrid_subscriptions']}
    for link in rows['subscription_fk_links']:
        subscription = subscriptions_by_id.get(link['id'])
        if subscription:
            for key in ('owned_timeline_id', 'grouped_in', 'bundle_overlay_for', 'shell_source_id'):
                if link.get(key):
                    subscription[key] = link[key]
    return table_rows


def main() -> int:
    storage = FakeStorage()
    storage.delete_stale_rows('timegrid_calendars', [{'id': 'cal_keep'}], ('id',))
    storage.delete_stale_rows(
        'timegrid_published_bundle_items',
        [{'bundle_id': 'bundle_keep', 'subscription_id': 'sub_keep'}],
        ('bundle_id', 'subscription_id'),
    )
    assert ('timegrid_calendars', ('cal_remove',)) in storage.deleted
    assert ('timegrid_calendars', ('cal_keep',)) not in storage.deleted
    assert ('timegrid_published_bundle_items', ('bundle_remove', 'sub_remove')) in storage.deleted
    assert ('timegrid_published_bundle_items', ('bundle_keep', 'sub_keep')) not in storage.deleted

    fragment_storage = FakeStorage()
    store = build_store(users=2, timelines_per_user=4, events_per_timeline=2)
    store['users']['sample1']['notifications'] = [{
        'id': 'note_sample1',
        'kind': 'workspace_notice',
        'title': 'Sample notice',
        'body': '',
        'href': '',
        'actor_acct': 'sample1',
        'created_at': store['users']['sample1']['updated_at'],
    }]
    store['users']['sample2']['notifications'] = [{
        'id': 'note_sample2',
        'kind': 'workspace_notice',
        'title': 'Other notice',
        'body': '',
        'href': '',
        'actor_acct': 'sample2',
        'created_at': store['users']['sample2']['updated_at'],
    }]
    fragment_storage.save_user_fragment(
        store,
        'sample1',
        identities=True,
        calendars=True,
        subscriptions=True,
        timelines=True,
        exports=True,
        notifications=True,
    )
    upserts = fragment_storage.writer.upserts
    user_rows = next(rows for table, rows, _conflict in upserts if table == 'timegrid_users')
    identity_rows = next(rows for table, rows, _conflict in upserts if table == 'timegrid_auth_identities')
    calendar_rows = next(rows for table, rows, _conflict in upserts if table == 'timegrid_calendars')
    subscription_rows = next(rows for table, rows, _conflict in upserts if table == 'timegrid_subscriptions')
    timeline_rows = next(rows for table, rows, _conflict in upserts if table == 'timegrid_timelines')
    export_rows = next(rows for table, rows, _conflict in upserts if table == 'timegrid_exports')
    notification_rows = next(rows for table, rows, _conflict in upserts if table == 'timegrid_notifications')
    assert {row['acct'] for row in user_rows} == {'sample1'}
    assert {row['acct'] for row in identity_rows} == {'sample1'}
    assert {row['owner_acct'] for row in calendar_rows} == {'sample1'}
    assert {row['owner_acct'] for row in subscription_rows} == {'sample1'}
    assert {row['owner_acct'] for row in timeline_rows} == {'sample1'}
    assert {row['acct'] for row in export_rows} == {'sample1'}
    assert {row['acct'] for row in notification_rows} == {'sample1'}
    assert all(payload for _table, _column, _value, payload in fragment_storage.writer.patches), 'Supabase patches must never send an empty JSON body'
    assert all(not str(value).startswith(('tl_u2_', 'sub_u2_')) for _table, _column, value, _payload in fragment_storage.writer.patches)

    roundtrip_store = build_store(users=1, timelines_per_user=6, events_per_timeline=2)
    roundtrip_user = roundtrip_store['users']['sample1']
    roundtrip_user.setdefault('calendars', [])
    extra_calendar = {
        'id': 'cal_sample1_lab',
        'workspace': 'personal',
        'title': 'Lab',
        'color': '#577590',
        'position': 2,
        'is_default': False,
        'archived': False,
        'created_at': roundtrip_user['updated_at'],
        'updated_at': roundtrip_user['updated_at'],
    }
    roundtrip_user['calendars'].append(extra_calendar)
    target_timeline = roundtrip_user['timelines'][0]
    target_subscription = next(item for item in roundtrip_user['subscriptions'] if item.get('id') == target_timeline.get('subscription_id'))
    target_timeline['calendar_id'] = extra_calendar['id']
    target_subscription['calendar_id'] = extra_calendar['id']
    roundtrip_store['exports']['lab_dynamic'] = {
        'acct': 'sample1',
        'calendar_id': extra_calendar['id'],
        'kind': 'dynamic',
        'snapshot': {'metadata': {'title': 'Lab export'}},
        'ics_text': '',
        'created_at': roundtrip_user['updated_at'],
        'updated_at': roundtrip_user['updated_at'],
    }
    rows = transform(roundtrip_store)
    reloaded = FakeReadableStorage(rows_as_imported(rows)).load_store()
    reloaded_user = reloaded['users']['sample1']
    assert any(item['id'] == extra_calendar['id'] and item['title'] == 'Lab' for item in reloaded_user['calendars'])
    reloaded_timeline = next(item for item in reloaded_user['timelines'] if item['id'] == target_timeline['id'])
    reloaded_subscription = next(item for item in reloaded_user['subscriptions'] if item['id'] == target_subscription['id'])
    assert reloaded_timeline['calendar_id'] == extra_calendar['id']
    assert reloaded_timeline['subscription_id'] == target_subscription['id']
    assert reloaded_subscription['calendar_id'] == extra_calendar['id']
    assert reloaded_subscription['owned_timeline_id'] == target_timeline['id']
    assert reloaded['exports']['lab_dynamic']['calendar_id'] == extra_calendar['id']

    print({'ok': True, 'deleted': storage.deleted, 'fragment_upserts': len(upserts), 'fragment_patches': len(fragment_storage.writer.patches), 'roundtrip_calendars': len(reloaded_user['calendars'])})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
