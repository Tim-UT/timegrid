#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'supabase' / 'migrations' / '202605230001_initial_timegrid_data.sql'
LOCKDOWN_MIGRATION = ROOT / 'supabase' / 'migrations' / '20260524024404_lock_timegrid_service_role_access.sql'
PERFORMANCE_MIGRATION = ROOT / 'supabase' / 'migrations' / '20260524143708_add_timegrid_fk_indexes_and_function_search_path.sql'
RLS_HELPER_LOCKDOWN_MIGRATION = ROOT / 'supabase' / 'migrations' / '20260524143829_revoke_public_execute_on_rls_auto_enable.sql'
CITEXT_EXTENSION_MIGRATION = ROOT / 'supabase' / 'migrations' / '20260524143948_move_citext_extension_out_of_public.sql'


def table_block(sql: str, table: str) -> str:
    pattern = rf'create table public\.{re.escape(table)} \((.*?)\n\);'
    match = re.search(pattern, sql, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise AssertionError(f'missing table {table}')
    return match.group(1)


def assert_columns(block: str, table: str, columns: list[str]) -> None:
    missing = [column for column in columns if not re.search(rf'^\s*{re.escape(column)}\b', block, flags=re.MULTILINE)]
    if missing:
        raise AssertionError(f'{table} missing columns: {", ".join(missing)}')


def main() -> int:
    sql = MIGRATION.read_text(encoding='utf-8')
    lockdown_sql = LOCKDOWN_MIGRATION.read_text(encoding='utf-8').lower()
    performance_sql = PERFORMANCE_MIGRATION.read_text(encoding='utf-8').lower()
    rls_helper_lockdown_sql = RLS_HELPER_LOCKDOWN_MIGRATION.read_text(encoding='utf-8').lower()
    citext_extension_sql = CITEXT_EXTENSION_MIGRATION.read_text(encoding='utf-8').lower()
    required_columns = {
        'timegrid_users': ['acct', 'supabase_user_id', 'mastodon_profile', 'onboarding'],
        'timegrid_auth_identities': ['acct', 'provider', 'provider_subject', 'email', 'supabase_user_id'],
        'timegrid_calendars': ['owner_acct', 'workspace', 'title', 'position', 'is_default', 'archived'],
        'timegrid_timelines': ['owner_acct', 'calendar_id', 'subscription_id', 'events', 'position'],
        'timegrid_subscriptions': ['owner_acct', 'calendar_id', 'workspace', 'owned_timeline_id', 'grouped_in', 'detached', 'position'],
        'timegrid_published_bundles': ['slug', 'owner_acct', 'calendar_id', 'visibility', 'invited', 'listed', 'archived', 'owner_detached'],
        'timegrid_published_bundle_items': ['bundle_id', 'subscription_id', 'position'],
        'timegrid_exports': ['token', 'acct', 'calendar_id', 'kind', 'snapshot', 'ics_text'],
        'timegrid_notifications': ['id', 'acct', 'read_at'],
        'timegrid_auth_pending': ['state', 'provider', 'verifier', 'next_path', 'expires_at'],
        'timegrid_auth_sessions': ['session_id', 'acct', 'auth_provider', 'expires_at'],
    }
    for table, columns in required_columns.items():
        assert_columns(table_block(sql, table), table, columns)
        rls = f'alter table public.{table} enable row level security;'
        if rls not in sql:
            raise AssertionError(f'{table} must enable row level security')
        revoke = f'revoke all on table public.{table} from anon, authenticated;'
        if revoke not in lockdown_sql:
            raise AssertionError(f'{table} must revoke anon/authenticated table access')

    required_indexes = [
        'timegrid_calendars_owner_workspace_idx',
        'timegrid_timelines_owner_calendar_idx',
        'timegrid_timelines_events_gin_idx',
        'timegrid_subscriptions_owner_calendar_idx',
        'timegrid_published_owner_idx',
        'timegrid_published_calendar_idx',
        'timegrid_exports_acct_calendar_idx',
        'timegrid_notifications_acct_created_idx',
        'timegrid_auth_sessions_expires_idx',
    ]
    for index in required_indexes:
        if index not in sql:
            raise AssertionError(f'missing index {index}')
    required_fk_indexes = [
        'timegrid_exports_calendar_fk_idx',
        'timegrid_published_bundle_items_subscription_fk_idx',
        'timegrid_subscriptions_bundle_overlay_for_fk_idx',
        'timegrid_subscriptions_calendar_fk_idx',
        'timegrid_subscriptions_shell_source_id_fk_idx',
        'timegrid_timelines_calendar_fk_idx',
        'timegrid_timelines_subscription_fk_idx',
    ]
    for index in required_fk_indexes:
        if index not in performance_sql:
            raise AssertionError(f'missing FK index {index}')
    if 'alter function public.set_updated_at() set search_path = public, pg_temp;' not in performance_sql:
        raise AssertionError('set_updated_at must pin search_path')
    if 'revoke execute on function public.rls_auto_enable() from anon, authenticated' not in performance_sql:
        raise AssertionError('rls_auto_enable must not be executable by browser roles')
    if 'revoke execute on function public.rls_auto_enable() from public, anon, authenticated' not in rls_helper_lockdown_sql:
        raise AssertionError('rls_auto_enable must revoke inherited public execute privileges')
    if 'create schema if not exists extensions;' not in citext_extension_sql:
        raise AssertionError('extensions schema must exist before moving citext')
    if 'alter extension citext set schema extensions;' not in citext_extension_sql:
        raise AssertionError('citext extension must live outside public schema')

    required_constraints = [
        'timegrid_one_default_calendar_per_workspace',
        'timegrid_timelines_subscription_fk',
        "check (workspace in ('personal', 'creator'))",
        "check (workspace in ('personal', 'creator', 'archive'))",
        "check (visibility in ('public', 'private', 'invited'))",
        "check (kind in ('dynamic', 'static'))",
    ]
    for constraint in required_constraints:
        if constraint not in sql:
            raise AssertionError(f'missing constraint {constraint}')

    print({
        'ok': True,
        'tables': len(required_columns),
        'indexes': len(required_indexes),
        'migration': str(MIGRATION.relative_to(ROOT)),
        'lockdown_migration': str(LOCKDOWN_MIGRATION.relative_to(ROOT)),
        'performance_migration': str(PERFORMANCE_MIGRATION.relative_to(ROOT)),
        'rls_helper_lockdown_migration': str(RLS_HELPER_LOCKDOWN_MIGRATION.relative_to(ROOT)),
        'citext_extension_migration': str(CITEXT_EXTENSION_MIGRATION.relative_to(ROOT)),
    })
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
