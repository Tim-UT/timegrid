#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from timegrid_storage import SupabaseStorage
from scripts.generate_sample_store import build_store


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
    fragment_storage.save_user_fragment(store, 'sample1', subscriptions=True, timelines=True, exports=True)
    upserts = fragment_storage.writer.upserts
    user_rows = next(rows for table, rows, _conflict in upserts if table == 'timegrid_users')
    subscription_rows = next(rows for table, rows, _conflict in upserts if table == 'timegrid_subscriptions')
    timeline_rows = next(rows for table, rows, _conflict in upserts if table == 'timegrid_timelines')
    export_rows = next(rows for table, rows, _conflict in upserts if table == 'timegrid_exports')
    assert {row['acct'] for row in user_rows} == {'sample1'}
    assert {row['owner_acct'] for row in subscription_rows} == {'sample1'}
    assert {row['owner_acct'] for row in timeline_rows} == {'sample1'}
    assert {row['acct'] for row in export_rows} == {'sample1'}
    assert all(not str(value).startswith(('tl_u2_', 'sub_u2_')) for _table, _column, value, _payload in fragment_storage.writer.patches)

    print({'ok': True, 'deleted': storage.deleted, 'fragment_upserts': len(upserts), 'fragment_patches': len(fragment_storage.writer.patches)})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
