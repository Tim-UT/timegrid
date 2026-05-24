-- TimeGrid is currently accessed by the Python backend through the Supabase
-- service-role key only. Keep browser/API roles locked out until explicit,
-- audited client-side RLS policies are introduced.

revoke all on table public.timegrid_users from anon, authenticated;
revoke all on table public.timegrid_auth_identities from anon, authenticated;
revoke all on table public.timegrid_calendars from anon, authenticated;
revoke all on table public.timegrid_timelines from anon, authenticated;
revoke all on table public.timegrid_subscriptions from anon, authenticated;
revoke all on table public.timegrid_published_bundles from anon, authenticated;
revoke all on table public.timegrid_published_bundle_items from anon, authenticated;
revoke all on table public.timegrid_exports from anon, authenticated;
revoke all on table public.timegrid_notifications from anon, authenticated;
revoke all on table public.timegrid_signup_intents from anon, authenticated;
revoke all on table public.timegrid_auth_pending from anon, authenticated;
revoke all on table public.timegrid_auth_sessions from anon, authenticated;
