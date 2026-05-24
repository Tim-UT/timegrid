#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DEFAULT_COLOR = '#2f7d80'


def slugify(value: str) -> str:
    cleaned = ''.join(ch.lower() if ch.isalnum() else '-' for ch in value).strip('-')
    cleaned = '-'.join(part for part in cleaned.split('-') if part)
    return cleaned[:48] or 'calendar'


def parse_time(value: Any) -> str | None:
    if not value:
        return None
    raw = str(value)
    if raw.endswith('Z'):
        raw = raw[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(raw).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def table_url(base_url: str, table: str, *, query: str = '') -> str:
    url = f'{base_url.rstrip("/")}/rest/v1/{table}'
    return f'{url}?{query}' if query else url


class SupabaseRest:
    TRANSIENT_ERROR_CODES = ('40P01', '40001', '55P03')

    def __init__(self, url: str, service_key: str, *, dry_run: bool = False) -> None:
        self.url = url.rstrip('/')
        self.dry_run = dry_run
        self.session = requests.Session()
        self.session.headers.update({
            'apikey': service_key,
            'Authorization': f'Bearer {service_key}',
            'Content-Type': 'application/json',
        })

    def _request_with_retry(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        last_resp: requests.Response | None = None
        for attempt in range(4):
            resp = self.session.request(method, url, **kwargs)
            if resp.status_code < 500 or not any(code in resp.text for code in self.TRANSIENT_ERROR_CODES):
                return resp
            last_resp = resp
            time.sleep(0.15 * (2 ** attempt))
        assert last_resp is not None
        return last_resp

    def upsert(self, table: str, rows: list[dict[str, Any]], *, on_conflict: str) -> None:
        rows = normalize_rows(rows)
        if not rows:
            return
        if self.dry_run:
            print(f'dry-run upsert {table}: {len(rows)} rows')
            return
        headers = {'Prefer': 'resolution=merge-duplicates'}
        query = f'on_conflict={on_conflict}'
        resp = self._request_with_retry(
            'POST',
            table_url(self.url, table, query=query),
            headers=headers,
            data=json.dumps(rows),
        )
        if resp.status_code >= 400:
            raise RuntimeError(f'{table} upsert failed: {resp.status_code} {resp.text[:1000]}')

    def patch(self, table: str, match_column: str, match_value: str, payload: dict[str, Any]) -> None:
        if not payload:
            return
        if self.dry_run:
            print(f'dry-run patch {table}: {match_column}={match_value}')
            return
        query = f'{match_column}=eq.{requests.utils.quote(match_value, safe="")}'
        resp = self._request_with_retry(
            'PATCH',
            table_url(self.url, table, query=query),
            data=json.dumps(payload),
        )
        if resp.status_code >= 400:
            raise RuntimeError(f'{table} patch failed: {resp.status_code} {resp.text[:1000]}')


def default_calendar_id(acct: str, workspace: str) -> str:
    return f'cal_{slugify(acct)}_{workspace}'


def subscription_workspace(item: dict[str, Any]) -> str:
    workspace = str(item.get('workspace') or '').strip().lower()
    if workspace in {'personal', 'creator', 'archive'}:
        return workspace
    if item.get('creator_archived'):
        return 'archive'
    return 'personal'


def calendar_for_workspace(acct: str, workspace: str) -> str:
    return default_calendar_id(acct, 'creator' if workspace == 'creator' else 'personal')


def load_store(path: Path) -> dict[str, Any]:
    with path.open('r', encoding='utf-8') as fh:
        data = json.load(fh)
    data.setdefault('users', {})
    data.setdefault('published', {})
    data.setdefault('signup_intents', [])
    data.setdefault('exports', {})
    return data


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [row for row in rows if row]
    if not rows:
        return []
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return [{key: row.get(key) for key in columns} for row in rows]


def transform(store: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    users: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    calendars: list[dict[str, Any]] = []
    timelines_initial: list[dict[str, Any]] = []
    timeline_subscription_links: list[tuple[str, str]] = []
    subscriptions_initial: list[dict[str, Any]] = []
    subscription_fk_links: list[tuple[str, dict[str, Any]]] = []
    published: list[dict[str, Any]] = []
    published_items: list[dict[str, Any]] = []
    exports: list[dict[str, Any]] = []
    notifications: list[dict[str, Any]] = []
    signup_intents: list[dict[str, Any]] = []
    calendar_workspace_by_acct: dict[str, dict[str, str]] = {}

    for acct_key, raw_user in store.get('users', {}).items():
        acct = str(raw_user.get('acct') or acct_key).strip().lower()
        if not acct:
            continue
        created_at = parse_time(raw_user.get('created_at')) or parse_time(raw_user.get('updated_at'))
        users.append({
            'acct': acct,
            'user_id': str(raw_user.get('user_id') or '') or None,
            'mastodon_account_id': str(raw_user.get('account_id') or '') or None,
            'display_name': str(raw_user.get('display_name') or acct),
            'avatar_url': str(raw_user.get('avatar') or ''),
            'bio': str(raw_user.get('bio') or ''),
            'profile_visibility': str(raw_user.get('profile_visibility') or 'public'),
            'mastodon_profile': raw_user.get('mastodon_profile') or {},
            'onboarding': raw_user.get('onboarding') or {'calendar_ready': True, 'mastodon_ready': False},
            'blocked_accounts': list(raw_user.get('blocked_accounts') or []),
            'created_at': created_at,
            'updated_at': parse_time(raw_user.get('updated_at')) or created_at,
        })
        user_calendars: list[dict[str, Any]] = []
        seen_calendar_ids: set[str] = set()
        for index, item in enumerate(raw_user.get('calendars') or []):
            calendar_id = str(item.get('id') or '').strip()
            if not calendar_id or calendar_id in seen_calendar_ids:
                continue
            workspace = 'creator' if str(item.get('workspace') or '').strip().lower() == 'creator' else 'personal'
            seen_calendar_ids.add(calendar_id)
            user_calendars.append({
                'id': calendar_id,
                'owner_acct': acct,
                'workspace': workspace,
                'title': str(item.get('title') or ('Creator' if workspace == 'creator' else 'Personal')),
                'color': str(item.get('color') or DEFAULT_COLOR),
                'position': int(item.get('position') or index),
                'is_default': bool(item.get('is_default')),
                'archived': bool(item.get('archived')),
                'settings': item.get('settings') or {},
                'created_at': parse_time(item.get('created_at')) or created_at,
                'updated_at': parse_time(item.get('updated_at')) or parse_time(raw_user.get('updated_at')) or created_at,
            })
        for workspace, title in (('personal', 'Personal'), ('creator', 'Creator')):
            default_id = default_calendar_id(acct, workspace)
            has_workspace_calendar = any(item['workspace'] == workspace for item in user_calendars)
            has_workspace_default = any(item['workspace'] == workspace and item['is_default'] for item in user_calendars)
            if default_id not in seen_calendar_ids and (not has_workspace_calendar or not has_workspace_default):
                user_calendars.append({
                    'id': default_id,
                    'owner_acct': acct,
                    'workspace': workspace,
                    'title': title,
                    'color': DEFAULT_COLOR,
                    'position': 0 if workspace == 'personal' else 1,
                    'is_default': True,
                    'archived': False,
                    'settings': {},
                    'created_at': created_at,
                    'updated_at': parse_time(raw_user.get('updated_at')) or created_at,
                })
                seen_calendar_ids.add(default_id)
        calendar_workspace_by_id = {item['id']: item['workspace'] for item in user_calendars}
        calendar_workspace_by_acct[acct] = calendar_workspace_by_id

        def row_calendar_id(workspace: str, requested: Any) -> str:
            requested_id = str(requested or '').strip()
            if requested_id and calendar_workspace_by_id.get(requested_id) == workspace:
                return requested_id
            return default_calendar_id(acct, workspace)

        calendars.extend(user_calendars)
        for identity in raw_user.get('linked_identities') or []:
            provider = str(identity.get('provider') or '').strip().lower()
            subject = str(identity.get('provider_subject') or '').strip()
            if not provider or not subject:
                continue
            identities.append({
                'id': str(identity.get('id') or f'ident_{slugify(provider)}_{slugify(subject)}')[:80],
                'acct': acct,
                'provider': provider,
                'provider_subject': subject,
                'email': str(identity.get('email') or '').strip().lower() or None,
                'email_verified': bool(identity.get('email_verified')),
                'created_at': parse_time(identity.get('created_at')),
                'updated_at': parse_time(identity.get('updated_at')) or parse_time(identity.get('created_at')),
            })
        sub_by_id = {str(item.get('id')): item for item in raw_user.get('subscriptions') or [] if item.get('id')}
        timeline_ids = {str(item.get('id')) for item in raw_user.get('timelines') or [] if item.get('id')}
        for index, timeline in enumerate(raw_user.get('timelines') or []):
            timeline_id = str(timeline.get('id') or '').strip()
            if not timeline_id:
                continue
            linked_sub = sub_by_id.get(str(timeline.get('subscription_id') or ''))
            workspace = subscription_workspace(linked_sub or {})
            timelines_initial.append({
                'id': timeline_id,
                'owner_acct': acct,
                'calendar_id': row_calendar_id(workspace, timeline.get('calendar_id')),
                'subscription_id': None,
                'kind': str(timeline.get('kind') or ''),
                'title': str(timeline.get('title') or 'Untitled timeline'),
                'description': str(timeline.get('description') or ''),
                'color': str(timeline.get('color') or DEFAULT_COLOR),
                'events': list(timeline.get('events') or []),
                'position': index,
                'metadata': {k: v for k, v in timeline.items() if k not in {'id', 'subscription_id', 'kind', 'title', 'description', 'color', 'events', 'created_at', 'updated_at'}},
                'created_at': parse_time(timeline.get('created_at')),
                'updated_at': parse_time(timeline.get('updated_at')) or parse_time(timeline.get('created_at')),
            })
            if timeline.get('subscription_id'):
                timeline_subscription_links.append((timeline_id, str(timeline.get('subscription_id'))))
        for index, item in enumerate(raw_user.get('subscriptions') or []):
            sub_id = str(item.get('id') or '').strip()
            if not sub_id:
                continue
            workspace = subscription_workspace(item)
            fk_payload = {
                'owned_timeline_id': str(item.get('owned_timeline_id') or '') if str(item.get('owned_timeline_id') or '') in timeline_ids else None,
                'grouped_in': str(item.get('grouped_in') or '') if str(item.get('grouped_in') or '') in sub_by_id else None,
                'bundle_overlay_for': str(item.get('bundle_overlay_for') or '') if str(item.get('bundle_overlay_for') or '') in sub_by_id else None,
                'shell_source_id': str(item.get('shell_source_id') or '') if str(item.get('shell_source_id') or '') in sub_by_id else None,
            }
            subscriptions_initial.append({
                'id': sub_id,
                'owner_acct': acct,
                'calendar_id': row_calendar_id(workspace, item.get('calendar_id')),
                'title': str(item.get('title') or item.get('url') or 'Subscription'),
                'url': str(item.get('url') or ''),
                'visible': bool(item.get('visible', True)),
                'trashed': bool(item.get('trashed', False)),
                'kind': str(item.get('kind') or ''),
                'workspace': workspace,
                'owned_timeline_id': None,
                'grouped_in': None,
                'bundle_overlay_for': None,
                'shell_source_id': None,
                'components': list(item.get('components') or []),
                'color': str(item.get('color') or DEFAULT_COLOR),
                'author_name': str(item.get('author_name') or raw_user.get('display_name') or acct),
                'author_acct': str(item.get('author_acct') or acct).strip().lower(),
                'official': bool(item.get('official')),
                'detached': bool(item.get('detached')),
                'creator_archived': bool(item.get('creator_archived')),
                'source_code': str(item.get('source_code') or ''),
                'source_format': str(item.get('source_format') or ''),
                'hashtags': list(item.get('hashtags') or []),
                'description': str(item.get('description') or ''),
                'position': index,
                'metadata': {k: v for k, v in item.items() if k not in {
                    'id', 'title', 'url', 'visible', 'trashed', 'kind', 'workspace',
                    'owned_timeline_id', 'grouped_in', 'bundle_overlay_for', 'shell_source_id',
                    'components', 'color', 'author_name', 'author_acct', 'official', 'detached',
                    'creator_archived', 'source_code', 'source_format', 'hashtags', 'description',
                    'created_at', 'updated_at'
                }},
                'created_at': parse_time(item.get('created_at')),
                'updated_at': parse_time(item.get('updated_at')) or parse_time(item.get('created_at')),
            })
            subscription_fk_links.append((sub_id, fk_payload))
        for note in raw_user.get('notifications') or []:
            note_id = str(note.get('id') or '').strip()
            if not note_id:
                continue
            notifications.append({
                'id': note_id,
                'acct': acct,
                'kind': str(note.get('kind') or ''),
                'title': str(note.get('title') or ''),
                'body': str(note.get('body') or ''),
                'href': str(note.get('href') or ''),
                'actor_acct': str(note.get('actor_acct') or ''),
                'read_at': parse_time(note.get('read_at')),
                'created_at': parse_time(note.get('created_at')),
            })

    known_subscription_ids = {row['id'] for row in subscriptions_initial}

    def known_calendar_id(owner: str, workspace: str, requested: Any) -> str:
        owner = str(owner or '').strip().lower()
        workspace = 'creator' if workspace == 'creator' else 'personal'
        requested_id = str(requested or '').strip()
        if requested_id and calendar_workspace_by_acct.get(owner, {}).get(requested_id) == workspace:
            return requested_id
        return calendar_for_workspace(owner, workspace)

    for bundle in (store.get('published') or {}).values():
        bundle_id = str(bundle.get('id') or '').strip()
        owner = str(bundle.get('owner_acct') or '').strip().lower()
        if not bundle_id or not owner:
            continue
        published.append({
            'id': bundle_id,
            'slug': str(bundle.get('slug') or slugify(bundle.get('title') or bundle_id)),
            'owner_acct': owner,
            'calendar_id': known_calendar_id(owner, 'creator', bundle.get('calendar_id')),
            'title': str(bundle.get('title') or 'Published calendar'),
            'share_url': str(bundle.get('share_url') or ''),
            'visibility': str(bundle.get('visibility') or 'public'),
            'invited': list(bundle.get('invited') or []),
            'hashtags': list(bundle.get('hashtags') or []),
            'listed': bool(bundle.get('listed', True)),
            'archived': bool(bundle.get('archived', False)),
            'owner_detached': bool(bundle.get('owner_detached', False)),
            'allow_hard_copy': bool(bundle.get('allow_hard_copy', False)),
            'metadata': {k: v for k, v in bundle.items() if k not in {'id', 'slug', 'owner_acct', 'title', 'share_url', 'visibility', 'invited', 'hashtags', 'listed', 'archived', 'owner_detached', 'allow_hard_copy', 'subscription_ids', 'created_at', 'updated_at'}},
            'created_at': parse_time(bundle.get('created_at')),
            'updated_at': parse_time(bundle.get('updated_at')) or parse_time(bundle.get('created_at')),
        })
        for position, sub_id in enumerate(bundle.get('subscription_ids') or []):
            if str(sub_id) in known_subscription_ids:
                published_items.append({'bundle_id': bundle_id, 'subscription_id': str(sub_id), 'position': position})

    for token, record in (store.get('exports') or {}).items():
        acct = str(record.get('acct') or '').strip().lower()
        if not acct:
            continue
        exports.append({
            'token': str(token),
            'acct': acct,
            'calendar_id': known_calendar_id(acct, 'personal', record.get('calendar_id')),
            'kind': str(record.get('kind') or 'dynamic'),
            'title': str((record.get('snapshot') or {}).get('metadata', {}).get('title') or ''),
            'snapshot': record.get('snapshot') or {},
            'ics_text': str(record.get('ics_text') or ''),
            'metadata': {k: v for k, v in record.items() if k not in {'acct', 'kind', 'snapshot', 'ics_text', 'created_at', 'updated_at'}},
            'created_at': parse_time(record.get('created_at')),
            'updated_at': parse_time(record.get('updated_at')) or parse_time(record.get('created_at')),
        })

    for intent in store.get('signup_intents') or []:
        intent_id = str(intent.get('id') or '').strip()
        if not intent_id:
            continue
        signup_intents.append({
            'id': intent_id,
            'provider': str(intent.get('provider') or ''),
            'email': str(intent.get('email') or '').strip().lower() or None,
            'display_name': str(intent.get('display_name') or ''),
            'note': str(intent.get('note') or ''),
            'next_path': str(intent.get('next') or intent.get('next_path') or '/'),
            'status': str(intent.get('status') or 'pending'),
            'create_linked_mastodon': bool(intent.get('create_linked_mastodon', True)),
            'created_at': parse_time(intent.get('created_at')),
            'updated_at': parse_time(intent.get('updated_at')) or parse_time(intent.get('created_at')),
        })

    return {
        'users': users,
        'identities': identities,
        'calendars': calendars,
        'timelines_initial': timelines_initial,
        'timeline_subscription_links': [{'id': tl_id, 'subscription_id': sub_id} for tl_id, sub_id in timeline_subscription_links],
        'subscriptions_initial': subscriptions_initial,
        'subscription_fk_links': [{'id': sub_id, **payload} for sub_id, payload in subscription_fk_links],
        'published': published,
        'published_items': published_items,
        'exports': exports,
        'notifications': notifications,
        'signup_intents': signup_intents,
    }


def import_rows(client: SupabaseRest, rows: dict[str, list[dict[str, Any]]]) -> None:
    client.upsert('timegrid_users', rows['users'], on_conflict='acct')
    client.upsert('timegrid_auth_identities', rows['identities'], on_conflict='id')
    client.upsert('timegrid_calendars', rows['calendars'], on_conflict='id')
    client.upsert('timegrid_timelines', rows['timelines_initial'], on_conflict='id')
    client.upsert('timegrid_subscriptions', rows['subscriptions_initial'], on_conflict='id')
    for row in rows['subscription_fk_links']:
        sub_id = row.pop('id')
        payload = {k: v for k, v in row.items() if v}
        if payload:
            client.patch('timegrid_subscriptions', 'id', sub_id, payload)
    for row in rows['timeline_subscription_links']:
        client.patch('timegrid_timelines', 'id', row['id'], {'subscription_id': row['subscription_id']})
    client.upsert('timegrid_published_bundles', rows['published'], on_conflict='id')
    client.upsert('timegrid_published_bundle_items', rows['published_items'], on_conflict='bundle_id,subscription_id')
    client.upsert('timegrid_exports', rows['exports'], on_conflict='token')
    client.upsert('timegrid_notifications', rows['notifications'], on_conflict='id')
    client.upsert('timegrid_signup_intents', rows['signup_intents'], on_conflict='id')


def main() -> int:
    parser = argparse.ArgumentParser(description='Import TimeGrid JSON storage into Supabase.')
    parser.add_argument('store_json', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    supabase_url = os.environ.get('SUPABASE_URL', '').strip()
    service_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '').strip()
    if not args.dry_run and (not supabase_url or not service_key):
        print('SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required unless --dry-run is used.', file=sys.stderr)
        return 2

    rows = transform(load_store(args.store_json))
    for key, value in rows.items():
        print(f'{key}: {len(value)}')
    client = SupabaseRest(supabase_url or 'https://example.supabase.co', service_key or 'dry-run', dry_run=args.dry_run)
    import_rows(client, rows)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
