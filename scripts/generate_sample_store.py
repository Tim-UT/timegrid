#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def event(user_index: int, timeline_index: int, event_index: int) -> dict:
    start = datetime(2026, 6, 1, 14, tzinfo=timezone.utc) + timedelta(days=event_index, hours=timeline_index)
    return {
        'id': f'evt_u{user_index}_t{timeline_index}_{event_index}',
        'title': f'Sample event {event_index + 1}',
        'start': iso(start),
        'end': iso(start + timedelta(hours=1)),
        'description': f'Synthetic event for user {user_index + 1}, timeline {timeline_index + 1}.',
        'location': 'TimeGrid Lab',
        'url': 'https://calendar.time-grid.org',
        'recurrence': None if event_index % 3 else {'freq': 'weekly', 'count': 4},
        'exdates': [],
        'overrides': [],
    }


def build_store(users: int, timelines_per_user: int, events_per_timeline: int) -> dict:
    now = iso(datetime.now(timezone.utc))
    store = {'users': {}, 'published': {}, 'signup_intents': [], 'exports': {}}
    for user_index in range(users):
        acct = f'sample{user_index + 1}'
        user = {
            'acct': acct,
            'user_id': f'user_{user_index + 1}',
            'display_name': f'Sample User {user_index + 1}',
            'avatar': '',
            'bio': 'Synthetic TimeGrid account for migration testing.',
            'profile_visibility': 'public',
            'blocked_accounts': [],
            'linked_identities': [
                {
                    'id': f'ident_sample_{user_index + 1}',
                    'provider': 'email',
                    'provider_subject': f'sample{user_index + 1}@example.com',
                    'email': f'sample{user_index + 1}@example.com',
                    'email_verified': True,
                    'created_at': now,
                }
            ],
            'mastodon_profile': {'acct': acct, 'provisioned': False},
            'onboarding': {'calendar_ready': True, 'mastodon_ready': False},
            'notifications': [],
            'subscriptions': [],
            'timelines': [],
            'published': [],
            'updated_at': now,
        }
        for timeline_index in range(timelines_per_user):
            timeline_id = f'tl_u{user_index + 1}_{timeline_index + 1}'
            sub_id = f'sub_u{user_index + 1}_{timeline_index + 1}'
            workspace = 'creator' if timeline_index % 2 else 'personal'
            timeline = {
                'id': timeline_id,
                'title': f'{workspace.title()} calendar {timeline_index + 1}',
                'description': 'Synthetic editable timeline.',
                'events': [event(user_index, timeline_index, i) for i in range(events_per_timeline)],
                'created_at': now,
                'updated_at': now,
                'color': '#2f7d80',
                'subscription_id': sub_id,
            }
            subscription = {
                'id': sub_id,
                'title': timeline['title'],
                'url': f'https://calendar.time-grid.org/ics/{acct}/{timeline_id}.ics',
                'visible': True,
                'trashed': False,
                'created_at': now,
                'owned_timeline_id': timeline_id,
                'color': timeline['color'],
                'author_name': user['display_name'],
                'author_acct': acct,
                'workspace': workspace,
            }
            user['timelines'].append(timeline)
            user['subscriptions'].append(subscription)
        publish_ids = [item['id'] for item in user['subscriptions'][:2]]
        if publish_ids:
            slug = f'{acct}-sample-bundle'
            bundle = {
                'id': f'pub_{acct}',
                'slug': slug,
                'title': f'{user["display_name"]} sample bundle',
                'owner_acct': acct,
                'subscription_ids': publish_ids,
                'subscription_count': len(publish_ids),
                'created_at': now,
                'share_url': f'https://calendar.time-grid.org/p/{slug}',
                'visibility': 'public',
                'invited': [],
                'hashtags': ['sample', 'migration'],
                'allow_hard_copy': False,
                'archived': False,
                'listed': True,
                'owner_detached': False,
            }
            user['published'].append(bundle)
            store['published'][slug] = bundle
        store['exports'][f'export_{acct}'] = {
            'acct': acct,
            'kind': 'dynamic',
            'snapshot': {},
            'created_at': now,
            'updated_at': now,
        }
        store['users'][acct] = user
    return store


def main() -> int:
    parser = argparse.ArgumentParser(description='Generate a synthetic TimeGrid JSON store for migration tests.')
    parser.add_argument('output', type=Path)
    parser.add_argument('--users', type=int, default=8)
    parser.add_argument('--timelines', type=int, default=6)
    parser.add_argument('--events', type=int, default=20)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build_store(args.users, args.timelines, args.events), indent=2), encoding='utf-8')
    print(args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
