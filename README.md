# TimeGrid

TimeGrid is a combined self-hosted project for two related services:

- a calendar application that runs from this repository root
- a Mastodon codebase stored under `mastodon/` for the social server component

The goal of this repository is to keep the project deployable by other users without shipping production secrets, live user data, media storage, or machine-specific paths.

## What is in this repository

### 1. TimeGrid calendar app

The calendar app is a lightweight Python service built around the standard library HTTP server plus a small `requests` dependency.

It provides:

- Mastodon OAuth sign-in
- local JSON-backed persistence for app data and auth state
- static frontend assets from `static/`
- easy deployment as a single process behind a reverse proxy

### 2. Mastodon app

The `mastodon/` directory contains the Mastodon application code used as the social server part of the TimeGrid stack.

It is included in the same repo so the full project can be versioned together, but production runtime files for Mastodon are intentionally excluded from git.

## Repository layout

- `app.py`: main backend for the TimeGrid calendar app
- `static/`: frontend JavaScript, CSS, and browser assets for the calendar app
- `data/`: local JSON storage created and used at runtime by the calendar app
- `.env.example`: example environment file for the calendar app
- `requirements.txt`: Python dependency list for the calendar app
- `timegrid-calendar.service`: reusable example systemd unit for the calendar app
- `deploy/`: example deployment files for reverse proxy and service setup
- `mastodon/`: Mastodon codebase for the social server component
- `timegrids-icon.png`: app icon served by the calendar backend

## Architecture overview

A typical deployment uses two public domains:

- `calendar.example.com` for the TimeGrid calendar app
- `social.example.com` for Mastodon

A typical stack looks like this:

1. A reverse proxy such as Caddy receives public HTTPS traffic.
2. Calendar traffic is proxied to the local Python app on port `9100`.
3. Mastodon web traffic is proxied to the web process on port `3000`.
4. Mastodon streaming traffic is proxied to port `4000`.
5. Local runtime data stays on the server and is not committed back into git.

## TimeGrid calendar app

### Requirements

- Linux server such as Ubuntu
- Python 3.11 or newer
- `python3-venv` recommended
- a Mastodon application with client ID and client secret
- a reverse proxy if serving the app publicly over HTTPS

### Quick start

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

### Calendar environment variables

The calendar app reads configuration from `.env`.

- `APP_BASE_URL`: public HTTPS base URL for the calendar app
- `MASTODON_BASE_URL`: public base URL for the Mastodon server used for login
- `MASTODON_CLIENT_ID`: OAuth client ID created in Mastodon
- `MASTODON_CLIENT_SECRET`: OAuth client secret created in Mastodon
- `ADMIN_ACCOUNTS`: comma-separated list of admin account names or emails
- `PORT`: local bind port for the calendar app, defaults to `9100`

### Calendar runtime files

These files are created or updated on the server during runtime and are intentionally not tracked in git:

- `.env`
- `data/store.json`
- `data/auth-state.json`
- backup files such as `*.bak.*`
- logs and cache files

On first start, the app creates the `data/` files it needs automatically.

### Calendar deployment with systemd

A reusable example service file is included at `timegrid-calendar.service` and another copy is provided in `deploy/timegrid-calendar.service`.

Basic deployment flow:

1. Clone the repository to a target directory such as `/opt/timegrid`.
2. Create a virtualenv and install `requirements.txt`.
3. Copy `.env.example` to `.env` and fill in real values.
4. Copy the service file to `/etc/systemd/system/timegrid-calendar.service`.
5. Adjust `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` if needed.
6. Reload systemd and start the service.

Example:

```bash
sudo cp deploy/timegrid-calendar.service /etc/systemd/system/timegrid-calendar.service
sudo systemctl daemon-reload
sudo systemctl enable --now timegrid-calendar.service
```

## Reverse proxy

Example Caddy config is included in `deploy/Caddyfile`.

It shows the intended split:

- `calendar.example.com` -> `127.0.0.1:9100`
- `social.example.com` -> Mastodon web on `127.0.0.1:3000`
- Mastodon streaming endpoints -> `127.0.0.1:4000`

You can adapt the same layout for Nginx or another reverse proxy if preferred.

## Mastodon component

The `mastodon/` directory contains the Mastodon application source so the social part of the project can live in the same repository as the calendar app.

### Important note

This repository includes Mastodon source code, but not a full production Mastodon runtime state.

The following kinds of files should remain local to the server and must not be committed:

- `mastodon/.env.production`
- `mastodon/.env.production.local`
- `mastodon/public/system/`
- `mastodon/storage/`
- `mastodon/log/`
- `mastodon/tmp/`
- database dumps, keys, or any secret material

These paths are ignored in the root `.gitignore` so that future pushes do not accidentally include sensitive or machine-local data.

### Mastodon deployment expectations

Running Mastodon requires more infrastructure than the calendar app. In a normal production deployment you should expect to provide:

- Ruby and Bundler
- Node.js and Yarn
- PostgreSQL
- Redis
- the Mastodon web process
- the Mastodon Sidekiq worker process
- the Mastodon streaming process
- SMTP configuration for email
- optional object storage depending on your setup

The checked-in Mastodon folder is intended as source code and deployment base, not as a complete exported live environment.

## Safe git policy for this repository

This repository is structured so the source code can be pushed, while sensitive runtime state stays only on the server.

### Tracked

- application source code
- static assets
- deployment examples
- sample configuration files
- documentation

### Not tracked

- live secrets
- real `.env` files
- runtime JSON data
- uploaded media and storage directories
- logs, caches, backups, and generated temporary files

## Suggested deployment flow for a new user

1. Clone the repository.
2. Decide whether you want to deploy only the calendar app or both calendar and Mastodon.
3. For calendar-only deployment:
   - create `.env`
   - install Python dependencies
   - run `app.py` behind a reverse proxy
4. For full deployment:
   - deploy the calendar app from the repo root
   - deploy Mastodon from `mastodon/`
   - configure your reverse proxy for both domains
5. Keep all production secrets and runtime files outside git.

## Notes for maintainers

- If you change deployment paths, update the example systemd and proxy files.
- If you add new runtime directories, add them to `.gitignore` before pushing.
- If you copy data from a live server into the repo, review it carefully before committing.
- Prefer example files such as `.env.example` over committing real production values.

## License and upstreams

- The TimeGrid calendar app files in the repository root are this project's application code.
- The `mastodon/` directory contains Mastodon source code and follows Mastodon's own licensing and upstream project terms as documented in that subdirectory.
