# TimeGrid Supabase Data Plan

This plan covers only the TimeGrid calendar app. Mastodon keeps its own
PostgreSQL, Redis, Elasticsearch, and media storage.

## Product Model

TimeGrid currently stores one large JSON document with users, timelines,
subscriptions, published bundles, exports, notifications, signup intents, and
server-side auth state. The next version needs two upgrades at the same time:

- durable Supabase Postgres storage
- multiple calendars per personal and creator workspace

The best durable model is:

- A user owns many calendars.
- A calendar is a folder-like workspace tab.
- A calendar contains timelines and subscriptions.
- A self-owned timeline has a paired subscription so it can be shown, merged,
  published, and exported like any other source.
- A published bundle references selected subscriptions, usually from one
  calendar.
- An export can be scoped to a specific calendar. Dynamic exports rebuild from
  that calendar's current visible sources.

## Main Tables

- `timegrid_users`: app profiles keyed by `acct`.
- `timegrid_auth_identities`: Mastodon, Google, Apple, and email identities.
- `timegrid_calendars`: personal/creator calendar folders and vertical tabs.
- `timegrid_timelines`: editable event collections, with events stored as JSONB.
- `timegrid_subscriptions`: URL feeds, self-owned timeline feeds, merged bundles,
  archive state, visibility, author metadata, and source metadata.
- `timegrid_published_bundles`: public/private/invited share pages.
- `timegrid_published_bundle_items`: selected subscriptions in each published
  bundle.
- `timegrid_exports`: dynamic/static export tokens scoped to a calendar.
- `timegrid_notifications`: in-app notifications.
- `timegrid_signup_intents`: admin-visible signup requests.
- `timegrid_auth_pending` and `timegrid_auth_sessions`: server-side OAuth and
  session state, replacing `data/auth-state.json`.

## Why Events And Components Stay JSONB

Timeline events support recurrence, exception dates, overrides, imported fields,
and editor-only source metadata. Subscription bundle components also preserve
snapshots of external feeds. Keeping those as JSONB avoids over-normalizing the
most changeable parts of the product while still allowing ownership, calendar
membership, publishing, export, and auth to be relational.

GIN indexes are added to event JSONB so future search/filter features do not
require another migration.

## Calendar Tabs

Each user should get at least:

- a default personal calendar
- a default creator calendar

Existing timelines and subscriptions migrate into the matching default calendar
based on their `workspace` value:

- missing/`personal` -> default personal calendar
- `creator` -> default creator calendar
- `archive` remains archive workspace but keeps its calendar assignment

The UI can treat `timegrid_calendars` like vertical browser tabs. Switching tabs
changes the active calendar filter for subscriptions, timelines, previews, and
exports.

## Auth Direction

Mastodon OAuth is the only visible sign-in/sign-up option in the current phase
because TimeGrid can rely on the existing Mastodon email flow. Supabase
email/password, Google, and Apple can be added later by configuring those
providers and re-enabling their UI buttons.

The app profile should not assume one auth provider per account. Instead,
`timegrid_auth_identities` links many providers to one TimeGrid `acct`.

## Migration Approach

1. Back up live `store.json` and `auth-state.json`.
2. Create the Supabase schema through tracked migrations.
3. Import JSON into normalized tables.
4. Run compatibility reads that reconstruct the current store shape.
5. Flip the app to Supabase writes.
6. Keep JSON as a rollback backup during the first production window.

This staged approach lets existing import, edit, publish, export, dynamic export,
and embed routes keep working while storage moves underneath them.

## Verification

Run these checks before changing the Supabase schema or storage mapping:

```bash
python3 scripts/test_supabase_schema_contract.py
python3 scripts/test_storage_reconcile.py
python3 scripts/smoke_large_dataset.py
python3 scripts/smoke_timegrid_flows.py
```

The schema contract check proves the migration keeps the required TimeGrid
tables, calendar/export foreign keys, indexes, constraints, and RLS enablement.
The smoke flows cover multi-calendar personal and creator workspaces, imports,
dynamic/static exports, edit-after-dynamic-export, published bundle lifecycle,
timeline moves, and detach behavior.
