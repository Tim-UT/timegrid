-- Supabase's project creation UI can add this helper in the exposed public
-- schema. TimeGrid does not call it from browser roles, so remove inherited
-- PUBLIC execute privileges as well as explicit anon/authenticated grants.

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
    execute 'revoke execute on function public.rls_auto_enable() from public, anon, authenticated';
  end if;
end;
$$;
