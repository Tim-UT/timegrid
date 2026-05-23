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

Enable these Supabase Auth providers:

- Email/password
- Google
- Apple

As of the latest production check on May 23, 2026, Supabase reports Email as
enabled and Google/Apple as disabled in `/auth/v1/settings`. TimeGrid hides
clickable Google/Apple buttons until Supabase reports those providers enabled.

For Google and Apple, configure the provider in its own developer console, then
copy the Supabase callback URL shown in the provider setup page into Google or
Apple. TimeGrid starts the flow at:

```text
/auth/provider/google/login
/auth/provider/apple/login
```

Supabase redirects back to `/auth` with the Auth token fragment. The browser
then calls `/api/auth/supabase/session`, and the backend creates a `tg_session`
cookie.

## Test Emails

Use these for development flow checks:

```text
sample1@time-grid.org
creator.sample@time-grid.org
apple.flow@time-grid.org
google.flow@time-grid.org
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
