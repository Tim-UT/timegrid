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

- Email/password
- Mastodon OAuth

Google and Apple buttons are intentionally hidden for now. They can be restored
later after provider credentials are configured in Supabase Auth.

## Test Emails

Use these for development flow checks:

```text
sample1@time-grid.org
creator.sample@time-grid.org
```

Use at least 8 characters for passwords. If email confirmations are enabled,
signup returns a "check your email" state and login works after confirmation.

Supabase's built-in email sender is rate-limited. If signup returns
`email rate limit exceeded`, configure a custom SMTP provider in Supabase Auth
before broader testing or launch.

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
