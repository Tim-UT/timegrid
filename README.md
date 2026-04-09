# TimeGrid

TimeGrid is a self-hosted calendar web app with Mastodon-based sign-in and a simple Python backend.

## Features

- Single-process Python app with no framework dependency
- Static frontend assets bundled in `static/`
- Mastodon OAuth login
- Local JSON-backed storage for calendars and auth sessions
- Easy deployment behind Caddy, Nginx, or another reverse proxy

## Requirements

- Ubuntu or another Linux host
- Python 3.11+
- `python3-venv` recommended
- A Mastodon application with a client ID and secret

## Quick start

```bash
git clone https://github.com/Tim-UT/timegrid.git
cd timegrid
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 app.py
```

The app listens on the port defined by `PORT` in `.env`.

## Environment variables

- `APP_BASE_URL`: public HTTPS URL for this app
- `MASTODON_BASE_URL`: Mastodon server base URL
- `MASTODON_CLIENT_ID`: OAuth client ID for the Mastodon app
- `MASTODON_CLIENT_SECRET`: OAuth client secret for the Mastodon app
- `ADMIN_ACCOUNTS`: comma-separated list of admin account names or emails
- `PORT`: local listening port, defaults to `9100`

## Data and secrets

Runtime files are intentionally not tracked in git:

- `.env`
- `data/store.json`
- `data/auth-state.json`
- backup files, logs, and cache files

On first start, the app will create the `data/` files it needs automatically.

## systemd deployment

1. Create a virtualenv and install dependencies.
2. Copy `.env.example` to `.env` and fill in real values.
3. Copy `timegrid-calendar.service` to `/etc/systemd/system/timegrid-calendar.service`.
4. Adjust `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` if your install path is different.
5. Run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now timegrid-calendar.service
```

## Reverse proxy

Proxy your public domain to `127.0.0.1:9100` and keep TLS termination at the proxy.
