-- Cover foreign keys that are commonly used for calendar moves, export
-- lookups, and bundle/shell cleanup. Composite owner-first indexes are useful
-- for app reads, but Postgres also needs leading-column indexes for FK checks.

create index if not exists timegrid_exports_calendar_fk_idx
on public.timegrid_exports(calendar_id)
where calendar_id is not null;

create index if not exists timegrid_published_bundle_items_subscription_fk_idx
on public.timegrid_published_bundle_items(subscription_id);

create index if not exists timegrid_subscriptions_bundle_overlay_for_fk_idx
on public.timegrid_subscriptions(bundle_overlay_for)
where bundle_overlay_for is not null;

create index if not exists timegrid_subscriptions_calendar_fk_idx
on public.timegrid_subscriptions(calendar_id)
where calendar_id is not null;

create index if not exists timegrid_subscriptions_shell_source_id_fk_idx
on public.timegrid_subscriptions(shell_source_id)
where shell_source_id is not null;

create index if not exists timegrid_timelines_calendar_fk_idx
on public.timegrid_timelines(calendar_id)
where calendar_id is not null;

create index if not exists timegrid_timelines_subscription_fk_idx
on public.timegrid_timelines(subscription_id)
where subscription_id is not null;

alter function public.set_updated_at() set search_path = public, pg_temp;

do $$
begin
  if exists (
    select 1
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname = 'rls_auto_enable'
      and p.pronargs = 0
  ) then
    execute 'revoke execute on function public.rls_auto_enable() from anon, authenticated';
  end if;
end;
$$;
