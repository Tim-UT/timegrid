# Supabase Auth Setup For TimeGrid

TimeGrid keeps its own `tg_session` cookie. Supabase Auth proves the user's
identity, then the Python app creates the TimeGrid session and profile.

## Required Environment

Server-only:

```bash
TIMEGRID_STORAGE=supabase
SUPABASE_URL=https://kivxtaprfkonlifigery.supabase.co
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
TIMEGRID_ENABLE_EMAIL_AUTH=false
TIMEGRID_ENABLE_EXTERNAL_AUTH=false
```

`SUPABASE_ANON_KEY` is safe for browser-driven Auth requests. The service-role
key is server-only and must never be embedded in JavaScript.

## Redirect URLs

Add these in Supabase Auth URL settings:

```text
https://calendar.time-grid.org/auth
https://calendar.time-grid.org/auth?next=/**
http://127.0.0.1:9102/auth
http://127.0.0.1:9102/auth?next=/**
```

Localhost entries are only for development.

## Providers

This phase uses:

- Mastodon OAuth

Email/password, Google, and Apple buttons are intentionally hidden for now.
Mastodon email/SMPP delivery is the active account creation path. Email/password
and external Supabase Auth routes are disabled unless their explicit feature
flags are set to `true`.

When custom SMTP and Supabase OAuth providers are ready, flip:

```bash
TIMEGRID_ENABLE_EMAIL_AUTH=true
TIMEGRID_ENABLE_EXTERNAL_AUTH=true
```

With those flags enabled, `/api/auth/options` advertises Mastodon, email,
Google, and Apple. The auth page renders the email/password form and Google /
Apple buttons. Google and Apple use Supabase Auth authorize URLs; successful
callbacks post the Supabase access token back to TimeGrid, which creates the
same `tg_session` cookie and links the provider identity to a TimeGrid profile.

Use `python3 scripts/smoke_auth_feature_flags.py` before turning this on. It
mocks Supabase Auth with example addresses `student@example.edu` and
`google.student@example.edu` / `apple.student@example.edu`, so it verifies
TimeGrid session/profile wiring without sending real email.

## Local Browser QA

For local UI testing without sending real OAuth or email traffic, run the app
with:

```bash
TIMEGRID_ENABLE_TEST_LOGIN=true
```

Then visit:

```text
/api/dev/test-login?acct=sample1&next=/u/sample1
```

This endpoint is disabled unless the env var is explicitly enabled. Do not set
it in production.
