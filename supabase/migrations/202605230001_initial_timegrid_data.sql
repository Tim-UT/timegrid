-- TimeGrid calendar app data model.
-- This intentionally excludes Mastodon runtime data.

create extension if not exists pgcrypto;
create extension if not exists citext;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table public.timegrid_users (
  acct text primary key,
  user_id text unique,
  supabase_user_id uuid unique,
  mastodon_account_id text,
  display_name text not null default '',
  avatar_url text not null default '',
  bio text not null default '',
  profile_visibility text not null default 'public'
    check (profile_visibility in ('public', 'private')),
  mastodon_profile jsonb not null default '{}'::jsonb,
  onboarding jsonb not null default '{"calendar_ready": true, "mastodon_ready": false}'::jsonb,
  blocked_accounts text[] not null default '{}',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.timegrid_auth_identities (
  id text primary key,
  acct text not null references public.timegrid_users(acct) on delete cascade,
  provider text not null,
  provider_subject text not null,
  email citext,
  email_verified boolean not null default false,
  supabase_user_id uuid,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (provider, provider_subject)
);

create table public.timegrid_calendars (
  id text primary key,
  owner_acct text not null references public.timegrid_users(acct) on delete cascade,
  workspace text not null default 'personal'
    check (workspace in ('personal', 'creator')),
  title text not null default 'My calendar',
  color text not null default '',
  position integer not null default 0,
  is_default boolean not null default false,
  archived boolean not null default false,
  settings jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_acct, workspace, title)
);

create unique index timegrid_one_default_calendar_per_workspace
on public.timegrid_calendars(owner_acct, workspace)
where is_default and not archived;

