#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from timegrid_storage import SupabaseStorage


class FakeStorage(SupabaseStorage):
    def __init__(self) -> None:
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
    print({'ok': True, 'deleted': storage.deleted})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
