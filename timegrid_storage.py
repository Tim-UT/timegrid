from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

try:
    from scripts.import_json_to_supabase import SupabaseRest, import_rows, normalize_rows, transform
except ModuleNotFoundError:
    from import_json_to_supabase import SupabaseRest, import_rows, normalize_rows, transform


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value)
    if raw.endswith('Z'):
        raw = raw[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except ValueError:
        return None


def iso_to_epoch(value: Any) -> float:
    parsed = parse_datetime(value)
    return parsed.timestamp() if parsed else time.time()


def epoch_to_iso(value: Any) -> str:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        ts = time.time()
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def table_url(base_url: str, table: str, *, query: str = '') -> str:
    url = f'{base_url.rstrip("/")}/rest/v1/{table}'
    return f'{url}?{query}' if query else url


def quote_filter(value: Any) -> str:
    return requests.utils.quote(str(value), safe='')


class SupabaseStorage:
    def __init__(self, url: str | None = None, service_key: str | None = None) -> None:
        self.url = (url or os.environ.get('SUPABASE_URL') or '').rstrip('/')
        self.service_key = service_key or os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or ''
        if not self.url or not self.service_key:
            raise RuntimeError('SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required when TIMEGRID_STORAGE=supabase')
        self.session = requests.Session()
        self.session.headers.update({
            'apikey': self.service_key,
            'Authorization': f'Bearer {self.service_key}',
            'Content-Type': 'application/json',
        })
        self.writer = SupabaseRest(self.url, self.service_key)

    def get_rows(self, table: str, *, order: str = '') -> list[dict[str, Any]]:
        query = 'select=*'
        if order:
            query += f'&order={order}'
        resp = self.session.get(table_url(self.url, table, query=query), timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f'{table} read failed: {resp.status_code} {resp.text[:1000]}')
        return resp.json()

    def delete_all(self, table: str, pk_column: str) -> None:
        resp = self.session.delete(
            table_url(self.url, table, query=f'{pk_column}=not.is.null'),
            headers={'Prefer': 'return=minimal'},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f'{table} delete failed: {resp.status_code} {resp.text[:1000]}')

    def table_keys(self, table: str, pk_columns: tuple[str, ...]) -> set[tuple[Any, ...]]:
        query = f'select={",".join(pk_columns)}'
        resp = self.session.get(table_url(self.url, table, query=query), timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f'{table} key read failed: {resp.status_code} {resp.text[:1000]}')
        return {tuple(row.get(column) for column in pk_columns) for row in resp.json()}

    def delete_key(self, table: str, pk_columns: tuple[str, ...], key: tuple[Any, ...]) -> None:
        query = '&'.join(f'{column}=eq.{quote_filter(value)}' for column, value in zip(pk_columns, key))
        resp = self.session.delete(
            table_url(self.url, table, query=query),
            headers={'Prefer': 'return=minimal'},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f'{table} stale delete failed: {resp.status_code} {resp.text[:1000]}')

    def delete_stale_rows(self, table: str, rows: list[dict[str, Any]], pk_columns: tuple[str, ...]) -> None:
        desired = {
            tuple(row.get(column) for column in pk_columns)
            for row in normalize_rows(rows)
            if all(row.get(column) is not None for column in pk_columns)
        }
        existing = self.table_keys(table, pk_columns)
        for key in sorted(existing - desired):
            self.delete_key(table, pk_columns, key)

    def reconcile_stale_rows(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        for table, row_key, pk_columns in (
            ('timegrid_published_bundle_items', 'published_items', ('bundle_id', 'subscription_id')),
            ('timegrid_notifications', 'notifications', ('id',)),
            ('timegrid_exports', 'exports', ('token',)),
            ('timegrid_published_bundles', 'published', ('id',)),
            ('timegrid_auth_identities', 'identities', ('id',)),
            ('timegrid_subscriptions', 'subscriptions_initial', ('id',)),
            ('timegrid_timelines', 'timelines_initial', ('id',)),
            ('timegrid_calendars', 'calendars', ('id',)),
            ('timegrid_signup_intents', 'signup_intents', ('id',)),
            ('timegrid_users', 'users', ('acct',)),
        ):
            self.delete_stale_rows(table, rows.get(row_key, []), pk_columns)

    def load_store(self) -> dict[str, Any]:
        users = self.get_rows('timegrid_users', order='acct.asc')
        identities = self.get_rows('timegrid_auth_identities', order='created_at.asc')
        calendars = self.get_rows('timegrid_calendars', order='position.asc')
        timelines = self.get_rows('timegrid_timelines', order='position.asc')
        subscriptions = self.get_rows('timegrid_subscriptions', order='position.asc')
        bundles = self.get_rows('timegrid_published_bundles', order='created_at.desc')
        bundle_items = self.get_rows('timegrid_published_bundle_items', order='position.asc')
        exports = self.get_rows('timegrid_exports', order='created_at.desc')
        notifications = self.get_rows('timegrid_notifications', order='created_at.desc')
        signup_intents = self.get_rows('timegrid_signup_intents', order='created_at.desc')

        store: dict[str, Any] = {'users': {}, 'published': {}, 'signup_intents': [], 'exports': {}}
        for row in users:
            acct = row['acct']
            store['users'][acct] = {
                'acct': acct,
                'user_id': row.get('user_id') or '',
                'account_id': row.get('mastodon_account_id') or '',
                'display_name': row.get('display_name') or acct,
                'avatar': row.get('avatar_url') or '',
                'bio': row.get('bio') or '',
                'profile_visibility': row.get('profile_visibility') or 'public',
                'blocked_accounts': row.get('blocked_accounts') or [],
                'linked_identities': [],
                'mastodon_profile': row.get('mastodon_profile') or {'acct': acct, 'provisioned': False},
                'onboarding': row.get('onboarding') or {'calendar_ready': True, 'mastodon_ready': False},
                'notifications': [],
                'subscriptions': [],
                'timelines': [],
                'published': [],
                'calendars': [],
                'created_at': row.get('created_at') or '',
                'updated_at': row.get('updated_at') or '',
            }

        for row in calendars:
            user = store['users'].get(row.get('owner_acct'))
            if not user:
                continue
            user['calendars'].append({
                'id': row.get('id') or '',
                'workspace': row.get('workspace') or 'personal',
                'title': row.get('title') or 'My calendar',
                'color': row.get('color') or '',
                'position': row.get('position') or 0,
                'is_default': bool(row.get('is_default')),
                'archived': bool(row.get('archived')),
                'settings': row.get('settings') or {},
                'created_at': row.get('created_at') or '',
                'updated_at': row.get('updated_at') or '',
            })

        for row in identities:
            user = store['users'].get(row.get('acct'))
            if not user:
                continue
            user['linked_identities'].append({
                'id': row.get('id') or '',
                'provider': row.get('provider') or '',
                'provider_subject': row.get('provider_subject') or '',
                'email': row.get('email') or '',
                'email_verified': bool(row.get('email_verified')),
                'created_at': row.get('created_at') or '',
            })

        for row in timelines:
            user = store['users'].get(row.get('owner_acct'))
            if not user:
                continue
            metadata = row.get('metadata') or {}
            item = dict(metadata)
            item.update({
                'id': row.get('id') or '',
                'calendar_id': row.get('calendar_id') or '',
                'subscription_id': row.get('subscription_id') or '',
                'title': row.get('title') or 'Untitled timeline',
                'description': row.get('description') or '',
                'events': row.get('events') or [],
                'created_at': row.get('created_at') or '',
                'updated_at': row.get('updated_at') or '',
                'color': row.get('color') or '',
            })
            if row.get('kind'):
                item['kind'] = row.get('kind')
            user['timelines'].append(item)

        for row in subscriptions:
            user = store['users'].get(row.get('owner_acct'))
            if not user:
                continue
            metadata = row.get('metadata') or {}
            item = dict(metadata)
            item.update({
                'id': row.get('id') or '',
                'calendar_id': row.get('calendar_id') or '',
                'title': row.get('title') or row.get('url') or 'Subscription',
                'url': row.get('url') or '',
                'position': row.get('position') or 0,
                'visible': bool(row.get('visible')),
                'trashed': bool(row.get('trashed')),
                'created_at': row.get('created_at') or '',
                'color': row.get('color') or '',
                'author_name': row.get('author_name') or '',
                'author_acct': row.get('author_acct') or '',
                'official': bool(row.get('official')),
                'detached': bool(row.get('detached')),
                'creator_archived': bool(row.get('creator_archived')),
                'source_code': row.get('source_code') or '',
                'source_format': row.get('source_format') or '',
                'hashtags': row.get('hashtags') or [],
                'description': row.get('description') or '',
                'workspace': row.get('workspace') or 'personal',
            })
            for key in ('kind', 'owned_timeline_id', 'grouped_in', 'bundle_overlay_for', 'shell_source_id'):
                if row.get(key):
                    item[key] = row.get(key)
            if row.get('components'):
                item['components'] = row.get('components') or []
            user['subscriptions'].append(item)

        bundle_subs: dict[str, list[str]] = {}
        for row in bundle_items:
            bundle_subs.setdefault(row.get('bundle_id') or '', []).append(row.get('subscription_id') or '')
        for row in bundles:
            metadata = row.get('metadata') or {}
            bundle = dict(metadata)
            bundle.update({
                'id': row.get('id') or '',
                'slug': row.get('slug') or '',
                'title': row.get('title') or 'Published calendar',
                'owner_acct': row.get('owner_acct') or '',
                'calendar_id': row.get('calendar_id') or '',
                'subscription_ids': [x for x in bundle_subs.get(row.get('id') or '', []) if x],
                'subscription_count': len([x for x in bundle_subs.get(row.get('id') or '', []) if x]),
                'created_at': row.get('created_at') or '',
                'share_url': row.get('share_url') or '',
                'visibility': row.get('visibility') or 'public',
                'invited': row.get('invited') or [],
                'hashtags': row.get('hashtags') or [],
                'allow_hard_copy': bool(row.get('allow_hard_copy')),
                'archived': bool(row.get('archived')),
                'listed': bool(row.get('listed')),
                'owner_detached': bool(row.get('owner_detached')),
            })
            store['published'][bundle['slug']] = bundle
            owner = store['users'].get(bundle['owner_acct'])
            if owner:
                owner['published'].append(dict(bundle))

        for row in exports:
            metadata = row.get('metadata') or {}
            record = dict(metadata)
            record.update({
                'acct': row.get('acct') or '',
                'calendar_id': row.get('calendar_id') or '',
                'kind': row.get('kind') or 'dynamic',
                'snapshot': row.get('snapshot') or {},
                'ics_text': row.get('ics_text') or '',
                'created_at': row.get('created_at') or '',
                'updated_at': row.get('updated_at') or '',
            })
            store['exports'][row['token']] = record

        for row in notifications:
            user = store['users'].get(row.get('acct'))
            if not user:
                continue
            user['notifications'].append({
                'id': row.get('id') or '',
                'kind': row.get('kind') or '',
                'title': row.get('title') or '',
                'body': row.get('body') or '',
                'href': row.get('href') or '',
                'actor_acct': row.get('actor_acct') or '',
                'read_at': row.get('read_at') or '',
                'created_at': row.get('created_at') or '',
            })

        for row in signup_intents:
            store['signup_intents'].append({
                'id': row.get('id') or '',
                'provider': row.get('provider') or '',
                'email': row.get('email') or '',
                'display_name': row.get('display_name') or '',
                'note': row.get('note') or '',
                'next': row.get('next_path') or '/',
                'status': row.get('status') or 'pending',
                'create_linked_mastodon': bool(row.get('create_linked_mastodon')),
                'created_at': row.get('created_at') or '',
            })

        return store

    def save_store(self, data: dict[str, Any]) -> None:
        rows = transform(data)
        import_rows(self.writer, rows)
        self.reconcile_stale_rows(rows)

    def save_user_fragment(
        self,
        data: dict[str, Any],
        acct: str,
        *,
        identities: bool = False,
        calendars: bool = False,
        subscriptions: bool = False,
        timelines: bool = False,
        exports: bool = False,
        notifications: bool = False,
    ) -> None:
        acct = str(acct or '').strip().lower()
        if not acct:
            self.save_store(data)
            return
        rows = transform(data)
        user_rows = [row for row in rows['users'] if row.get('acct') == acct]
        self.writer.upsert('timegrid_users', user_rows, on_conflict='acct')
        if identities:
            self.writer.upsert(
                'timegrid_auth_identities',
                [row for row in rows['identities'] if row.get('acct') == acct],
                on_conflict='id',
            )
        if calendars:
            self.writer.upsert(
                'timegrid_calendars',
                [row for row in rows['calendars'] if row.get('owner_acct') == acct],
                on_conflict='id',
            )
        if timelines:
            timeline_rows = [row for row in rows['timelines_initial'] if row.get('owner_acct') == acct]
            timeline_ids = {row.get('id') for row in timeline_rows}
            self.writer.upsert('timegrid_timelines', timeline_rows, on_conflict='id')
        if subscriptions:
            subscription_rows = [row for row in rows['subscriptions_initial'] if row.get('owner_acct') == acct]
            subscription_ids = {row.get('id') for row in subscription_rows}
            self.writer.upsert('timegrid_subscriptions', subscription_rows, on_conflict='id')
            for row in rows['subscription_fk_links']:
                sub_id = row.get('id')
                if sub_id in subscription_ids:
                    payload = {k: v for k, v in row.items() if k != 'id' and v}
                    if payload:
                        self.writer.patch('timegrid_subscriptions', 'id', sub_id, payload)
        if timelines:
            for row in rows['timeline_subscription_links']:
                if row.get('id') in timeline_ids:
                    self.writer.patch('timegrid_timelines', 'id', row['id'], {'subscription_id': row['subscription_id']})
        if exports:
            self.writer.upsert(
                'timegrid_exports',
                [row for row in rows['exports'] if row.get('acct') == acct],
                on_conflict='token',
            )
        if notifications:
            self.writer.upsert(
                'timegrid_notifications',
                [row for row in rows['notifications'] if row.get('acct') == acct],
                on_conflict='id',
            )

    def reconcile_store(self, data: dict[str, Any]) -> None:
        rows = transform(data)
        import_rows(self.writer, rows)
        self.reconcile_stale_rows(rows)

    def load_auth_state(self) -> dict[str, dict[str, Any]]:
        pending: dict[str, dict[str, Any]] = {}
        sessions: dict[str, dict[str, Any]] = {}
        for row in self.get_rows('timegrid_auth_pending', order='created_at.asc'):
            state = row.get('state') or ''
            if not state:
                continue
            pending[state] = {
                'provider': row.get('provider') or '',
                'verifier': row.get('verifier') or '',
                'nonce': row.get('nonce') or '',
                'next': row.get('next_path') or '/',
                'created_at': iso_to_epoch(row.get('created_at')),
            }
        for row in self.get_rows('timegrid_auth_sessions', order='created_at.asc'):
            session_id = row.get('session_id') or ''
            if not session_id:
                continue
            created_at = iso_to_epoch(row.get('created_at'))
            expires_at = iso_to_epoch(row.get('expires_at'))
            sessions[session_id] = {
                'acct': row.get('acct') or '',
                'account_id': row.get('account_id') or '',
                'display_name': row.get('display_name') or '',
                'avatar': row.get('avatar_url') or '',
                'role': row.get('role') or '',
                'created_at': created_at,
                'auth_provider': row.get('auth_provider') or '',
                'access_token': row.get('access_token') or '',
                'max_age': max(0, int(expires_at - created_at)),
            }
        return {'pending_auth': pending, 'sessions': sessions}

    def save_auth_state(self, pending_auth: dict[str, dict[str, Any]], sessions: dict[str, dict[str, Any]]) -> None:
        self.delete_all('timegrid_auth_pending', 'state')
        self.delete_all('timegrid_auth_sessions', 'session_id')
        now = time.time()
        pending_rows = []
        for state, auth in pending_auth.items():
            created_at = float(auth.get('created_at') or now)
            pending_rows.append({
                'state': state,
                'provider': auth.get('provider') or '',
                'verifier': auth.get('verifier') or '',
                'nonce': auth.get('nonce') or '',
                'next_path': auth.get('next') or '/',
                'created_at': epoch_to_iso(created_at),
                'expires_at': epoch_to_iso(created_at + 1800),
                'metadata': {k: v for k, v in auth.items() if k not in {'provider', 'verifier', 'nonce', 'next', 'created_at'}},
            })
        session_rows = []
        for session_id, session in sessions.items():
            created_at = float(session.get('created_at') or now)
            max_age = int(session.get('max_age') or 1209600)
            session_rows.append({
                'session_id': session_id,
                'acct': session.get('acct') or '',
                'account_id': session.get('account_id') or '',
                'display_name': session.get('display_name') or '',
                'avatar_url': session.get('avatar') or '',
                'role': session.get('role') or '',
                'auth_provider': session.get('auth_provider') or '',
                'access_token': session.get('access_token') or '',
                'created_at': epoch_to_iso(created_at),
                'expires_at': epoch_to_iso(created_at + max_age),
            })
        self.writer.upsert('timegrid_auth_pending', pending_rows, on_conflict='state')
        self.writer.upsert('timegrid_auth_sessions', session_rows, on_conflict='session_id')


def use_supabase_storage() -> bool:
    return os.environ.get('TIMEGRID_STORAGE', '').strip().lower() == 'supabase'