create table public.timegrid_timelines (
  id text primary key,
  owner_acct text not null references public.timegrid_users(acct) on delete cascade,
  calendar_id text references public.timegrid_calendars(id) on delete set null,
  subscription_id text,
  kind text not null default '',
  title text not null default 'Untitled timeline',
  description text not null default '',
  color text not null default '',
  events jsonb not null default '[]'::jsonb,
  position integer not null default 0,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.timegrid_subscriptions (
  id text primary key,
  owner_acct text not null references public.timegrid_users(acct) on delete cascade,
  calendar_id text references public.timegrid_calendars(id) on delete set null,
  title text not null default 'Subscription',
  url text not null default '',
  visible boolean not null default true,
  trashed boolean not null default false,
  kind text not null default '',
  workspace text not null default 'personal'
    check (workspace in ('personal', 'creator', 'archive')),
  owned_timeline_id text references public.timegrid_timelines(id) on delete set null,
  grouped_in text references public.timegrid_subscriptions(id) on delete set null,
  bundle_overlay_for text references public.timegrid_subscriptions(id) on delete set null,
  shell_source_id text references public.timegrid_subscriptions(id) on delete set null,
  components jsonb not null default '[]'::jsonb,
  color text not null default '',
  author_name text not null default '',
  author_acct text not null default '',
  official boolean not null default false,
  detached boolean not null default false,
  creator_archived boolean not null default false,
  source_code text not null default '',
  source_format text not null default '',
  hashtags text[] not null default '{}',
  description text not null default '',
  position integer not null default 0,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.timegrid_timelines
  add constraint timegrid_timelines_subscription_fk
  foreign key (subscription_id) references public.timegrid_subscriptions(id) on delete set null;

create table public.timegrid_published_bundles (
  id text primary key,
  slug text not null unique,
  owner_acct text not null references public.timegrid_users(acct) on delete cascade,
  calendar_id text references public.timegrid_calendars(id) on delete set null,
  title text not null default 'Published calendar',
  share_url text not null default '',
  visibility text not null default 'public'
    check (visibility in ('public', 'private', 'invited')),
  invited text[] not null default '{}',
  hashtags text[] not null default '{}',
  listed boolean not null default true,
  archived boolean not null default false,
  owner_detached boolean not null default false,
  allow_hard_copy boolean not null default false,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.timegrid_published_bundle_items (
  bundle_id text not null references public.timegrid_published_bundles(id) on delete cascade,
  subscription_id text not null references public.timegrid_subscriptions(id) on delete cascade,
  position integer not null default 0,
  created_at timestamptz not null default now(),
  primary key (bundle_id, subscription_id)
);

create table public.timegrid_exports (
  token text primary key,
  acct text not null references public.timegrid_users(acct) on delete cascade,
  calendar_id text references public.timegrid_calendars(id) on delete set null,
  kind text not null check (kind in ('dynamic', 'static')),
  title text not null default '',
  snapshot jsonb not null default '{}'::jsonb,
  ics_text text not null default '',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.timegrid_notifications (
  id text primary key,
  acct text not null references public.timegrid_users(acct) on delete cascade,
  kind text not null default '',
  title text not null default '',
  body text not null default '',
  href text not null default '',
  actor_acct text not null default '',
  read_at timestamptz,
  created_at timestamptz not null default now()
);

create table public.timegrid_signup_intents (
  id text primary key,
  provider text not null default '',
  email citext,
  display_name text not null default '',
  note text not null default '',
  next_path text not null default '/',
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'rejected', 'completed')),
  create_linked_mastodon boolean not null default true,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.timegrid_auth_pending (
  state text primary key,
  provider text not null default '',
  verifier text not null default '',
  nonce text not null default '',
  next_path text not null default '/',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null
);

create table public.timegrid_auth_sessions (
  session_id text primary key,
  acct text not null references public.timegrid_users(acct) on delete cascade,
  account_id text not null default '',
  display_name text not null default '',
  avatar_url text not null default '',
  role text not null default '',
  auth_provider text not null default '',
  access_token text not null default '',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null
);

create index timegrid_users_supabase_user_idx on public.timegrid_users(supabase_user_id);
create index timegrid_identities_acct_idx on public.timegrid_auth_identities(acct);
create index timegrid_identities_email_idx on public.timegrid_auth_identities(email) where email is not null;
create index timegrid_calendars_owner_workspace_idx on public.timegrid_calendars(owner_acct, workspace, archived, position);
create index timegrid_timelines_owner_calendar_idx on public.timegrid_timelines(owner_acct, calendar_id, position);
create index timegrid_timelines_events_gin_idx on public.timegrid_timelines using gin (events jsonb_path_ops);
create index timegrid_subscriptions_owner_calendar_idx on public.timegrid_subscriptions(owner_acct, calendar_id, workspace, trashed, position);
create index timegrid_subscriptions_grouped_idx on public.timegrid_subscriptions(grouped_in) where grouped_in is not null;
create index timegrid_subscriptions_owned_timeline_idx on public.timegrid_subscriptions(owned_timeline_id) where owned_timeline_id is not null;
create index timegrid_published_owner_idx on public.timegrid_published_bundles(owner_acct, archived, listed);
create index timegrid_published_calendar_idx on public.timegrid_published_bundles(calendar_id) where calendar_id is not null;
create index timegrid_exports_acct_calendar_idx on public.timegrid_exports(acct, calendar_id, kind);
create index timegrid_notifications_acct_created_idx on public.timegrid_notifications(acct, created_at desc);
create index timegrid_notifications_unread_idx on public.timegrid_notifications(acct) where read_at is null;
create index timegrid_auth_pending_expires_idx on public.timegrid_auth_pending(expires_at);
create index timegrid_auth_sessions_acct_idx on public.timegrid_auth_sessions(acct);
create index timegrid_auth_sessions_expires_idx on public.timegrid_auth_sessions(expires_at);

create trigger timegrid_users_set_updated_at before update on public.timegrid_users
  for each row execute function public.set_updated_at();
create trigger timegrid_identities_set_updated_at before update on public.timegrid_auth_identities
  for each row execute function public.set_updated_at();
create trigger timegrid_calendars_set_updated_at before update on public.timegrid_calendars
  for each row execute function public.set_updated_at();
create trigger timegrid_timelines_set_updated_at before update on public.timegrid_timelines
  for each row execute function public.set_updated_at();
create trigger timegrid_subscriptions_set_updated_at before update on public.timegrid_subscriptions
  for each row execute function public.set_updated_at();
create trigger timegrid_published_set_updated_at before update on public.timegrid_published_bundles
  for each row execute function public.set_updated_at();
create trigger timegrid_exports_set_updated_at before update on public.timegrid_exports
  for each row execute function public.set_updated_at();
create trigger timegrid_signup_intents_set_updated_at before update on public.timegrid_signup_intents
  for each row execute function public.set_updated_at();

alter table public.timegrid_users enable row level security;
alter table public.timegrid_auth_identities enable row level security;
alter table public.timegrid_calendars enable row level security;
alter table public.timegrid_timelines enable row level security;
alter table public.timegrid_subscriptions enable row level security;
alter table public.timegrid_published_bundles enable row level security;
alter table public.timegrid_published_bundle_items enable row level security;
alter table public.timegrid_exports enable row level security;
alter table public.timegrid_notifications enable row level security;
alter table public.timegrid_signup_intents enable row level security;
alter table public.timegrid_auth_pending enable row level security;
alter table public.timegrid_auth_sessions enable row level security;

-- The Python server will use the service-role key for now.
-- Client-side access should be added through explicit policies once the browser
-- talks to Supabase directly.
