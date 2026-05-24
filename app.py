#!/usr/bin/env python3
from __future__ import annotations

import base64
import calendar as pycalendar
import copy
import csv
import hashlib
import html
import json
import mimetypes
import os
import random
import secrets
import tempfile
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from typing import Any

import requests

from timegrid_storage import SupabaseStorage, use_supabase_storage

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / 'static'
DATA_DIR = BASE_DIR / 'data'
DATA_FILE = DATA_DIR / 'store.json'
AUTH_STATE_FILE = DATA_DIR / 'auth-state.json'

APP_BASE_URL = os.environ.get('APP_BASE_URL', 'https://calendar.time-grid.org').rstrip('/')
MASTODON_BASE_URL = os.environ.get('MASTODON_BASE_URL', 'https://social.time-grid.org').rstrip('/')
MASTODON_CLIENT_ID = os.environ['MASTODON_CLIENT_ID']
MASTODON_CLIENT_SECRET = os.environ['MASTODON_CLIENT_SECRET']
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY', '').strip()
ENABLE_TEST_LOGIN = os.environ.get('TIMEGRID_ENABLE_TEST_LOGIN', '').strip().lower() in {'1', 'true', 'yes'}
ADMIN_ACCOUNTS = {x.strip().lower() for x in os.environ.get('ADMIN_ACCOUNTS', '').split(',') if x.strip()}
OFFICIAL_ACCT = 'official'
OFFICIAL_CONTAINER_TITLE = 'Official sources'
OFFICIAL_F1_TITLE = 'F1'
OFFICIAL_F1_URL = 'https://ics.ecal.com/ecal-sub/65df431b44c8c20008a014e4/Formula%201.ics'
OFFICIAL_BUNDLE_SLUG = 'official-sources'
SESSION_COOKIE = 'tg_session'
SESSION_MAX_AGE = 60 * 60 * 24 * 14
PENDING_AUTH_MAX_AGE = 60 * 30
PORT = int(os.environ.get('PORT', '9100'))

pending_auth: dict[str, dict[str, Any]] = {}
sessions: dict[str, dict[str, Any]] = {}
OIDC_DISCOVERY_CACHE: dict[str, dict[str, Any]] = {}
SUPABASE_AUTH_SETTINGS_CACHE: dict[str, Any] = {'loaded_at': 0.0, 'settings': {}}
STORAGE = SupabaseStorage() if use_supabase_storage() else None
STORE_CACHE: dict[str, Any] | None = None
STORE_CACHE_LOCK = threading.RLock()
CALENDAR_TEXT_CACHE: dict[str, tuple[float, str]] = {}
CALENDAR_TEXT_CACHE_LOCK = threading.RLock()
CALENDAR_TEXT_CACHE_TTL = int(os.environ.get('TIMEGRID_CALENDAR_TEXT_CACHE_TTL', '600'))
SOURCE_PROXY_TIMEOUT_SECONDS = float(os.environ.get('TIMEGRID_SOURCE_PROXY_TIMEOUT_SECONDS', '5'))

APP_JS = STATIC_DIR / 'app.js'
SCHEDULE_X_FRAME_JS = STATIC_DIR / 'schedule-x-frame.js'
CALENDAR_DOMAIN_JS = STATIC_DIR / 'timegrid-calendar-domain.js'
CALENDAR_EDITOR_JS = STATIC_DIR / 'timegrid-calendar-editor.js'
TIMELINE_CONTROLLER_JS = STATIC_DIR / 'timegrid-timeline-controller.js'
SCHEDULE_X_READONLY_JS = STATIC_DIR / 'schedule-x-readonly.js'
SCHEDULE_X_READONLY_CSS = STATIC_DIR / 'schedule-x-readonly.css'
STYLES_CSS = STATIC_DIR / 'styles.css'


def asset_href(path: str, source: Path) -> str:
    try:
        version = int(source.stat().st_mtime)
    except OSError:
        version = 1
    return f'{path}?v={version}'
ICON = BASE_DIR / 'timegrids-icon.png'


def ensure_store() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        save_store({'users': {}, 'published': {}})


def load_store() -> dict[str, Any]:
    global STORE_CACHE
    if STORAGE is not None:
        with STORE_CACHE_LOCK:
            if STORE_CACHE is None:
                data = STORAGE.load_store()
                data.setdefault('users', {})
                data.setdefault('published', {})
                data.setdefault('signup_intents', [])
                data.setdefault('exports', {})
                if ensure_official_content(data):
                    STORAGE.save_store(data)
                STORE_CACHE = copy.deepcopy(data)
            return copy.deepcopy(STORE_CACHE)
    ensure_store()
    with DATA_FILE.open('r', encoding='utf-8') as fh:
        data = json.load(fh)
    data.setdefault('users', {})
    data.setdefault('published', {})
    data.setdefault('signup_intents', [])
    data.setdefault('exports', {})
    if ensure_official_content(data):
        save_store(data)
    return data


def save_store(data: dict[str, Any]) -> None:
    global STORE_CACHE
    if STORAGE is not None:
        STORAGE.save_store(data)
        with STORE_CACHE_LOCK:
            STORE_CACHE = copy.deepcopy(data)
        with CALENDAR_TEXT_CACHE_LOCK:
            CALENDAR_TEXT_CACHE.clear()
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False, dir=DATA_DIR) as tmp:
        json.dump(data, tmp, ensure_ascii=True, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name
    os.replace(temp_name, DATA_FILE)
    with CALENDAR_TEXT_CACHE_LOCK:
        CALENDAR_TEXT_CACHE.clear()


def save_user_fragment(
    data: dict[str, Any],
    acct: str,
    *,
    identities: bool = False,
    calendars: bool = False,
    subscriptions: bool = False,
    timelines: bool = False,
    exports: bool = False,
    notifications: bool = False,
) -> None:
    global STORE_CACHE
    if STORAGE is not None:
        STORAGE.save_user_fragment(
            data,
            acct,
            identities=identities,
            calendars=calendars,
            subscriptions=subscriptions,
            timelines=timelines,
            exports=exports,
            notifications=notifications,
        )
        with STORE_CACHE_LOCK:
            STORE_CACHE = copy.deepcopy(data)
        with CALENDAR_TEXT_CACHE_LOCK:
            CALENDAR_TEXT_CACHE.clear()
        return
    save_store(data)


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False, dir=DATA_DIR) as tmp:
        json.dump(payload, tmp, ensure_ascii=True, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name
    os.replace(temp_name, path)


def ensure_auth_state() -> None:
    if STORAGE is not None:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not AUTH_STATE_FILE.exists():
        write_json_file(AUTH_STATE_FILE, {'pending_auth': {}, 'sessions': {}})


def prune_auth_state(now_ts: float | None = None) -> bool:
    now_ts = time.time() if now_ts is None else now_ts
    changed = False
    for state, auth in list(pending_auth.items()):
        created_at = float(auth.get('created_at') or 0)
        if not created_at or now_ts - created_at > PENDING_AUTH_MAX_AGE:
            pending_auth.pop(state, None)
            changed = True
    for session_id, session in list(sessions.items()):
        created_at = float(session.get('created_at') or 0)
        max_age = int(session.get('max_age') or SESSION_MAX_AGE)
        if not created_at or now_ts - created_at > max_age:
            sessions.pop(session_id, None)
            changed = True
    return changed


def load_auth_state() -> None:
    global pending_auth, sessions
    if STORAGE is not None:
        data = STORAGE.load_auth_state()
        pending_auth = data.get('pending_auth', {})
        sessions = data.get('sessions', {})
        if prune_auth_state():
            save_auth_state()
        return
    ensure_auth_state()
    with AUTH_STATE_FILE.open('r', encoding='utf-8') as fh:
        data = json.load(fh)
    pending_auth = data.get('pending_auth', {})
    sessions = data.get('sessions', {})
    if prune_auth_state():
        save_auth_state()


def save_auth_state() -> None:
    if STORAGE is not None:
        prune_auth_state()
        STORAGE.save_auth_state(pending_auth, sessions)
        return
    ensure_auth_state()
    prune_auth_state()
    write_json_file(AUTH_STATE_FILE, {'pending_auth': pending_auth, 'sessions': sessions})


def now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def slugify(value: str) -> str:
    cleaned = ''.join(ch.lower() if ch.isalnum() else '-' for ch in value).strip('-')
    cleaned = '-'.join(part for part in cleaned.split('-') if part)
    return cleaned[:48] or 'calendar'


def new_id(prefix: str) -> str:
    return f'{prefix}_{secrets.token_urlsafe(8)}'


def password_hash(password: str, salt_bytes: bytes) -> str:
    return hashlib.scrypt(password.encode('utf-8'), salt=salt_bytes, n=2**14, r=8, p=1).hex()


def hash_password(password: str, salt: str | None = None) -> dict[str, str]:
    salt_bytes = bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    digest = password_hash(password, salt_bytes)
    return {'salt': salt_bytes.hex(), 'hash': digest}


def env_value(name: str) -> str:
    return str(os.environ.get(name) or '').strip()


def supabase_auth_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY)


def email_auth_enabled() -> bool:
    return supabase_auth_enabled() and env_value('TIMEGRID_ENABLE_EMAIL_AUTH').lower() == 'true'


def external_auth_enabled() -> bool:
    return supabase_auth_enabled() and env_value('TIMEGRID_ENABLE_EXTERNAL_AUTH').lower() == 'true'


def supabase_auth_headers(access_token: str = '') -> dict[str, str]:
    token = access_token or SUPABASE_ANON_KEY
    return {
        'apikey': SUPABASE_ANON_KEY,
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }


def supabase_auth_settings() -> dict[str, Any]:
    if not supabase_auth_enabled():
        return {}
    loaded_at = float(SUPABASE_AUTH_SETTINGS_CACHE.get('loaded_at') or 0)
    cached = SUPABASE_AUTH_SETTINGS_CACHE.get('settings') or {}
    if cached and time.time() - loaded_at < 300:
        return cached
    try:
        resp = requests.get(
            f'{SUPABASE_URL}/auth/v1/settings',
            headers=supabase_auth_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        settings = resp.json()
    except Exception:
        settings = {}
    SUPABASE_AUTH_SETTINGS_CACHE['loaded_at'] = time.time()
    SUPABASE_AUTH_SETTINGS_CACHE['settings'] = settings
    return settings


def supabase_provider_enabled(provider_id: str) -> bool:
    settings = supabase_auth_settings()
    external = settings.get('external') if isinstance(settings, dict) else {}
    if not isinstance(external, dict):
        return False
    return bool(external.get(provider_id))


def decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split('.')
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = '=' * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload + padding).decode('utf-8'))
    except Exception:
        return {}


def load_oidc_discovery(url: str) -> dict[str, Any]:
    cached = OIDC_DISCOVERY_CACHE.get(url)
    if cached:
        return cached
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    OIDC_DISCOVERY_CACHE[url] = data
    return data


def external_provider_config(provider_id: str) -> dict[str, Any] | None:
    pid = str(provider_id or '').strip().lower()
    if pid == 'google':
        if not external_auth_enabled():
            return None
        client_id = env_value('GOOGLE_CLIENT_ID')
        client_secret = env_value('GOOGLE_CLIENT_SECRET')
        if not client_id or not client_secret:
            return None
        return {
            'id': 'google',
            'label': 'Google',
            'description': 'Continue with your Google account.',
            'client_id': client_id,
            'client_secret': client_secret,
            'authorize_url': 'https://accounts.google.com/o/oauth2/v2/auth',
            'token_url': 'https://oauth2.googleapis.com/token',
            'userinfo_url': 'https://openidconnect.googleapis.com/v1/userinfo',
            'scope': 'openid email profile',
            'use_pkce': True,
            'callback_method': 'GET',
        }
    if pid == 'apple':
        if not external_auth_enabled():
            return None
        client_id = env_value('APPLE_CLIENT_ID')
        client_secret = env_value('APPLE_CLIENT_SECRET')
        if not client_id or not client_secret:
            return None
        return {
            'id': 'apple',
            'label': 'Apple',
            'description': 'Continue with your Apple account.',
            'client_id': client_id,
            'client_secret': client_secret,
            'authorize_url': 'https://appleid.apple.com/auth/authorize',
            'token_url': 'https://appleid.apple.com/auth/token',
            'scope': 'name email',
            'use_pkce': False,
            'callback_method': 'POST',
            'response_mode': 'form_post',
        }
    if pid == 'uoft':
        if not external_auth_enabled():
            return None
        discovery_url = env_value('UOFT_OIDC_DISCOVERY_URL')
        client_id = env_value('UOFT_CLIENT_ID')
        client_secret = env_value('UOFT_CLIENT_SECRET')
        if not discovery_url or not client_id or not client_secret:
            return None
        discovery = load_oidc_discovery(discovery_url)
        return {
            'id': 'uoft',
            'label': 'UofT',
            'description': 'Continue with your University of Toronto account.',
            'client_id': client_id,
            'client_secret': client_secret,
            'authorize_url': discovery.get('authorization_endpoint') or '',
            'token_url': discovery.get('token_endpoint') or '',
            'userinfo_url': discovery.get('userinfo_endpoint') or '',
            'scope': env_value('UOFT_OIDC_SCOPE') or 'openid email profile',
            'use_pkce': True,
            'callback_method': 'GET',
        }
    return None


def configured_auth_providers() -> list[dict[str, Any]]:
    return [{
        'id': 'mastodon',
        'label': 'Mastodon',
        'description': 'Sign in with your linked social.time-grid.org account.',
        'status': 'active',
        'provisions_mastodon': True,
    }]


def verify_password(password: str, record: dict[str, Any]) -> bool:
    salt = str(record.get('salt') or '').strip()
    digest = str(record.get('hash') or '').strip()
    if not salt or not digest:
        return False
    try:
        candidate = password_hash(password, bytes.fromhex(salt))
    except ValueError:
        return False
    return secrets.compare_digest(candidate, digest)


def unique_acct_from_seed(store: dict[str, Any], seed: str) -> str:
    base = slugify(seed.replace('@', '-at-'))[:24] or f'user-{secrets.token_hex(2)}'
    acct = base
    suffix = 2
    while acct in store.get('users', {}):
        acct = f'{base}-{suffix}'
        suffix += 1
    return acct


def safe_post_auth_path(next_path: str, acct: str) -> str:
    raw = str(next_path or '/').strip() or '/'
    parsed = urllib.parse.urlparse(raw)
    path = parsed.path or '/'
    if path in {'', '/', '/auth', '/auth/login'} or path.startswith('/auth/provider/'):
        return f'/u/{acct}'
    if path.startswith('/u/'):
        parts = [part for part in path.split('/') if part]
        if len(parts) >= 2 and parts[1] != acct:
            return f'/u/{acct}'
    return path + (f'?{parsed.query}' if parsed.query else '')


def make_cookie_header(session_id: str, expires_in: int = 60 * 60 * 24 * 14) -> str:
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE] = session_id
    cookie[SESSION_COOKIE]['path'] = '/'
    cookie[SESSION_COOKIE]['httponly'] = True
    cookie[SESSION_COOKIE]['secure'] = True
    cookie[SESSION_COOKIE]['samesite'] = 'Lax'
    cookie[SESSION_COOKIE]['max-age'] = str(expires_in)
    return cookie.output(header='').strip()


def clear_cookie_header() -> str:
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE] = ''
    cookie[SESSION_COOKIE]['path'] = '/'
    cookie[SESSION_COOKIE]['httponly'] = True
    cookie[SESSION_COOKIE]['secure'] = True
    cookie[SESSION_COOKIE]['samesite'] = 'Lax'
    cookie[SESSION_COOKIE]['max-age'] = '0'
    return cookie.output(header='').strip()


def timeline_ics_url(acct: str, timeline_id: str) -> str:
    return f'{APP_BASE_URL}/ics/{urllib.parse.quote(acct)}/{urllib.parse.quote(timeline_id)}.ics'


def timeline_edit_url(acct: str, timeline_id: str) -> str:
    return f'/u/{urllib.parse.quote(acct)}/timelines/{urllib.parse.quote(timeline_id)}'


def build_embed_url(urls: list[str]) -> str:
    base = 'https://open-web-calendar.hosted.quelltext.eu/calendar.html'
    params = [('url', url) for url in urls]
    query = urllib.parse.urlencode(params, doseq=True)
    return f'{base}?{query}' if query else base


def build_trusted_embed_url(public_url: str) -> str:
    parsed = urllib.parse.urlparse(public_url)
    path = parsed.path or '/'
    query = f'?{parsed.query}' if parsed.query else ''
    return f'{APP_BASE_URL}/embed{path}{query}'


def bundle_feed_url(slug: str) -> str:
    return f'{APP_BASE_URL}/bundle/{urllib.parse.quote(slug)}.ics'


def bundle_subscribe_url(slug: str) -> str:
    return f'{APP_BASE_URL}/subscribe/{urllib.parse.quote(slug)}'


def personal_bundle_feed_url(acct: str, sub_id: str) -> str:
    return f'{APP_BASE_URL}/bundle/private/{urllib.parse.quote(acct)}/{urllib.parse.quote(sub_id)}.ics'


TIMELINE_COLORS = [
    '#1f7a8c', '#2388b8', '#2a9d8f', '#4d908e', '#577590',
    '#6a4c93', '#7b6d8d', '#8f5d5d', '#bc6c25', '#c06c84',
    '#d97706', '#e07a5f', '#e76f51', '#ef476f', '#f28482',
    '#f4a261', '#7f5539', '#90be6d', '#43aa8b', '#3d5a80',
]


def random_timeline_color() -> str:
    return random.choice(TIMELINE_COLORS)


def ensure_subscription_color(item: dict[str, Any]) -> str:
    color = str(item.get('color') or '').strip()
    if not color:
        color = random_timeline_color()
        item['color'] = color
    return color


def ensure_timeline_color(item: dict[str, Any]) -> str:
    color = str(item.get('color') or '').strip()
    if not color:
        color = random_timeline_color()
        item['color'] = color
    return color


def ensure_subscription_author(user: dict[str, Any], item: dict[str, Any]) -> None:
    if item.get('kind') == 'bundle':
        return
    acct = str(item.get('author_acct') or user.get('acct') or '').strip().lower()
    name = str(item.get('author_name') or user.get('display_name') or acct or user.get('acct') or '').strip()
    item['author_acct'] = acct
    item['author_name'] = name or acct or user.get('acct') or 'Unknown author'


def component_identity(item: dict[str, Any]) -> str:
    item_id = str(item.get('id') or '').strip()
    if item_id:
        return f'id:{item_id}'
    url = str(item.get('url') or '').strip()
    author = str(item.get('author_acct') or item.get('author_name') or '').strip().lower()
    title = str(item.get('title') or '').strip().lower()
    return f'url:{url}|author:{author}|title:{title}'


def pick_merge_color(items: list[dict[str, Any]]) -> str:
    pool = [str(item.get('color') or '').strip() for item in items if str(item.get('color') or '').strip()]
    return random.choice(pool) if pool else random_timeline_color()


def default_calendar_id(acct: str, workspace: str) -> str:
    base = slugify(acct)
    return f'cal_{base}_{workspace}'


def calendar_workspace(item: dict[str, Any] | None) -> str:
    workspace = str((item or {}).get('workspace') or '').strip().lower()
    if workspace == 'creator':
        return 'creator'
    return 'personal'


def default_calendar_record(acct: str, workspace: str) -> dict[str, Any]:
    return {
        'id': default_calendar_id(acct, workspace),
        'workspace': workspace,
        'title': 'Personal' if workspace == 'personal' else 'Creator',
        'color': '#2f7d80',
        'position': 0 if workspace == 'personal' else 1,
        'is_default': True,
        'archived': False,
        'created_at': now_iso(),
        'updated_at': now_iso(),
    }


def ensure_user_calendars(user: dict[str, Any]) -> list[dict[str, Any]]:
    acct = str(user.get('acct') or '').strip().lower()
    calendars = user.setdefault('calendars', [])
    existing = {(item.get('workspace'), item.get('id')) for item in calendars}
    for workspace in ('personal', 'creator'):
        default_id = default_calendar_id(acct, workspace)
        if (workspace, default_id) not in existing and not any(item.get('workspace') == workspace and item.get('is_default') for item in calendars):
            calendars.append(default_calendar_record(acct, workspace))
    calendars.sort(key=lambda item: (str(item.get('workspace') or ''), int(item.get('position') or 0), str(item.get('title') or '').lower()))
    return calendars


def default_calendar_for(user: dict[str, Any], workspace: str) -> str:
    calendars = ensure_user_calendars(user)
    for item in calendars:
        if item.get('workspace') == workspace and item.get('is_default') and not item.get('archived'):
            return str(item.get('id') or '')
    for item in calendars:
        if item.get('workspace') == workspace and not item.get('archived'):
            return str(item.get('id') or '')
    return default_calendar_id(str(user.get('acct') or ''), workspace)


def resolve_calendar_id(user: dict[str, Any], requested: str, workspace: str) -> str:
    requested = str(requested or '').strip()
    calendars = ensure_user_calendars(user)
    if requested and any(item.get('id') == requested and item.get('workspace') == workspace and not item.get('archived') for item in calendars):
        return requested
    return default_calendar_for(user, workspace)


def calendar_visible(item: dict[str, Any], calendar_id: str) -> bool:
    return not calendar_id or str(item.get('calendar_id') or '') == calendar_id


def default_user(acct: str) -> dict[str, Any]:
    return {
        'user_id': new_id('usr'),
        'acct': acct,
        'account_id': '',
        'display_name': acct,
        'avatar': '',
        'bio': '',
        'profile_visibility': 'public',
        'blocked_accounts': [],
        'notifications': [],
        'linked_identities': [],
        'mastodon_profile': {'acct': acct, 'provisioned': True},
        'onboarding': {'calendar_ready': True, 'mastodon_ready': True},
        'subscriptions': [],
        'timelines': [],
        'published': [],
        'calendars': [default_calendar_record(acct, 'personal'), default_calendar_record(acct, 'creator')],
        'updated_at': now_iso(),
    }


def ensure_user(store: dict[str, Any], acct: str) -> dict[str, Any]:
    user = store['users'].setdefault(acct, default_user(acct))
    user.setdefault('user_id', new_id('usr'))
    user.setdefault('subscriptions', [])
    user.setdefault('timelines', [])
    user.setdefault('published', [])
    user.setdefault('display_name', acct)
    user.setdefault('avatar', '')
    user.setdefault('bio', '')
    user.setdefault('profile_visibility', 'public')
    user.setdefault('blocked_accounts', [])
    user.setdefault('notifications', [])
    user.setdefault('linked_identities', [])
    user.setdefault('mastodon_profile', {'acct': acct, 'provisioned': True})
    user.setdefault('onboarding', {'calendar_ready': True, 'mastodon_ready': True})
    user.setdefault('updated_at', now_iso())
    ensure_user_calendars(user)
    for item in user.get('subscriptions', []):
        item.setdefault('detached', False)
        if 'workspace' not in item:
            item['workspace'] = 'creator' if item.get('creator_archived') else 'personal'
        item.setdefault('creator_archived', item.get('workspace') == 'archive')
        if not item.get('calendar_id'):
            item['calendar_id'] = default_calendar_for(user, calendar_workspace(item))
        ensure_subscription_color(item)
        ensure_subscription_author(user, item)
    for item in user.get('timelines', []):
        ensure_timeline_color(item)
    for item in user.get('timelines', []):
        sub_id = item.get('subscription_id')
        sub = find_subscription(user, sub_id) if sub_id else None
        if not item.get('calendar_id'):
            item['calendar_id'] = str((sub or {}).get('calendar_id') or default_calendar_for(user, calendar_workspace(sub)))
        if sub and not item.get('color'):
            item['color'] = ensure_subscription_color(sub)
        elif sub and not sub.get('color') and item.get('color'):
            sub['color'] = item.get('color')
    migrate_merged_groups(user)
    return user


def create_test_login_session(acct: str, display_name: str = '', *, role: str = '') -> tuple[str, dict[str, Any], dict[str, Any]]:
    acct = slugify(str(acct or 'sample1').strip().lower()) or 'sample1'
    display_name = str(display_name or acct).strip() or acct
    store = load_store()
    user = ensure_user(store, acct)
    user['display_name'] = display_name
    user['updated_at'] = now_iso()
    save_user_fragment(store, acct, calendars=True)
    session_id = new_id('sess')
    session = {
        'acct': acct,
        'account_id': '',
        'display_name': display_name,
        'avatar': user.get('avatar') or '',
        'role': role,
        'created_at': time.time(),
        'auth_provider': 'test',
        'access_token': '',
        'max_age': SESSION_MAX_AGE,
    }
    sessions[session_id] = session
    save_auth_state()
    return session_id, session, user


def linked_identities(user: dict[str, Any]) -> list[dict[str, Any]]:
    return user.setdefault('linked_identities', [])


def find_identity_by_email(store: dict[str, Any], email: str, provider: str = 'email') -> tuple[dict[str, Any], dict[str, Any]] | tuple[None, None]:
    target = str(email or '').strip().lower()
    if not target:
        return None, None
    for acct, raw_user in store.get('users', {}).items():
        user = ensure_user(store, acct)
        for identity in linked_identities(user):
            if str(identity.get('provider') or '').strip().lower() != provider:
                continue
            if str(identity.get('email') or '').strip().lower() == target:
                return user, identity
    return None, None


def find_identity_by_subject(store: dict[str, Any], provider: str, subject: str) -> tuple[dict[str, Any], dict[str, Any]] | tuple[None, None]:
    target_provider = str(provider or '').strip().lower()
    target_subject = str(subject or '').strip()
    if not target_provider or not target_subject:
        return None, None
    for acct, raw_user in store.get('users', {}).items():
        user = ensure_user(store, acct)
        for identity in linked_identities(user):
            if str(identity.get('provider') or '').strip().lower() != target_provider:
                continue
            if str(identity.get('provider_subject') or '').strip() == target_subject:
                return user, identity
    return None, None


def resolve_or_create_external_user(
    store: dict[str, Any],
    *,
    provider: str,
    subject: str,
    email: str,
    display_name: str,
    avatar: str = '',
    email_verified: bool = True,
) -> dict[str, Any]:
    user, identity = find_identity_by_subject(store, provider, subject)
    if not user and email:
        user, identity = find_identity_by_email(store, email, 'email')
    if not user and email:
        for acct, raw_user in store.get('users', {}).items():
            candidate = ensure_user(store, acct)
            for item in linked_identities(candidate):
                if str(item.get('email') or '').strip().lower() == email.lower():
                    user = candidate
                    identity = item
                    break
            if user:
                break
    if not user:
        acct_seed = display_name or email.split('@', 1)[0] if email else provider
        acct = unique_acct_from_seed(store, acct_seed)
        user = ensure_user(store, acct)
    user['display_name'] = display_name or user.get('display_name') or user['acct']
    if avatar:
        user['avatar'] = avatar
    user['updated_at'] = now_iso()
    user.setdefault('mastodon_profile', {'acct': user['acct'], 'provisioned': False})
    user['onboarding'] = {'calendar_ready': True, 'mastodon_ready': bool((user.get('onboarding') or {}).get('mastodon_ready', False))}
    identities = linked_identities(user)
    existing = next((item for item in identities if str(item.get('provider') or '').strip().lower() == provider and str(item.get('provider_subject') or '').strip() == subject), None)
    if not existing:
        identities.append({
            'id': new_id('ident'),
            'provider': provider,
            'provider_subject': subject,
            'email': email,
            'email_verified': bool(email_verified),
            'created_at': now_iso(),
        })
    return user


def create_session_for_user(user: dict[str, Any], *, provider: str, role: str = '') -> tuple[str, dict[str, Any]]:
    session_id = secrets.token_urlsafe(32)
    session = {
        'acct': user['acct'],
        'account_id': str(user.get('account_id') or ''),
        'display_name': user.get('display_name') or user['acct'],
        'avatar': user.get('avatar') or '',
        'role': role,
        'created_at': time.time(),
        'auth_provider': provider,
        'access_token': '',
        'max_age': SESSION_MAX_AGE,
    }
    sessions[session_id] = session
    save_auth_state()
    return session_id, session


def supabase_redirect_url(next_path: str) -> str:
    return f'{APP_BASE_URL}/auth?next={urllib.parse.quote(next_path or "/", safe="/?=&")}'


def supabase_oauth_authorize_url(provider_id: str, next_path: str) -> str:
    return (
        f'{SUPABASE_URL}/auth/v1/authorize?provider={urllib.parse.quote(provider_id)}'
        f'&redirect_to={urllib.parse.quote(supabase_redirect_url(next_path), safe="")}'
    )


def supabase_auth_user(access_token: str) -> dict[str, Any]:
    if not supabase_auth_enabled():
        raise RuntimeError('supabase_auth_disabled')
    resp = requests.get(
        f'{SUPABASE_URL}/auth/v1/user',
        headers=supabase_auth_headers(access_token),
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def resolve_or_create_supabase_user(store: dict[str, Any], auth_user: dict[str, Any], provider: str = '') -> dict[str, Any]:
    metadata = auth_user.get('user_metadata') or {}
    app_metadata = auth_user.get('app_metadata') or {}
    identities = auth_user.get('identities') or []
    provider = provider or str(app_metadata.get('provider') or '')
    if not provider and identities:
        provider = str((identities[0] or {}).get('provider') or '')
    provider = provider or 'email'
    subject = str(auth_user.get('id') or '')
    email = str(auth_user.get('email') or metadata.get('email') or '').strip().lower()
    display_name = str(
        metadata.get('full_name')
        or metadata.get('name')
        or metadata.get('display_name')
        or (email.split('@', 1)[0] if email else provider)
    ).strip()
    avatar = str(metadata.get('avatar_url') or metadata.get('picture') or '')
    user = resolve_or_create_external_user(
        store,
        provider=provider,
        subject=subject,
        email=email,
        display_name=display_name,
        avatar=avatar,
        email_verified=bool(auth_user.get('email_confirmed_at') or auth_user.get('confirmed_at') or metadata.get('email_verified')),
    )
    user['supabase_user_id'] = subject
    for identity in linked_identities(user):
        if identity.get('provider') == provider and identity.get('provider_subject') == subject:
            identity['supabase_user_id'] = subject
    return user


def create_session_from_supabase_access_token(access_token: str, provider: str = '') -> tuple[str, dict[str, Any], dict[str, Any]]:
    auth_user = supabase_auth_user(access_token)
    store = load_store()
    user = resolve_or_create_supabase_user(store, auth_user, provider=provider)
    user['updated_at'] = now_iso()
    save_user_fragment(store, user['acct'], identities=True, calendars=True)
    session_id, session = create_session_for_user(user, provider=provider or 'supabase')
    session['access_token'] = access_token
    save_auth_state()
    return session_id, session, user


def find_subscription(user: dict[str, Any], sub_id: str) -> dict[str, Any] | None:
    return next((sub for sub in user.get('subscriptions', []) if sub.get('id') == sub_id), None)


def find_timeline(user: dict[str, Any], timeline_id: str) -> dict[str, Any] | None:
    return next((item for item in user.get('timelines', []) if item.get('id') == timeline_id), None)


def normalize_positions(items: list[dict[str, Any]]) -> None:
    for index, item in enumerate(items):
        item['position'] = index


def move_item_to_position(items: list[dict[str, Any]], item: dict[str, Any], position: Any) -> None:
    if item not in items:
        return
    try:
        target = int(position)
    except (TypeError, ValueError):
        target = len(items) - 1
    items.remove(item)
    target = max(0, min(target, len(items)))
    items.insert(target, item)
    normalize_positions(items)


def move_subscription_to_position(user: dict[str, Any], item: dict[str, Any], position: Any) -> None:
    workspace = item.get('workspace') or 'personal'
    calendar_id = item.get('calendar_id') or default_calendar_for(user, calendar_workspace(item))
    siblings = [
        sub for sub in user.get('subscriptions', [])
        if sub.get('workspace', 'personal') == workspace
        and (sub.get('calendar_id') or default_calendar_for(user, calendar_workspace(sub))) == calendar_id
        and not sub.get('trashed')
        and not sub.get('grouped_in')
    ]
    siblings.sort(key=lambda sub: (int(sub.get('position') or 0), str(sub.get('title') or '').lower()))
    move_item_to_position(siblings, item, position)
    position_by_id = {sub.get('id'): sub.get('position') for sub in siblings}
    for sub in user.get('subscriptions', []):
        if sub.get('id') in position_by_id:
            sub['position'] = position_by_id[sub.get('id')]


def move_subscription_to_calendar(user: dict[str, Any], item: dict[str, Any], calendar_id: str, workspace: str) -> None:
    item['calendar_id'] = calendar_id
    item['workspace'] = workspace
    item['creator_archived'] = workspace == 'archive'
    timeline_id = item.get('owned_timeline_id')
    timeline = find_timeline(user, timeline_id) if timeline_id else None
    if timeline:
        timeline['calendar_id'] = calendar_id
        timeline['workspace'] = workspace
        timeline['updated_at'] = now_iso()


def migrate_merged_groups(user: dict[str, Any]) -> None:
    for item in user.get('subscriptions', []):
        if item.get('kind') != 'bundle':
            continue
        item['url'] = ''
        item.setdefault('components', [])
        item.setdefault('visible', True)
        if item.get('trashed'):
            continue
        for ref in item.get('components', []):
            child = find_subscription(user, ref.get('id', '')) if ref.get('id') else None
            if not child or child.get('id') == item.get('id'):
                continue
            if child.get('grouped_in') and child.get('grouped_in') != item.get('id'):
                parent = find_subscription(user, child.get('grouped_in', ''))
                if parent and not parent.get('trashed'):
                    continue
            child['grouped_in'] = item['id']
            child['trashed'] = False
            if item.get('visible'):
                child['visible'] = True


def grouped_children(user: dict[str, Any], bundle_id: str) -> list[dict[str, Any]]:
    return [sub for sub in user.get('subscriptions', []) if sub.get('grouped_in') == bundle_id]


def personal_membership_visible(item: dict[str, Any]) -> bool:
    return not item.get('detached') and (item.get('workspace') or 'personal') == 'personal'


def creator_membership_visible(item: dict[str, Any]) -> bool:
    return not item.get('detached') and (item.get('workspace') or 'personal') == 'creator'


def archive_membership_visible(item: dict[str, Any]) -> bool:
    return not item.get('detached') and (item.get('workspace') or 'personal') == 'archive'


def subscription_related_refs(user: dict[str, Any], sub_id: str) -> dict[str, list[str]]:
    bundle_ids: list[str] = []
    published_slugs: list[str] = []
    target = find_subscription(user, sub_id)
    parent_id = target.get('grouped_in') if target else ''
    if parent_id and find_subscription(user, parent_id):
        bundle_ids.append(parent_id)
    for bundle in user.get('subscriptions', []):
        bundle_id = bundle.get('id')
        if not bundle_id or bundle_id == sub_id or bundle_id in bundle_ids:
            continue
        if bundle.get('kind') == 'bundle' and any(ref.get('id') == sub_id for ref in bundle.get('components', []) or []):
            bundle_ids.append(bundle_id)
    for bundle in user.get('published', []):
        if sub_id in (bundle.get('subscription_ids') or []):
            slug = bundle.get('slug')
            if slug:
                published_slugs.append(slug)
    return {'bundle_ids': bundle_ids, 'published_slugs': published_slugs}


def purge_subscription(store: dict[str, Any], user: dict[str, Any], acct: str, item: dict[str, Any]) -> None:
    item_id = item.get('id', '')
    timeline_id = item.get('owned_timeline_id')
    if item.get('kind') == 'bundle':
        for child in grouped_children(user, item_id):
            child['grouped_in'] = ''
        user['timelines'] = [tl for tl in user.get('timelines', []) if tl.get('target_subscription_id') != item_id]
    for sub in user.get('subscriptions', []):
        if sub.get('grouped_in') == item_id:
            sub['grouped_in'] = ''
    user['subscriptions'] = [sub for sub in user.get('subscriptions', []) if sub.get('id') != item_id]
    if timeline_id:
        user['timelines'] = [tl for tl in user.get('timelines', []) if tl.get('id') != timeline_id]
    for bundle in user.get('subscriptions', []):
        if bundle.get('kind') == 'bundle':
            bundle['components'] = [ref for ref in bundle.get('components', []) if ref.get('id') != item_id]
    for bundle in user.get('published', []):
        bundle['subscription_ids'] = [sub_id for sub_id in bundle.get('subscription_ids', []) if sub_id != item_id]
        bundle['subscription_count'] = len(bundle['subscription_ids'])
    remove_empty_bundles(store, user, acct)


def component_entries(user: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for ref in item.get('components', []) or []:
        child = find_subscription(user, ref.get('id', '')) if ref.get('id') else None
        if child and child.get('grouped_in') == item.get('id'):
            entries.append(child)
            continue
        if ref.get('url'):
            entries.append({
                'id': ref.get('id', ''),
                'title': ref.get('title') or ref.get('url') or 'Subscription',
                'url': ref.get('url') or '',
                'visible': False,
                'trashed': False,
                'color': ref.get('color') or '',
                'author_name': ref.get('author_name') or '',
                'author_acct': ref.get('author_acct') or '',
            })
    return entries


def find_wrapper_timeline(user: dict[str, Any], subscription_id: str) -> dict[str, Any] | None:
    return next((item for item in user.get('timelines', []) if item.get('kind') == 'wrapper' and item.get('target_subscription_id') == subscription_id), None)


def sync_official_publications(store: dict[str, Any]) -> bool:
    user = ensure_user(store, OFFICIAL_ACCT)
    changed = False
    published_store = store.setdefault('published', {})
    rows = [
        item for item in user.get('subscriptions', [])
        if item.get('official') and item.get('kind') != 'bundle' and not item.get('trashed') and not item.get('detached')
    ]
    managed_keys = {f"official:row:{item.get('id', '')}" for item in rows if item.get('id')}
    managed_by_key = {
        str(bundle.get('system_key') or ''): bundle
        for bundle in published_store.values()
        if str(bundle.get('system_key') or '').startswith('official:row:')
    }

    desired_bundles: list[dict[str, Any]] = []
    for item in rows:
        item_id = item.get('id', '')
        if not item_id:
            continue
        system_key = f'official:row:{item_id}'
        bundle = managed_by_key.get(system_key)
        if bundle is None:
            slug_base = slugify(item.get('source_code') or item.get('title') or 'official-source')
            slug = slug_base
            while slug in published_store:
                slug = f'{slug_base}-{secrets.token_hex(2)}'
            bundle = {
                'id': new_id('pub'),
                'slug': slug,
                'created_at': now_iso(),
                'system_key': system_key,
            }
            published_store[slug] = bundle
            changed = True
        desired = {
            'system_key': system_key,
            'title': item.get('title') or item.get('source_code') or 'Official source',
            'owner_acct': OFFICIAL_ACCT,
            'subscription_ids': [item_id],
            'subscription_count': 1,
            'share_url': f"{APP_BASE_URL}/p/{bundle['slug']}",
            'visibility': 'public',
            'invited': [],
            'hashtags': normalize_bundle_hashtags(item.get('hashtags') or ['official']),
            'description': str(item.get('description') or '').strip(),
            'allow_hard_copy': False,
            'archived': False,
            'listed': bool(item.get('visible')),
            'owner_detached': False,
            'official': True,
        }
        for key, value in desired.items():
            if bundle.get(key) != value:
                bundle[key] = value
                changed = True
        desired_bundles.append(bundle)

    stale_slugs = [
        slug for slug, bundle in list(published_store.items())
        if str(bundle.get('system_key') or '') == 'official:bundle' or (
            str(bundle.get('system_key') or '').startswith('official:row:') and str(bundle.get('system_key') or '') not in managed_keys
        )
    ]
    if stale_slugs:
        for slug in stale_slugs:
            published_store.pop(slug, None)
        changed = True

    unmanaged_refs = [
        ref for ref in user.get('published', [])
        if not str(ref.get('system_key') or '') == 'official:bundle'
        and not str(ref.get('system_key') or '').startswith('official:row:')
    ]
    desired_refs = sorted(desired_bundles, key=lambda item: str(item.get('title') or '').lower())
    new_refs = desired_refs + unmanaged_refs
    if user.get('published', []) != new_refs:
        user['published'] = new_refs
        changed = True

    return changed


def ensure_official_content(store: dict[str, Any]) -> bool:
    user = ensure_user(store, OFFICIAL_ACCT)
    changed = False
    desired_user = {
        'display_name': 'TimeGrid Official',
        'bio': 'Admin-managed official calendar sources for TimeGrid.',
        'profile_visibility': 'public',
    }
    for key, value in desired_user.items():
        if user.get(key) != value:
            user[key] = value
            changed = True

    f1 = next((item for item in user.get('subscriptions', []) if item.get('system_key') == 'official:f1' or item.get('url') == OFFICIAL_F1_URL), None)
    if f1 is None:
        f1 = {
            'id': new_id('sub'),
            'created_at': now_iso(),
        }
        user['subscriptions'].insert(0, f1)
        changed = True
    desired_f1 = {
        'system_key': 'official:f1',
        'title': OFFICIAL_F1_TITLE,
        'url': OFFICIAL_F1_URL,
        'visible': True,
        'trashed': False,
        'detached': False,
        'workspace': 'personal',
        'official': True,
        'color': '#a5c96f',
        'author_name': 'TimeGrid Official',
        'author_acct': OFFICIAL_ACCT,
        'source_code': 'F1',
        'source_format': 'ical',
        'hashtags': ['f1', 'official'],
        'description': 'Official Formula 1 subscription source managed by TimeGrid admins.',
    }
    for key, value in desired_f1.items():
        if f1.get(key) != value:
            f1[key] = value
            changed = True
    if f1.get('grouped_in'):
        f1['grouped_in'] = ''
        changed = True

    legacy_bundle_ids = set()
    kept_subscriptions = []
    for item in user.get('subscriptions', []):
        if item.get('system_key') == 'official:container':
            legacy_bundle_ids.add(item.get('id', ''))
            changed = True
            continue
        kept_subscriptions.append(item)
    if len(kept_subscriptions) != len(user.get('subscriptions', [])):
        user['subscriptions'] = kept_subscriptions
    if legacy_bundle_ids:
        for item in user.get('subscriptions', []):
            if item.get('grouped_in') in legacy_bundle_ids:
                item['grouped_in'] = ''
                changed = True

    if sync_official_publications(store):
        changed = True

    if changed:
        user['updated_at'] = now_iso()
    return changed


def serialize_subscription(acct: str, item: dict[str, Any], user: dict[str, Any] | None = None, store: dict[str, Any] | None = None, viewer_session: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(item)
    payload['color'] = item.get('color') or ''
    payload['author_name'] = item.get('author_name') or ''
    payload['author_acct'] = item.get('author_acct') or ''
    payload['can_hard_copy'] = False
    payload['workspace'] = item.get('workspace') or 'personal'
    payload['official'] = bool(item.get('official'))
    payload['source_code'] = str(item.get('source_code') or '').strip()
    payload['source_format'] = str(item.get('source_format') or '').strip().lower()
    payload['hashtags'] = normalize_bundle_hashtags(item.get('hashtags'))
    payload['hashtag_text'] = ' '.join(f"#{tag}" for tag in payload['hashtags'])
    payload['description'] = str(item.get('description') or '')
    refs = subscription_related_refs(user, item.get('id', '')) if user is not None and item.get('id') else {'bundle_ids': [], 'published_slugs': []}
    payload['published_slugs'] = refs.get('published_slugs', [])
    payload['archive_allowed'] = bool(payload['published_slugs'])
    timeline_id = payload.get('owned_timeline_id')
    if timeline_id:
        payload['edit_url'] = timeline_edit_url(acct, timeline_id)
    elif payload.get('kind') == 'bundle':
        payload['editable_shell'] = True
        if user is not None:
            wrapper = find_wrapper_timeline(user, payload.get('id', ''))
            if wrapper:
                payload['edit_url'] = timeline_edit_url(acct, wrapper['id'])
    elif not payload.get('grouped_in'):
        payload['editable_shell'] = True
    if payload.get('kind') == 'bundle':
        payload['is_bundle'] = True
        payload['component_count'] = len(payload.get('components') or [])
        payload['url'] = ''
    slug = local_bundle_slug(payload.get('url') or '')
    if slug and store is not None:
        bundle = store.get('published', {}).get(slug)
        if not bundle or not bundle_visible_to_session(bundle, viewer_session):
            payload['unavailable'] = True
            payload['availability_note'] = 'Subscription source currently not available.' if payload.get('editable_shell') else 'Not available.'
        payload['allow_hard_copy'] = False
    return payload


def serialize_timeline(acct: str, item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    payload['ics_url'] = timeline_ics_url(acct, item['id'])
    payload['edit_url'] = timeline_edit_url(acct, item['id'])
    payload['color'] = item.get('color') or ''
    return payload


def serialize_bundle(bundle: dict[str, Any], store: dict[str, Any] | None = None, viewer_session: dict[str, Any] | None = None, viewer_user: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(bundle)
    payload['official'] = bool(bundle.get('official'))
    payload['visibility'] = bundle_visibility(bundle)
    payload['invited'] = bundle_invited_tokens(bundle)
    payload['hashtags'] = normalize_bundle_hashtags(bundle.get('hashtags'))
    payload['hashtag_text'] = ' '.join(f'#{tag}' for tag in payload['hashtags'])
    payload['allow_hard_copy'] = False
    payload['archived'] = bundle_archived(bundle)
    payload['listed'] = bundle_listed(bundle)
    payload['owner_detached'] = bundle_owner_detached(bundle)
    if payload['owner_detached']:
        payload['publish_state'] = 'removed_permanently'
    elif payload['archived']:
        payload['publish_state'] = 'archived'
    elif not payload['listed']:
        payload['publish_state'] = 'removed_from_publishing'
    else:
        payload['publish_state'] = 'active'
    payload['subscribe_url'] = bundle_subscribe_url(bundle['slug'])
    payload['feed_url'] = published_bundle_runtime_url(store, bundle['slug']) if store is not None else bundle_feed_url(bundle['slug'])
    payload['subscribed'] = user_has_bundle_subscription(viewer_user, bundle.get('slug', ''))
    payload['available'] = bundle_visible_to_session(bundle, viewer_session)
    contributors = bundle_contributors(store, bundle, viewer_session) if store is not None else []
    payload['contributors'] = contributors
    payload['contributor_text'] = ', '.join(f"{item['name']} ({item['count']})" if item.get('count', 0) != 1 else item['name'] for item in contributors)
    return payload


def normalize_invite_token(value: str) -> str:
    token = str(value or '').strip().lower()
    if token.startswith('@'):
        token = token[1:]
    return token


def normalize_bundle_hashtags(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else str(value or '').replace(',', ' ').split()
    out: list[str] = []
    for item in raw_items:
        token = str(item or '').strip().lower()
        if not token:
            continue
        if token.startswith('#'):
            token = token[1:]
        token = ''.join(ch for ch in token if ch.isalnum() or ch in {'_', '-'})
        if not token or token in out:
            continue
        out.append(token)
        if len(out) >= 20:
            break
    return out


def invite_variants(value: str) -> set[str]:
    token = normalize_invite_token(value)
    if not token:
        return set()
    variants = {token}
    if '@' in token:
        variants.add(token.split('@', 1)[0])
    return {item for item in variants if item}


def bundle_visibility(bundle: dict[str, Any]) -> str:
    visibility = str(bundle.get('visibility') or 'public').strip().lower()
    return visibility if visibility in {'public', 'invited', 'private'} else 'public'


def bundle_invited_tokens(bundle: dict[str, Any]) -> list[str]:
    raw = bundle.get('invited') or []
    if isinstance(raw, str):
        raw = raw.split(',')
    out: list[str] = []
    for item in raw:
        token = normalize_invite_token(str(item))
        if token and token not in out:
            out.append(token)
    return out


def bundle_archived(bundle: dict[str, Any]) -> bool:
    return bool(bundle.get('archived'))


def bundle_listed(bundle: dict[str, Any]) -> bool:
    return bool(bundle.get('listed', True))


def bundle_owner_detached(bundle: dict[str, Any]) -> bool:
    return bool(bundle.get('owner_detached'))


def bundle_discoverable(bundle: dict[str, Any]) -> bool:
    return not bundle_archived(bundle) and not bundle_owner_detached(bundle) and bundle_listed(bundle)


def bundle_visible_to_session(bundle: dict[str, Any], session: dict[str, Any] | None) -> bool:
    visibility = bundle_visibility(bundle)
    if visibility == 'public':
        return True
    if session is None:
        return False
    viewer_acct = str(session.get('acct') or '')
    if viewer_acct and viewer_acct == bundle.get('owner_acct'):
        return True
    if visibility == 'private':
        return False
    viewer_tokens = invite_variants(viewer_acct) | invite_variants(str(session.get('display_name') or ''))
    invited_tokens: set[str] = set()
    for token in bundle_invited_tokens(bundle):
        invited_tokens |= invite_variants(token)
    return bool(viewer_tokens & invited_tokens)


def user_has_bundle_subscription(user: dict[str, Any] | None, slug: str) -> bool:
    if not user:
        return False
    for item in user.get('subscriptions', []):
        if item.get('trashed'):
            continue
        if local_bundle_slug(item.get('url') or '') == slug:
            return True
    return False


def serialize_notification(item: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': item.get('id') or '',
        'kind': item.get('kind') or 'notice',
        'title': item.get('title') or '',
        'body': item.get('body') or '',
        'href': item.get('href') or '',
        'actor_acct': item.get('actor_acct') or '',
        'created_at': item.get('created_at') or now_iso(),
        'read_at': item.get('read_at') or '',
    }


def unread_notification_count(user: dict[str, Any]) -> int:
    return sum(1 for item in user.get('notifications', []) if not item.get('read_at'))


def serialize_auth_provider(provider: dict[str, Any], next_path: str = '/') -> dict[str, Any]:
    return {
        'id': provider['id'],
        'label': provider['label'],
        'description': provider['description'],
        'status': provider['status'],
        'provisions_mastodon': bool(provider.get('provisions_mastodon')),
        'login_href': (
            ''
            if provider.get('native_email_auth') or provider.get('status') != 'active'
            else (
                f'/auth/login?next={urllib.parse.quote(next_path, safe="/?=&")}'
                if provider['id'] == 'mastodon'
                else f'/auth/provider/{provider["id"]}/login?next={urllib.parse.quote(next_path, safe="/?=&")}'
            )
        ),
        'native_email_auth': bool(provider.get('native_email_auth')),
        'supabase_provider': bool(provider.get('supabase_provider')),
    }


def admin_notification_targets(store: dict[str, Any]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for acct in ADMIN_ACCOUNTS:
        if not acct:
            continue
        targets.append(ensure_user(store, acct))
    return targets


def create_signup_intent(store: dict[str, Any], *, provider: str, email: str, display_name: str, note: str, next_path: str) -> dict[str, Any]:
    record = {
        'id': new_id('signup'),
        'provider': provider,
        'email': email,
        'display_name': display_name,
        'note': note,
        'next': next_path,
        'status': 'pending',
        'create_linked_mastodon': True,
        'created_at': now_iso(),
    }
    store.setdefault('signup_intents', []).insert(0, record)
    for admin_user in admin_notification_targets(store):
        add_notification(
            admin_user,
            kind='signup_intent',
            title='New signup request',
            body=f'{display_name or email} requested {provider.title()} signup with linked Mastodon provisioning.',
            actor_acct='timegrid',
            href='/auth',
        )
    return record


def add_notification(user: dict[str, Any], *, kind: str, title: str, body: str = '', href: str = '', actor_acct: str = '') -> None:
    notifications = user.setdefault('notifications', [])
    notifications.insert(0, {
        'id': new_id('ntf'),
        'kind': kind,
        'title': title,
        'body': body,
        'href': href,
        'actor_acct': actor_acct,
        'created_at': now_iso(),
        'read_at': '',
    })
    del notifications[60:]


def user_profile_visibility(user: dict[str, Any]) -> str:
    visibility = str(user.get('profile_visibility') or 'public').strip().lower()
    return visibility if visibility in {'public', 'private'} else 'public'


def can_view_profile(viewer_user: dict[str, Any] | None, target_user: dict[str, Any]) -> bool:
    target_acct = str(target_user.get('acct') or '').strip().lower()
    if not target_acct:
        return False
    if viewer_user and str(viewer_user.get('acct') or '').strip().lower() == target_acct:
        return True
    if user_profile_visibility(target_user) == 'private':
        return False
    viewer_acct = str(viewer_user.get('acct') or '').strip().lower() if viewer_user else ''
    if viewer_acct and viewer_acct in {str(x).strip().lower() for x in target_user.get('blocked_accounts') or []}:
        return False
    if viewer_user and target_acct in {str(x).strip().lower() for x in viewer_user.get('blocked_accounts') or []}:
        return False
    return True


def serialize_public_profile(target_user: dict[str, Any], store: dict[str, Any], viewer_session: dict[str, Any] | None = None, viewer_user: dict[str, Any] | None = None) -> dict[str, Any]:
    acct = str(target_user.get('acct') or '').strip()
    visible_bundles: list[dict[str, Any]] = []
    for item in target_user.get('published', []):
        bundle = store.get('published', {}).get(item.get('slug')) or item
        if not bundle_discoverable(bundle):
            continue
        if not bundle_visible_to_session(bundle, viewer_session):
            continue
        visible_bundles.append(serialize_bundle(bundle, store, viewer_session, viewer_user))
    return {
        'acct': acct,
        'display_name': target_user.get('display_name') or acct,
        'avatar': target_user.get('avatar') or '',
        'bio': target_user.get('bio') or '',
        'profile_visibility': user_profile_visibility(target_user),
        'published': visible_bundles,
        'published_count': len(visible_bundles),
    }


def invited_users_for_bundle(store: dict[str, Any], bundle: dict[str, Any]) -> list[dict[str, Any]]:
    invited: list[dict[str, Any]] = []
    invited_tokens: set[str] = set()
    for token in bundle_invited_tokens(bundle):
        invited_tokens |= invite_variants(token)
    if not invited_tokens:
        return invited
    owner = str(bundle.get('owner_acct') or '').strip().lower()
    for user in store.get('users', {}).values():
        acct = str(user.get('acct') or '').strip().lower()
        if not acct or acct == owner:
            continue
        user_tokens = invite_variants(acct) | invite_variants(str(user.get('display_name') or ''))
        if user_tokens & invited_tokens:
            invited.append(user)
    return invited


def notify_bundle_invites(store: dict[str, Any], bundle: dict[str, Any], actor_acct: str) -> None:
    title = bundle.get('title') or 'Published calendar'
    href = f'/p/{bundle.get("slug")}'
    for user in invited_users_for_bundle(store, bundle):
        existing = next((item for item in user.get('notifications', []) if item.get('kind') == 'bundle_invite' and item.get('href') == href and not item.get('read_at')), None)
        if existing:
            continue
        add_notification(
            user,
            kind='bundle_invite',
            title=f'You were invited to {title}',
            body=f'@{actor_acct} invited you to view this published calendar.',
            href=href,
            actor_acct=actor_acct,
        )


def local_bundle_slug(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return ''
    if not parsed.path.startswith('/bundle/') or not parsed.path.endswith('.ics'):
        return ''
    app_host = urllib.parse.urlparse(APP_BASE_URL).netloc
    if parsed.netloc and parsed.netloc != app_host:
        return ''
    return urllib.parse.unquote(parsed.path[len('/bundle/'): -4])


def published_bundle_version(store: dict[str, Any], bundle: dict[str, Any]) -> str:
    user = ensure_user(store, bundle['owner_acct'])
    parts = [bundle.get('created_at') or '', bundle.get('title') or '', bundle.get('slug') or '']
    for sub_id in bundle.get('subscription_ids', []):
        item = find_subscription(user, sub_id)
        if not item or item.get('trashed'):
            continue
        for leaf in leaf_subscriptions(user, item):
            if not official_leaf_included(bundle, leaf):
                continue
            parts.append(leaf.get('id') or '')
            parts.append(leaf.get('url') or '')
            timeline_id = leaf.get('owned_timeline_id')
            if timeline_id:
                timeline = find_timeline(user, timeline_id)
                if timeline:
                    parts.append(timeline.get('updated_at') or timeline.get('created_at') or '')
            else:
                parts.append(leaf.get('created_at') or '')
    return hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()[:16]


def published_bundle_runtime_url(store: dict[str, Any], slug: str) -> str:
    bundle = store.get('published', {}).get(slug)
    base = bundle_feed_url(slug)
    if not bundle:
        return base
    return f'{base}?v={published_bundle_version(store, bundle)}'


def timeline_runtime_url(acct: str, timeline: dict[str, Any]) -> str:
    base = timeline_ics_url(acct, timeline['id'])
    stamp = urllib.parse.quote(str(timeline.get('updated_at') or timeline.get('created_at') or ''), safe='')
    return f'{base}?v={stamp}' if stamp else base


def subscription_runtime_url(acct: str, user: dict[str, Any], item: dict[str, Any], store: dict[str, Any] | None = None) -> str:
    timeline_id = item.get('owned_timeline_id')
    if timeline_id:
        timeline = find_timeline(user, timeline_id)
        if timeline:
            return timeline_runtime_url(acct, timeline)
    raw_url = item.get('url') or ''
    slug = local_bundle_slug(raw_url)
    if slug and store is not None:
        return published_bundle_runtime_url(store, slug)
    return raw_url


def create_timeline_record(title: str, description: str = '', *, kind: str = '', color: str = '') -> dict[str, Any]:
    item = {
        'id': new_id('tl'),
        'title': title or 'Untitled timeline',
        'description': description,
        'events': [],
        'created_at': now_iso(),
        'updated_at': now_iso(),
        'color': color or random_timeline_color(),
    }
    if kind:
        item['kind'] = kind
    return item


def sync_timeline_subscription(acct: str, user: dict[str, Any], timeline: dict[str, Any]) -> dict[str, Any] | None:
    if timeline.get('kind') == 'wrapper':
        return None
    subscription_id = timeline.get('subscription_id')
    sub = find_subscription(user, subscription_id) if subscription_id else None
    if sub is None:
        sub = {
            'id': new_id('sub'),
            'title': timeline.get('title') or 'Untitled timeline',
            'url': timeline_ics_url(acct, timeline['id']),
            'visible': True,
            'trashed': False,
            'created_at': timeline.get('created_at') or now_iso(),
            'owned_timeline_id': timeline['id'],
            'calendar_id': timeline.get('calendar_id') or default_calendar_for(user, 'personal'),
            'color': timeline.get('color') or random_timeline_color(),
            'author_name': user.get('display_name') or acct,
            'author_acct': acct,
            'workspace': timeline.get('workspace') or 'personal',
        }
        user['subscriptions'].insert(0, sub)
        timeline['subscription_id'] = sub['id']
    else:
        sub['title'] = timeline.get('title') or sub.get('title') or 'Untitled timeline'
        sub['url'] = timeline_ics_url(acct, timeline['id'])
        sub['owned_timeline_id'] = timeline['id']
        sub['calendar_id'] = timeline.get('calendar_id') or sub.get('calendar_id') or default_calendar_for(user, calendar_workspace(sub))
        sub['color'] = timeline.get('color') or sub.get('color') or random_timeline_color()
        sub['author_name'] = sub.get('author_name') or user.get('display_name') or acct
        sub['author_acct'] = sub.get('author_acct') or acct
    return sub


def ensure_bundle_component(bundle: dict[str, Any], item: dict[str, Any]) -> None:
    bundle.setdefault('components', [])
    snapshot = component_snapshot(item)
    for index, ref in enumerate(bundle['components']):
        if ref.get('id') == item.get('id'):
            bundle['components'][index] = snapshot
            return
    bundle['components'].append(snapshot)


def sync_group_parent_component(user: dict[str, Any], item: dict[str, Any]) -> None:
    parent_id = str(item.get('grouped_in') or '').strip()
    if not parent_id:
        return
    parent = find_subscription(user, parent_id)
    if not parent or parent.get('kind') != 'bundle':
        return
    parent.setdefault('components', [])
    if item.get('trashed') or item.get('detached'):
        parent['components'] = [ref for ref in parent.get('components', []) if ref.get('id') != item.get('id')]
        return
    ensure_bundle_component(parent, item)


def official_leaf_included(bundle: dict[str, Any], leaf: dict[str, Any]) -> bool:
    if not bundle.get('official'):
        return True
    return bool(leaf.get('visible', True)) and not leaf.get('trashed') and not leaf.get('detached')


def leaf_subscriptions(user: dict[str, Any], item: dict[str, Any], seen: set[str] | None = None) -> list[dict[str, Any]]:
    if seen is None:
        seen = set()
    item_id = item.get('id')
    if not item_id or item_id in seen:
        return []
    seen.add(item_id)
    if item.get('kind') != 'bundle':
        return [item]
    out: list[dict[str, Any]] = []
    children = grouped_children(user, item_id)
    if children:
        for child in children:
            out.extend(leaf_subscriptions(user, child, seen.copy()))
        return out
    for ref in item.get('components', []) or []:
        child = find_subscription(user, ref.get('id', '')) if ref.get('id') else None
        if child:
            out.extend(leaf_subscriptions(user, child, seen.copy()))
        elif ref.get('url'):
            out.append(dict(ref))
    return out


def strip_editor_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': event.get('id') or new_id('evt'),
        'title': event.get('title') or 'Untitled event',
        'start': event.get('start') or '',
        'end': event.get('end') or '',
        'description': event.get('description') or '',
        'location': event.get('location') or '',
        'url': event.get('url') or '',
        'recurrence': event.get('recurrence') or None,
        'exdates': list(event.get('exdates') or []),
        'overrides': list(event.get('overrides') or []),
    }


def editor_event_payload(event: dict[str, Any], *, source_timeline_id: str, source_subscription_id: str, source_title: str, editable: bool, source_color: str = '') -> dict[str, Any]:
    payload = strip_editor_event(event)
    payload['source_timeline_id'] = source_timeline_id
    payload['source_subscription_id'] = source_subscription_id
    payload['source_title'] = source_title
    payload['editable'] = editable
    payload['source_color'] = source_color
    return payload


def ensure_bundle_overlay_timeline(acct: str, user: dict[str, Any], bundle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    for sub in grouped_children(user, bundle.get('id', '')):
        if sub.get('bundle_overlay_for') == bundle.get('id') and sub.get('owned_timeline_id'):
            timeline = find_timeline(user, sub['owned_timeline_id'])
            if timeline:
                ensure_bundle_component(bundle, sub)
                return timeline, sub
    title = f"{bundle.get('title') or 'Merged timeline'} additions"
    timeline = create_timeline_record(title, f"Extra events inside {bundle.get('title') or 'merged timeline'}")
    user['timelines'].insert(0, timeline)
    sub = sync_timeline_subscription(acct, user, timeline)
    assert sub is not None
    sub['title'] = title
    sub['grouped_in'] = bundle['id']
    sub['bundle_overlay_for'] = bundle['id']
    sub['visible'] = bundle.get('visible', True)
    sub['trashed'] = False
    ensure_bundle_component(bundle, sub)
    timeline['updated_at'] = now_iso()
    return timeline, sub


def ensure_subscription_editor(acct: str, user: dict[str, Any], subscription: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if subscription.get('owned_timeline_id') and subscription.get('kind') != 'bundle':
        timeline = find_timeline(user, subscription['owned_timeline_id'])
        if not timeline:
            raise KeyError('timeline_not_found')
        return timeline, subscription

    bundle = subscription
    if subscription.get('kind') != 'bundle':
        bundle = next((item for item in user.get('subscriptions', []) if item.get('kind') == 'bundle' and item.get('shell_source_id') == subscription.get('id') and not item.get('trashed')), None)
        if bundle is None:
            bundle = {
                'id': new_id('sub'),
                'title': subscription.get('title') or 'Editable shell',
                'url': '',
                'visible': subscription.get('visible', True),
                'trashed': False,
                'created_at': now_iso(),
                'kind': 'bundle',
                'components': [component_snapshot(subscription)],
                'shell_source_id': subscription.get('id'),
                'color': subscription.get('color') or random_timeline_color(),
            }
            user['subscriptions'].insert(0, bundle)
        subscription['grouped_in'] = bundle['id']
        subscription['trashed'] = False
        ensure_bundle_component(bundle, subscription)

    overlay_timeline, overlay_sub = ensure_bundle_overlay_timeline(acct, user, bundle)
    wrapper = find_wrapper_timeline(user, bundle['id'])
    if wrapper is None:
        wrapper = create_timeline_record(bundle.get('title') or 'Merged timeline editor', '', kind='wrapper')
        wrapper['target_subscription_id'] = bundle['id']
        wrapper['overlay_timeline_id'] = overlay_timeline['id']
        wrapper['overlay_subscription_id'] = overlay_sub['id']
        user['timelines'].insert(0, wrapper)
    else:
        wrapper['overlay_timeline_id'] = overlay_timeline['id']
        wrapper['overlay_subscription_id'] = overlay_sub['id']
    wrapper['title'] = bundle.get('title') or wrapper.get('title') or 'Merged timeline editor'
    wrapper['updated_at'] = now_iso()
    return wrapper, bundle


def build_wrapper_timeline(acct: str, user: dict[str, Any], wrapper: dict[str, Any]) -> dict[str, Any] | None:
    target = find_subscription(user, wrapper.get('target_subscription_id', ''))
    if not target or target.get('trashed'):
        return None
    overlay_timeline, overlay_sub = ensure_bundle_overlay_timeline(acct, user, target)
    wrapper['overlay_timeline_id'] = overlay_timeline['id']
    wrapper['overlay_subscription_id'] = overlay_sub['id']
    events: list[dict[str, Any]] = []
    external_sources: list[dict[str, Any]] = []
    for sub in leaf_subscriptions(user, target):
        if sub.get('owned_timeline_id'):
            timeline = find_timeline(user, sub['owned_timeline_id'])
            if not timeline or timeline.get('kind') == 'wrapper':
                continue
            for event in timeline.get('events', []):
                events.append(editor_event_payload(event, source_timeline_id=timeline['id'], source_subscription_id=sub['id'], source_title=sub.get('title') or timeline.get('title') or 'Timeline', editable=True, source_color=sub.get('color') or timeline.get('color') or ''))
        elif sub.get('url'):
            external_sources.append({
                'subscription_id': sub['id'],
                'title': sub.get('title') or sub.get('url') or 'Subscription',
                'url': sub.get('url') or '',
                'editable': False,
                'fetch_url': f'/api/personal/{urllib.parse.quote(acct)}/subscriptions/{urllib.parse.quote(sub["id"] )}/source',
                'color': sub.get('color') or '',
            })
    payload = dict(wrapper)
    payload['title'] = target.get('title') or wrapper.get('title') or 'Merged timeline editor'
    payload['description'] = wrapper.get('description') or target.get('description') or ''
    payload['kind'] = 'wrapper'
    payload['events'] = events
    payload['external_sources'] = external_sources
    payload['overlay_timeline_id'] = overlay_timeline['id']
    payload['overlay_subscription_id'] = overlay_sub['id']
    payload['overlay_color'] = overlay_sub.get('color') or overlay_timeline.get('color') or ''
    payload['color'] = target.get('color') or overlay_sub.get('color') or wrapper.get('color') or ''
    payload['ics_url'] = personal_bundle_feed_url(acct, target['id'])
    payload['edit_url'] = timeline_edit_url(acct, wrapper['id'])
    return payload


def remove_empty_bundles(store: dict[str, Any], user: dict[str, Any], acct: str) -> None:
    kept: list[dict[str, Any]] = []
    for bundle in user.get('published', []):
        bundle['subscription_ids'] = [sub_id for sub_id in bundle.get('subscription_ids', []) if find_subscription(user, sub_id)]
        bundle['subscription_count'] = len(bundle['subscription_ids'])
        if bundle['subscription_count'] > 0:
            kept.append(bundle)
            if bundle.get('slug') in store['published']:
                store['published'][bundle['slug']]['subscription_ids'] = bundle['subscription_ids']
                store['published'][bundle['slug']]['subscription_count'] = bundle['subscription_count']
        else:
            slug = bundle.get('slug')
            if slug:
                store['published'].pop(slug, None)
    user['published'] = kept


def component_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': item.get('id', ''),
        'title': item.get('title') or item.get('url') or 'Subscription',
        'url': item.get('url') or '',
        'color': item.get('color') or '',
        'author_name': item.get('author_name') or '',
        'author_acct': item.get('author_acct') or '',
        'official': bool(item.get('official')),
        'source_code': str(item.get('source_code') or '').strip(),
        'source_format': str(item.get('source_format') or '').strip().lower(),
        'hashtags': normalize_bundle_hashtags(item.get('hashtags')),
        'description': str(item.get('description') or ''),
        'visible': bool(item.get('visible', True)),
    }


def subscription_leaf_snapshots(user: dict[str, Any], item: dict[str, Any], seen: set[str] | None = None) -> list[dict[str, Any]]:
    if seen is None:
        seen = set()
    item_key = component_identity(item)
    if item_key in seen or item.get('trashed'):
        return []
    seen.add(item_key)
    if item.get('kind') != 'bundle':
        return [component_snapshot(item)]
    out: list[dict[str, Any]] = []
    children = grouped_children(user, item.get('id', ''))
    if children:
        for child in children:
            out.extend(subscription_leaf_snapshots(user, child, seen.copy()))
        return out
    for ref in item.get('components', []) or []:
        child = find_subscription(user, ref.get('id', '')) if ref.get('id') else None
        if child:
            out.extend(subscription_leaf_snapshots(user, child, seen.copy()))
        elif ref.get('url'):
            out.append(component_snapshot(ref))
    return out


def materialize_component_subscription(user: dict[str, Any], ref: dict[str, Any], *, visible: bool = True) -> dict[str, Any]:
    item = {
        'id': new_id('sub'),
        'title': ref.get('title') or ref.get('url') or 'Subscription',
        'url': ref.get('url') or '',
        'visible': visible,
        'trashed': False,
        'created_at': now_iso(),
        'color': ref.get('color') or random_timeline_color(),
        'author_name': ref.get('author_name') or user.get('display_name') or user.get('acct') or 'Unknown author',
        'author_acct': str(ref.get('author_acct') or user.get('acct') or '').strip().lower(),
    }
    user['subscriptions'].insert(0, item)
    return item


def bundle_component_snapshots(store: dict[str, Any], bundle: dict[str, Any], viewer_session: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if viewer_session is not None and not bundle_visible_to_session(bundle, viewer_session):
        return []
    user = ensure_user(store, bundle['owner_acct'])
    refs: list[dict[str, Any]] = []
    for sub_id in bundle.get('subscription_ids', []):
        item = find_subscription(user, sub_id)
        if item:
            refs.extend([ref for ref in subscription_leaf_snapshots(user, item) if official_leaf_included(bundle, ref)])
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        key = component_identity(ref)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def bundle_contributors(store: dict[str, Any], bundle: dict[str, Any], viewer_session: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], dict[str, Any]] = {}
    for ref in bundle_component_snapshots(store, bundle, viewer_session):
        acct = str(ref.get('author_acct') or '').strip().lower()
        name = str(ref.get('author_name') or acct or 'Unknown author').strip()
        key = (acct, name.lower())
        row = counts.setdefault(key, {
            'acct': acct,
            'name': name,
            'count': 0,
        })
        row['count'] += 1
    return sorted(counts.values(), key=lambda item: (-int(item.get('count') or 0), str(item.get('name') or '').lower(), str(item.get('acct') or '').lower()))


def build_workspace_payload(acct: str, user: dict[str, Any], store: dict[str, Any], session: dict[str, Any], *, mode: str = 'personal', is_admin: bool = False, calendar_id: str = '') -> dict[str, Any]:
    active = []
    trash = []
    visible_urls: list[str] = []
    workspace_for_calendars = 'creator' if mode == 'creator' else 'personal'
    calendars = [item for item in ensure_user_calendars(user) if item.get('workspace') == workspace_for_calendars and not item.get('archived')]
    valid_calendar_ids = {str(item.get('id') or '') for item in calendars}
    active_calendar_id = calendar_id if calendar_id in valid_calendar_ids else default_calendar_for(user, workspace_for_calendars)
    membership_check = personal_membership_visible
    if mode == 'creator':
        membership_check = creator_membership_visible
    elif mode == 'archive':
        membership_check = archive_membership_visible

    for item in user.get('subscriptions', []):
        if not membership_check(item):
            continue
        if mode != 'archive' and not calendar_visible(item, active_calendar_id):
            continue
        if item.get('trashed'):
            trash.append(serialize_subscription(acct, item, user, store, session))
            continue
        if item.get('grouped_in'):
            parent = find_subscription(user, item.get('grouped_in', ''))
            if parent and (parent.get('trashed') or not membership_check(parent)):
                continue
            runtime_url = subscription_runtime_url(acct, user, item, store)
            if item.get('visible') and runtime_url:
                visible_urls.append(runtime_url)
            continue
        payload = serialize_subscription(acct, item, user, store, session)
        if item.get('kind') == 'bundle':
            payload['components'] = [serialize_subscription(acct, child, user, store, session) for child in component_entries(user, item)]
            payload['component_count'] = len(payload['components'])
        active.append(payload)
        if item.get('kind') == 'bundle':
            if item.get('visible'):
                visible_urls.extend(resolve_subscription_urls(user, item, store=store))
        elif item.get('visible'):
            runtime_url = subscription_runtime_url(acct, user, item, store)
            if runtime_url:
                visible_urls.append(runtime_url)

    active.sort(key=lambda entry: (int(entry.get('position') or 0), str(entry.get('title') or '').lower()))
    trash.sort(key=lambda entry: (int(entry.get('position') or 0), str(entry.get('title') or '').lower()))

    deduped_visible_urls: list[str] = []
    for url in visible_urls:
        if url and url not in deduped_visible_urls:
            deduped_visible_urls.append(url)

    visible_sources = []
    seen_sources: set[str] = set()

    def add_visible_source(source_item: dict[str, Any], color_override: str = '') -> None:
        source_id = source_item.get('id')
        source_url = source_item.get('url') or ''
        slug = local_bundle_slug(source_url)
        if slug:
            bundle = store.get('published', {}).get(slug)
            if not bundle or not bundle_visible_to_session(bundle, session):
                return
        runtime_url = subscription_runtime_url(acct, user, source_item, store)
        fetch_url = f'/api/personal/{urllib.parse.quote(acct)}/subscriptions/{urllib.parse.quote(source_id)}/source' if source_id else runtime_url
        key = fetch_url or runtime_url or source_url or ''
        if not key or key in seen_sources:
            return
        seen_sources.add(key)
        visible_sources.append({
            'id': source_id or '',
            'title': source_item.get('title') or source_item.get('url') or 'Calendar source',
            'fetch_url': fetch_url or '',
            'url': runtime_url or source_item.get('url') or '',
            'color': color_override or source_item.get('color') or '',
        })

    for item in user.get('subscriptions', []):
        if not membership_check(item) or item.get('trashed') or not item.get('visible'):
            continue
        if mode != 'archive' and not calendar_visible(item, active_calendar_id):
            continue
        if item.get('grouped_in'):
            continue
        if item.get('kind') == 'bundle':
            bundle_color = item.get('color') or ''
            for child in leaf_subscriptions(user, item):
                if child.get('trashed'):
                    continue
                add_visible_source(child, bundle_color)
        else:
            add_visible_source(item)

    publish_candidates: list[dict[str, Any]] = []
    active_published: list[dict[str, Any]] = []
    archived_published: list[dict[str, Any]] = []
    if mode == 'creator':
        for item in user.get('subscriptions', []):
            if item.get('trashed') or item.get('grouped_in') or item.get('detached'):
                continue
            if not calendar_visible(item, active_calendar_id):
                continue
            payload = serialize_subscription(acct, item, user, store, session)
            if item.get('kind') == 'bundle':
                payload['components'] = [serialize_subscription(acct, child, user, store, session) for child in component_entries(user, item)]
                payload['component_count'] = len(payload['components'])
            publish_candidates.append(payload)
        workspace_order = {'creator': 0, 'personal': 1, 'archive': 2}
        publish_candidates.sort(key=lambda entry: (
            workspace_order.get(entry.get('workspace') or 'personal', 9),
            str(entry.get('title') or '').lower(),
        ))
        for item in user.get('published', []):
            bundle = store.get('published', {}).get(item.get('slug')) or item
            if bundle.get('calendar_id') and bundle.get('calendar_id') != active_calendar_id:
                continue
            if bundle_owner_detached(bundle):
                continue
            if bundle_archived(bundle):
                archived_published.append(serialize_bundle(bundle, store, session, user))
            else:
                active_published.append(serialize_bundle(bundle, store, session, user))
    elif mode == 'archive':
        for item in user.get('published', []):
            bundle = store.get('published', {}).get(item.get('slug')) or item
            if bundle_owner_detached(bundle) or not bundle_archived(bundle):
                continue
            archived_published.append(serialize_bundle(bundle, store, session, user))

    official_registry_rows = []
    if mode == 'creator' and acct == OFFICIAL_ACCT and is_admin:
        official_registry_rows = [serialize_subscription(acct, item, user, store, session) for item in user.get('subscriptions', []) if item.get('official') and item.get('kind') != 'bundle' and not item.get('trashed') and not item.get('detached')]

    return {
        'user': {
            'acct': acct,
            'display_name': user.get('display_name') or acct,
            'avatar': user.get('avatar') or '',
            'is_owner': acct == session['acct'],
            'is_admin': is_admin,
            'bio': user.get('bio') or '',
            'profile_visibility': user_profile_visibility(user),
            'notifications_unread': unread_notification_count(user),
        },
        'workspace': mode,
        'calendars': calendars,
        'active_calendar_id': active_calendar_id,
        'subscriptions': active,
        'trash': trash,
        'published': active_published if mode == 'creator' else [],
        'archived_published': archived_published,
        'publish_candidates': publish_candidates,
        'timelines': [
            serialize_timeline(acct, item)
            for item in user.get('timelines', [])
            if item.get('kind') != 'wrapper'
            and membership_check(find_subscription(user, item.get('subscription_id', '')) or {})
            and (mode == 'archive' or calendar_visible(item, active_calendar_id))
        ],
        'embed_url': build_embed_url(deduped_visible_urls),
        'visible_sources': visible_sources,
        'official_registry_rows': official_registry_rows,
    }


def resolve_subscription_urls(user: dict[str, Any], item: dict[str, Any], seen: set[str] | None = None, store: dict[str, Any] | None = None) -> list[str]:
    if seen is None:
        seen = set()
    item_id = item.get('id')
    if item_id and item_id in seen:
        return []
    if item_id:
        seen.add(item_id)
    if item.get('kind') == 'bundle':
        urls: list[str] = []
        for child in grouped_children(user, item_id):
            urls.extend(resolve_subscription_urls(user, child, seen.copy(), store))
        if not urls:
            for ref in item.get('components', []) or []:
                if ref.get('url'):
                    urls.append(ref['url'])
        deduped: list[str] = []
        for url in urls:
            if url and url not in deduped:
                deduped.append(url)
        return deduped
    runtime_url = subscription_runtime_url(user.get('acct', ''), user, item, store) if user.get('acct') else (item.get('url') or '')
    return [runtime_url] if runtime_url else []


def bundle_urls(store: dict[str, Any], bundle: dict[str, Any], viewer_session: dict[str, Any] | None = None) -> list[str]:
    if viewer_session is not None and not bundle_visible_to_session(bundle, viewer_session):
        return []
    user = ensure_user(store, bundle['owner_acct'])
    urls: list[str] = []
    for sub_id in bundle.get('subscription_ids', []):
        item = find_subscription(user, sub_id)
        if item:
            urls.extend(resolve_subscription_urls(user, item, store=store))
    deduped: list[str] = []
    for url in urls:
        if url and url not in deduped:
            deduped.append(url)
    return deduped


def escape_ics_text(value: str) -> str:
    return str(value).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def iso_to_ics(value: str) -> str:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def recurrence_to_rrule(event: dict[str, Any]) -> str | None:
    recurrence = event.get('recurrence') or {}
    freq = recurrence.get('freq')
    if not freq:
        return None
    parts = [f'FREQ={str(freq).upper()}']
    interval = int(recurrence.get('interval') or 1)
    if interval > 1:
        parts.append(f'INTERVAL={interval}')
    until = recurrence.get('until')
    if until:
        parts.append(f'UNTIL={iso_to_ics(until)}')
    byweekday = recurrence.get('byweekday') or []
    if byweekday:
        parts.append('BYDAY=' + ','.join(str(day).upper() for day in byweekday))
    count = recurrence.get('count')
    if count:
        parts.append(f'COUNT={int(count)}')
    return ';'.join(parts)


def extract_calendar_blocks(raw_text: str) -> tuple[list[list[str]], list[list[str]]]:
    lines = str(raw_text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    timezones: list[list[str]] = []
    events: list[list[str]] = []
    stack: list[str] = []
    buffer: list[str] | None = None
    kind = ''
    for line in lines:
        upper = line.upper()
        if upper.startswith('BEGIN:'):
            name = upper.split(':', 1)[1]
            stack.append(name)
            if name in {'VEVENT', 'VTIMEZONE'} and buffer is None:
                kind = name
                buffer = [line]
                continue
        if buffer is not None:
            buffer.append(line)
            if upper == f'END:{kind}':
                if kind == 'VEVENT':
                    events.append(buffer)
                elif kind == 'VTIMEZONE':
                    timezones.append(buffer)
                buffer = None
                kind = ''
            continue
        if upper.startswith('END:') and stack:
            stack.pop()
    return timezones, events


def local_calendar_bytes(url: str, store: dict[str, Any], session: dict[str, Any] | None = None) -> bytes | None:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return None
    app_host = urllib.parse.urlparse(APP_BASE_URL).netloc
    if parsed.netloc and parsed.netloc != app_host:
        return None
    path = parsed.path or ''
    if path.startswith('/ics/') and path.endswith('.ics'):
        rest = path[len('/ics/'): -4]
        if '/' not in rest:
            return None
        acct, timeline_id = rest.split('/', 1)
        user = ensure_user(store, urllib.parse.unquote(acct))
        timeline = find_timeline(user, urllib.parse.unquote(timeline_id))
        return timeline_to_ics(user['acct'], timeline) if timeline else None
    if path.startswith('/bundle/private/') and path.endswith('.ics'):
        parts = [part for part in path.split('/') if part]
        if len(parts) != 4:
            return None
        acct = urllib.parse.unquote(parts[2])
        sub_id = urllib.parse.unquote(parts[3][:-4])
        user = ensure_user(store, acct)
        item = find_subscription(user, sub_id)
        if not item or item.get('kind') != 'bundle':
            return None
        urls = resolve_subscription_urls(user, item, store=store)
        return merged_calendar_bytes(urls, item.get('title') or sub_id, f"Merged private timeline from {acct}", '-//TimeGrid//Merged Timeline//EN', store=store, session=session)
    if path.startswith('/bundle/') and path.endswith('.ics'):
        slug = urllib.parse.unquote(path[len('/bundle/'): -4])
        bundle = store.get('published', {}).get(slug)
        if not bundle or not bundle_discoverable(bundle):
            return None
        if session is not None and not bundle_visible_to_session(bundle, session):
            return None
        urls = bundle_urls(store, bundle, session)
        return merged_calendar_bytes(urls, bundle.get('title') or slug, f"Published bundle from {APP_BASE_URL}/p/{slug}", '-//TimeGrid//Published Bundle//EN', store=store, session=session)
    return None


def calendar_text_for_url(url: str, store: dict[str, Any] | None = None, session: dict[str, Any] | None = None) -> str | None:
    cache_key = str(url or '')
    now_ts = time.time()
    if cache_key:
        with CALENDAR_TEXT_CACHE_LOCK:
            cached = CALENDAR_TEXT_CACHE.get(cache_key)
            if cached and cached[0] > now_ts:
                return cached[1]
    if store is not None:
        local = local_calendar_bytes(url, store, session)
        if local is not None:
            text = local.decode('utf-8', errors='replace')
            if cache_key:
                with CALENDAR_TEXT_CACHE_LOCK:
                    CALENDAR_TEXT_CACHE[cache_key] = (now_ts + CALENDAR_TEXT_CACHE_TTL, text)
            return text
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        text = resp.text
        if cache_key:
            with CALENDAR_TEXT_CACHE_LOCK:
                CALENDAR_TEXT_CACHE[cache_key] = (now_ts + CALENDAR_TEXT_CACHE_TTL, text)
        return text
    except Exception:
        return None


def merged_calendar_bytes(urls: list[str], title: str, desc: str, prodid: str, *, store: dict[str, Any] | None = None, session: dict[str, Any] | None = None) -> bytes:
    timezone_blocks: list[list[str]] = []
    event_blocks: list[list[str]] = []
    seen_timezones: set[str] = set()
    seen_events: set[str] = set()
    for url in urls:
        text = calendar_text_for_url(url, store, session)
        if text is None:
            continue
        tz_blocks, ev_blocks = extract_calendar_blocks(text)
        for block in tz_blocks:
            key = '\n'.join(block).strip()
            if key and key not in seen_timezones:
                seen_timezones.add(key)
                timezone_blocks.append(block)
        for block in ev_blocks:
            key = '\n'.join(block).strip()
            if key and key not in seen_events:
                seen_events.add(key)
                event_blocks.append(block)
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        f'PRODID:{prodid}',
        'CALSCALE:GREGORIAN',
        f'X-WR-CALNAME:{escape_ics_text(title)}',
        f'X-WR-CALDESC:{escape_ics_text(desc)}',
        'METHOD:PUBLISH',
        'X-PUBLISHED-TTL:PT5M',
        'REFRESH-INTERVAL;VALUE=DURATION:PT5M',
    ]
    for block in timezone_blocks + event_blocks:
        lines.extend(block)
    lines.append('END:VCALENDAR')
    return ('\r\n'.join(lines) + '\r\n').encode('utf-8')


def timeline_to_ics(acct: str, timeline: dict[str, Any]) -> bytes:
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//TimeGrid//Calendar Timeline//EN',
        'CALSCALE:GREGORIAN',
        f'X-WR-CALNAME:{escape_ics_text(timeline.get("title") or acct)}',
    ]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    for event in timeline.get('events', []):
        title = event.get('title') or 'Untitled event'
        uid = f'{event.get("id")}-{timeline.get("id")}@calendar.time-grid.org'
        lines.extend([
            'BEGIN:VEVENT',
            f'UID:{uid}',
            f'DTSTAMP:{stamp}',
            f'DTSTART:{iso_to_ics(event["start"])}',
            f'DTEND:{iso_to_ics(event["end"])}',
            f'SUMMARY:{escape_ics_text(title)}',
        ])
        rrule = recurrence_to_rrule(event)
        if rrule:
            lines.append(f'RRULE:{rrule}')
        exdates = list(dict.fromkeys(event.get('exdates') or []))
        if exdates:
            lines.append('EXDATE:' + ','.join(iso_to_ics(value) for value in exdates))
        if event.get('description'):
            lines.append(f'DESCRIPTION:{escape_ics_text(event["description"])}')
        if event.get('location'):
            lines.append(f'LOCATION:{escape_ics_text(event["location"])}')
        if event.get('url'):
            lines.append(f'URL:{escape_ics_text(event["url"])}')
        lines.append('END:VEVENT')

        for override in event.get('overrides') or []:
            recurrence_id = override.get('recurrence_id')
            if not recurrence_id or override.get('deleted'):
                continue
            lines.extend([
                'BEGIN:VEVENT',
                f'UID:{uid}',
                f'DTSTAMP:{stamp}',
                f'RECURRENCE-ID:{iso_to_ics(recurrence_id)}',
                f'DTSTART:{iso_to_ics(override.get("start") or recurrence_id)}',
                f'DTEND:{iso_to_ics(override.get("end") or override.get("start") or recurrence_id)}',
                f'SUMMARY:{escape_ics_text(override.get("title") or title)}',
            ])
            if override.get('description'):
                lines.append(f'DESCRIPTION:{escape_ics_text(override["description"])}')
            if override.get('location'):
                lines.append(f'LOCATION:{escape_ics_text(override["location"])}')
            if override.get('url'):
                lines.append(f'URL:{escape_ics_text(override["url"])}')
            lines.append('END:VEVENT')
    lines.append('END:VCALENDAR')
    return ('\r\n'.join(lines) + '\r\n').encode('utf-8')




def personal_export_title(user: dict[str, Any], acct: str) -> str:
    return f"{user.get('display_name') or acct} personal calendar"


def collect_personal_export_sources(acct: str, user: dict[str, Any], store: dict[str, Any], *, calendar_id: str = '') -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_source(source_item: dict[str, Any], color_override: str = '') -> None:
        runtime_url = subscription_runtime_url(acct, user, source_item, store)
        if not runtime_url or runtime_url in seen:
            return
        seen.add(runtime_url)
        sources.append({
            'id': source_item.get('id') or '',
            'title': source_item.get('title') or runtime_url or 'Calendar source',
            'url': runtime_url,
            'color': color_override or source_item.get('color') or '',
            'author_name': source_item.get('author_name') or user.get('display_name') or acct,
            'author_acct': source_item.get('author_acct') or acct,
        })

    for item in user.get('subscriptions', []):
        if not personal_membership_visible(item) or item.get('trashed') or not item.get('visible') or item.get('grouped_in'):
            continue
        if not calendar_visible(item, calendar_id):
            continue
        if item.get('kind') == 'bundle':
            bundle_color = item.get('color') or ''
            for child in leaf_subscriptions(user, item):
                if child.get('trashed'):
                    continue
                add_source(child, bundle_color)
        else:
            add_source(item)
    return sources


def personal_export_metadata(acct: str, user: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    authors: list[str] = []
    timelines: list[str] = []
    for item in sources:
        author_label = item.get('author_name') or item.get('author_acct') or acct
        if author_label and author_label not in authors:
            authors.append(author_label)
        title = item.get('title') or item.get('url') or 'Calendar source'
        if title and title not in timelines:
            timelines.append(title)
    return {
        'title': personal_export_title(user, acct),
        'authors': authors,
        'timelines': timelines,
        'website_name': 'TimeGrid Calendar',
        'website_url': APP_BASE_URL,
        'owner_acct': acct,
        'owner_name': user.get('display_name') or acct,
    }


def build_personal_export_snapshot(acct: str, user: dict[str, Any], store: dict[str, Any], *, calendar_id: str = '') -> dict[str, Any]:
    calendar_id = resolve_calendar_id(user, calendar_id, 'personal')
    sources = collect_personal_export_sources(acct, user, store, calendar_id=calendar_id)
    metadata = personal_export_metadata(acct, user, sources)
    metadata['calendar_id'] = calendar_id
    urls = [item.get('url') or '' for item in sources if item.get('url')]
    title = metadata['title']
    desc = f"Visible personal TimeGrid calendar for @{acct} from {APP_BASE_URL}. Authors: {', '.join(metadata['authors']) or metadata['owner_name']}"
    ics_bytes = merged_calendar_bytes(urls, title, desc, '-//TimeGrid//Personal Export//EN', store=store)
    return {
        'sources': sources,
        'urls': urls,
        'metadata': metadata,
        'ics_bytes': ics_bytes,
        'ics_text': ics_bytes.decode('utf-8'),
    }


def export_token_url(token: str) -> str:
    return f'{APP_BASE_URL}/export/{token}.ics'


def dynamic_calendar_headers(body: bytes) -> dict[str, str]:
    stamp = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
    return {
        'Cache-Control': 'no-store, no-cache, max-age=0, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0',
        'Last-Modified': stamp,
        'ETag': f'"{hashlib.sha256(body).hexdigest()}"',
    }


def ensure_export_record(store: dict[str, Any], acct: str, *, mode: str, snapshot: dict[str, Any] | None = None, calendar_id: str = '') -> dict[str, Any]:
    exports = store.setdefault('exports', {})
    if mode == 'dynamic':
        for token, record in exports.items():
            if record.get('acct') == acct and record.get('kind') == 'dynamic' and str(record.get('calendar_id') or '') == calendar_id:
                record['updated_at'] = now_iso()
                return {'token': token, 'record': record}
        token = new_id('exp')
        record = {
            'acct': acct,
            'kind': 'dynamic',
            'calendar_id': calendar_id,
            'created_at': now_iso(),
            'updated_at': now_iso(),
        }
        exports[token] = record
        return {'token': token, 'record': record}
    token = new_id('exp')
    snapshot = snapshot or {}
    record = {
        'acct': acct,
        'kind': 'static',
        'calendar_id': calendar_id,
        'created_at': now_iso(),
        'updated_at': now_iso(),
        'title': snapshot.get('metadata', {}).get('title') or acct,
        'authors': list(snapshot.get('metadata', {}).get('authors') or []),
        'timelines': list(snapshot.get('metadata', {}).get('timelines') or []),
        'ics_text': snapshot.get('ics_text') or '',
    }
    exports[token] = record
    return {'token': token, 'record': record}


def folded_ics_lines(raw_text: str) -> list[str]:
    lines = str(raw_text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    out: list[str] = []
    for line in lines:
        if not line:
            if out:
                out.append('')
            continue
        if line.startswith((' ', '\t')) and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def unescape_ics_text(value: str) -> str:
    return str(value or '').replace('\\n', '\n').replace('\\,', ',').replace('\\;', ';').replace('\\\\', '\\')


def parse_ics_value(raw: str) -> tuple[datetime | None, bool]:
    value = str(raw or '').strip()
    if not value:
        return None, False
    if 'T' in value:
        cleaned = value.replace('Z', '+00:00') if value.endswith('Z') else value
        try:
            dt = datetime.fromisoformat(cleaned)
        except ValueError:
            try:
                dt = datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
            except ValueError:
                return None, False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc), False
    try:
        dt = datetime.strptime(value[:8], '%Y%m%d').replace(tzinfo=timezone.utc)
        return dt, True
    except ValueError:
        return None, False


def parse_ics_events(raw_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in folded_ics_lines(raw_text):
        upper = line.upper()
        if upper == 'BEGIN:VEVENT':
            current = {}
            continue
        if upper == 'END:VEVENT':
            if current is not None:
                start_dt, all_day = parse_ics_value(current.get('DTSTART', ''))
                end_dt, _ = parse_ics_value(current.get('DTEND', ''))
                if start_dt is not None:
                    if end_dt is None:
                        end_dt = start_dt + timedelta(days=1 if all_day else 0, hours=0 if all_day else 1)
                    events.append({
                        'uid': current.get('UID', ''),
                        'title': unescape_ics_text(current.get('SUMMARY', '') or 'Untitled event'),
                        'start': start_dt,
                        'end': end_dt,
                        'all_day': all_day,
                        'description': unescape_ics_text(current.get('DESCRIPTION', '')),
                        'location': unescape_ics_text(current.get('LOCATION', '')),
                        'url': unescape_ics_text(current.get('URL', '')),
                    })
            current = None
            continue
        if current is None or ':' not in line:
            continue
        left, value = line.split(':', 1)
        key = left.split(';', 1)[0].upper()
        if key in {'UID', 'SUMMARY', 'DTSTART', 'DTEND', 'DESCRIPTION', 'LOCATION', 'URL'}:
            current[key] = value
    events.sort(key=lambda item: (item['start'], item['title']))
    return events


def export_csv_bytes(snapshot: dict[str, Any]) -> bytes:
    buf = StringIO()
    writer = csv.writer(buf)
    meta = snapshot['metadata']
    writer.writerow(['calendar_title', 'website_name', 'website_url', 'timeline_title', 'timeline_author', 'event_title', 'start_utc', 'end_utc', 'all_day', 'description', 'location', 'event_url'])
    title_lookup = {item.get('url') or '': item for item in snapshot.get('sources', [])}
    for url in snapshot.get('urls', []):
        source_item = title_lookup.get(url, {})
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            events = parse_ics_events(resp.text)
        except Exception:
            events = []
        for event in events:
            writer.writerow([
                meta['title'],
                meta['website_name'],
                meta['website_url'],
                source_item.get('title') or meta['title'],
                source_item.get('author_name') or meta['owner_name'],
                event['title'],
                event['start'].strftime('%Y-%m-%d %H:%M:%S UTC'),
                event['end'].strftime('%Y-%m-%d %H:%M:%S UTC'),
                'yes' if event.get('all_day') else 'no',
                event.get('description') or '',
                event.get('location') or '',
                event.get('url') or '',
            ])
    return buf.getvalue().encode('utf-8')


def pdf_escape(value: str) -> str:
    return str(value).replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def pdf_text_command(x: int, y: int, text_value: str, *, font: str = 'F1', size: int = 12) -> str:
    return f'BT /{font} {size} Tf 1 0 0 1 {x} {y} Tm ({pdf_escape(text_value)}) Tj ET\n'


def pdf_line_command(x1: int, y1: int, x2: int, y2: int) -> str:
    return f'{x1} {y1} m {x2} {y2} l S\n'


def build_simple_pdf(page_specs: list[dict[str, Any]]) -> bytes:
    objects: list[bytes] = []

    def add_object(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    font_regular = add_object(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    font_bold = add_object(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>')
    font_mono = add_object(b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>')

    page_object_templates: list[tuple[int, tuple[int, int]]] = []
    for spec in page_specs:
        stream = spec['stream'].encode('latin-1', 'replace')
        content_id = add_object(f"<< /Length {len(stream)} >>\nstream\n".encode('latin-1') + stream + b'endstream')
        page_object_templates.append((content_id, spec['size']))

    pages_id = len(objects) + len(page_object_templates) + 1
    page_ids: list[int] = []
    for content_id, size in page_object_templates:
        width, height = size
        payload = f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {width} {height}] /Resources << /Font << /F1 {font_regular} 0 R /F2 {font_bold} 0 R /F3 {font_mono} 0 R >> >> /Contents {content_id} 0 R >>".encode('latin-1')
        page_ids.append(add_object(payload))

    kids = ' '.join(f'{page_id} 0 R' for page_id in page_ids)
    add_object(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode('latin-1'))
    catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode('latin-1'))

    out = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f'{index} 0 obj\n'.encode('latin-1'))
        out.extend(obj)
        out.extend(b'\nendobj\n')
    xref_start = len(out)
    out.extend(f'xref\n0 {len(objects) + 1}\n'.encode('latin-1'))
    out.extend(b'0000000000 65535 f \n')
    for offset in offsets[1:]:
        out.extend(f'{offset:010d} 00000 n \n'.encode('latin-1'))
    out.extend(f'trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_start}\n%%EOF'.encode('latin-1'))
    return bytes(out)


def format_export_event(event: dict[str, Any]) -> str:
    if event.get('all_day'):
        return f"{event['start'].strftime('%b %d')} all day - {event['title']}"
    return f"{event['start'].strftime('%b %d %H:%M')} - {event['title']}"


def pdf_rect_command(x: int, y: int, width: int, height: int, *, fill_rgb: tuple[float, float, float] | None = None, stroke: bool = True) -> str:
    out = ''
    if fill_rgb is not None:
        r, g, b = fill_rgb
        out += f'q {r:.3f} {g:.3f} {b:.3f} rg {x} {y} {width} {height} re f Q\n'
    if stroke:
        out += f'{x} {y} {width} {height} re S\n'
    return out


def month_matrix(year: int, month: int) -> list[list[int]]:
    return pycalendar.Calendar(firstweekday=0).monthdayscalendar(year, month)


def events_by_date(events: list[dict[str, Any]], year: int | None = None, month: int | None = None) -> dict[date, list[dict[str, Any]]]:
    buckets: dict[date, list[dict[str, Any]]] = {}
    for event in events:
        start_dt = event.get('start')
        if not isinstance(start_dt, datetime):
            continue
        if year is not None and start_dt.year != year:
            continue
        if month is not None and start_dt.month != month:
            continue
        buckets.setdefault(start_dt.date(), []).append(event)
    for value in buckets.values():
        value.sort(key=lambda item: item['start'])
    return buckets


def truncate_text(value: str, limit: int) -> str:
    text_value = str(value or '').strip()
    if len(text_value) <= limit:
        return text_value
    return text_value[: max(1, limit - 1)].rstrip() + '…'


def render_month_grid(stream: str, *, year: int, month: int, x: int, y_top: int, width: int, height: int, events_map: dict[date, list[dict[str, Any]]], compact: bool) -> str:
    headers = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
    matrix = month_matrix(year, month)
    title_y = y_top - 14
    stream += pdf_text_command(x + 6, title_y, f'{pycalendar.month_name[month]} {year}', font='F2', size=11 if compact else 15)
    grid_top = y_top - (28 if compact else 34)
    cell_w = width // 7
    cell_h = (height - (34 if compact else 44)) // 7
    for idx, label in enumerate(headers):
        hx = x + idx * cell_w + 4
        stream += pdf_text_command(hx, grid_top - 10, label, font='F3', size=7 if compact else 9)
    grid_y_top = grid_top - 16
    rows = 6
    for row in range(rows + 1):
        yy = grid_y_top - row * cell_h
        stream += pdf_line_command(x, yy, x + width, yy)
    for col in range(8):
        xx = x + col * cell_w
        stream += pdf_line_command(xx, grid_y_top, xx, grid_y_top - rows * cell_h)
    for row_index in range(rows):
        week = matrix[row_index] if row_index < len(matrix) else [0] * 7
        for col_index, day_num in enumerate(week):
            if not day_num:
                continue
            cell_x = x + col_index * cell_w
            cell_top = grid_y_top - row_index * cell_h
            cell_bottom = cell_top - cell_h
            day_date = date(year, month, day_num)
            day_events = events_map.get(day_date, [])
            if day_events:
                stream += pdf_rect_command(cell_x + 1, cell_bottom + 1, cell_w - 2, cell_h - 2, fill_rgb=(0.91, 0.96, 0.94), stroke=False)
            stream += pdf_text_command(cell_x + 4, cell_top - 11, str(day_num), font='F2' if day_events else 'F1', size=7 if compact else 9)
            if compact:
                if day_events:
                    stream += pdf_rect_command(cell_x + cell_w - 10, cell_top - 11, 4, 4, fill_rgb=(0.12, 0.44, 0.47), stroke=False)
                    count_label = str(len(day_events)) if len(day_events) < 10 else '9+'
                    stream += pdf_text_command(cell_x + cell_w - 19, cell_top - 11, count_label, size=6)
                continue
            line_y = cell_top - 24
            for event in day_events[:3]:
                prefix = 'All day' if event.get('all_day') else event['start'].strftime('%H:%M')
                label = truncate_text(f'{prefix} {event.get("title") or "Event"}', 18)
                stream += pdf_rect_command(cell_x + 4, line_y + 2, 3, 3, fill_rgb=(0.12, 0.44, 0.47), stroke=False)
                stream += pdf_text_command(cell_x + 10, line_y, label, size=7)
                line_y -= 10
            extra = len(day_events) - 3
            if extra > 0:
                stream += pdf_text_command(cell_x + 10, cell_bottom + 6, f'+{extra} more', size=7)
    return stream


def render_year_page(year: int, meta: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    stream = ''
    stream += pdf_text_command(40, 575, f"{meta['title']} - Year view {year}", font='F2', size=18)
    stream += pdf_text_command(40, 555, f"TimeGrid Calendar | {meta['website_url']}", size=11)
    stream += pdf_text_command(40, 539, f"Authors: {', '.join(meta['authors']) or meta['owner_name']}", size=11)
    stream += pdf_text_command(40, 523, f"Visible timelines: {', '.join(meta['timelines'][:6])}", size=10)
    yearly_events = events_by_date(events, year)
    block_w = 220
    block_h = 95
    col_x = [40, 286, 532]
    row_top = [500, 384, 268, 152]
    month = 1
    for top in row_top:
        for x in col_x:
            stream += pdf_rect_command(x, top - block_h, block_w, block_h, stroke=True)
            month_events = {day: value for day, value in yearly_events.items() if day.month == month}
            stream = render_month_grid(stream, year=year, month=month, x=x, y_top=top, width=block_w, height=block_h, events_map=month_events, compact=True)
            month += 1
    stream += pdf_text_command(40, 32, f"Events in export: {len([item for item in events if item['start'].year == year])}", size=10)
    return {'size': (792, 612), 'stream': stream}


def render_month_pages(year: int, meta: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for month in range(1, 13):
        stream = ''
        stream += pdf_text_command(40, 760, f"{meta['title']} - {pycalendar.month_name[month]} {year}", font='F2', size=18)
        stream += pdf_text_command(40, 742, f"TimeGrid Calendar | {meta['website_url']}", size=10)
        stream += pdf_text_command(40, 728, f"Authors: {', '.join(meta['authors']) or meta['owner_name']}", size=10)
        month_events = events_by_date(events, year, month)
        stream = render_month_grid(stream, year=year, month=month, x=40, y_top=700, width=532, height=560, events_map=month_events, compact=False)
        pages.append({'size': (612, 792), 'stream': stream})
    return pages

def week_start_dates(year: int) -> list[datetime]:
    day = datetime(year, 1, 1, tzinfo=timezone.utc)
    day -= timedelta(days=day.weekday())
    out: list[datetime] = []
    while day.year <= year or (day + timedelta(days=6)).year <= year:
        if day.year == year or (day + timedelta(days=6)).year == year:
            out.append(day)
        day += timedelta(days=7)
    return out


def render_week_pages(year: int, meta: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for week_start in week_start_dates(year):
        week_end = week_start + timedelta(days=6)
        label = f"Week of {week_start.strftime('%b %d, %Y')} to {week_end.strftime('%b %d, %Y')}"
        stream = ''
        stream += pdf_text_command(40, 760, f"{meta['title']} - {label}", font='F2', size=17)
        stream += pdf_text_command(40, 742, f"TimeGrid Calendar | {meta['website_url']}", size=10)
        stream += pdf_text_command(40, 726, f"Authors: {', '.join(meta['authors']) or meta['owner_name']}", size=10)
        y = 690
        week_events = [item for item in events if week_start.date() <= item['start'].date() <= week_end.date()]
        if not week_events:
            stream += pdf_text_command(40, y, 'No events in this week.', size=11)
        else:
            for event in week_events[:42]:
                stream += pdf_text_command(40, y, format_export_event(event), size=11)
                y -= 14
        pages.append({'size': (612, 792), 'stream': stream})
    return pages


def export_pdf_bytes(snapshot: dict[str, Any], year: int, view: str) -> bytes:
    events = parse_ics_events(snapshot.get('ics_text') or '')
    meta = snapshot['metadata']
    if view == 'week':
        pages = render_week_pages(year, meta, events)
    elif view == 'month':
        pages = render_month_pages(year, meta, events)
    else:
        pages = [render_year_page(year, meta, events)]
    return build_simple_pdf(pages)


def calendar_head() -> str:
    return f'''
<link rel="stylesheet" href="{asset_href("/schedule-x-readonly.css", SCHEDULE_X_READONLY_CSS)}" />
<script src="{asset_href("/timegrid-calendar-domain.js", CALENDAR_DOMAIN_JS)}" defer></script>
<script src="{asset_href("/timegrid-calendar-editor.js", CALENDAR_EDITOR_JS)}" defer></script>
<script src="{asset_href("/timegrid-timeline-controller.js", TIMELINE_CONTROLLER_JS)}" defer></script>
<script src="{asset_href("/schedule-x-frame.js", SCHEDULE_X_FRAME_JS)}" defer></script>
<script src="{asset_href("/schedule-x-readonly.js", SCHEDULE_X_READONLY_JS)}" defer></script>
'''


def page_shell(title: str, page: str, body_class: str = '', extra_head: str = '', body_attrs: str = '', app_html: str = '') -> bytes:
    title_esc = html.escape(title)
    body_attrs = f' {body_attrs.strip()}' if body_attrs.strip() else ''
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title_esc}</title>
  <meta property="og:title" content="{title_esc}" />
  <meta property="og:type" content="website" />
  <meta property="og:image" content="{APP_BASE_URL}/timegrids-icon.png" />
  <link rel="icon" type="image/png" href="/timegrids-icon.png" />
  <link rel="stylesheet" href="{asset_href("/styles.css", STYLES_CSS)}" />
  {extra_head}
</head>
<body class="{body_class}" data-page="{page}"{body_attrs}>
  <div id="app">{app_html}</div>
  <script src="{asset_href("/app.js", APP_JS)}" defer></script>
</body>
</html>'''.encode('utf-8')


def auth_initial_html(next_path: str) -> str:
    next_href = html.escape(next_path or '/', quote=True)
    mastodon_href = html.escape(f'/auth/login?next={urllib.parse.quote(next_path or "/")}', quote=True)
    return f'''
    <div class="auth-shell">
      <section class="auth-centered-card">
        <div class="auth-mark">TimeGrid</div>
        <h1>Create your TimeGrid account</h1>
        <p class="auth-subcopy">Use one account for calendars, creator pages, publishing, invites, and dynamic exports.</p>
        <div class="auth-mode-switch" role="tablist" aria-label="Auth mode">
          <button type="button" class="active">Sign up</button>
          <button type="button">Sign in</button>
        </div>
        <div class="auth-secondary-list">
          <a class="button auth-provider-button" href="{mastodon_href}">Continue with Mastodon</a>
        </div>
        <div class="auth-help">Use your social.time-grid.org Mastodon account for TimeGrid access.</div>
        <div class="auth-link-row">
          <a href="/published">Browse published calendars</a>
          <span>·</span>
          <a href="{next_href}">Back to TimeGrid</a>
        </div>
      </section>
    </div>'''


def timeline_page(title: str) -> bytes:
    return page_shell(title, 'timeline', 'timeline-page', calendar_head())


def compact_embed_page(title: str, description: str, iframe_url: str, share_url: str, eyebrow: str = 'TimeGrid Calendar', subscribe_url: str = '') -> bytes:
    title_esc = html.escape(title)
    desc_esc = html.escape(description)
    iframe_esc = html.escape(iframe_url, quote=True)
    share_esc = html.escape(share_url, quote=True)
    subscribe_esc = html.escape(subscribe_url, quote=True) if subscribe_url else ''
    eyebrow_esc = html.escape(eyebrow)
    subscribe_link = f'<a class="embed-action" href="{subscribe_esc}">Subscribe</a>' if subscribe_esc else ''
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title_esc}</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Georgia, "Times New Roman", serif; background: #eef1ea; color: #1f1a18; }}
    .embed-shell {{ padding: 12px; }}
    .embed-card {{ border: 1px solid #d2cbbe; border-radius: 18px; overflow: hidden; background: #f5f1e8; box-shadow: 0 18px 40px rgba(73, 62, 49, 0.14); }}
    .embed-copy {{ padding: 14px 18px 10px; background: linear-gradient(135deg, rgba(244,247,240,0.98), rgba(248,238,228,0.96)); border-bottom: 1px solid #d8d0c4; }}
    .embed-copy .eyebrow {{ text-transform: uppercase; letter-spacing: 0.18em; font-size: 12px; font-weight: 700; color: #1f7a82; margin-bottom: 6px; }}
    .embed-copy h1 {{ margin: 0 0 6px; font-size: 24px; line-height: 1.05; }}
    .embed-copy p {{ margin: 0; font-size: 13px; line-height: 1.35; color: #544c46; }}
    .embed-frame {{ background: #fdfbf7; padding: 10px 10px 0; height: 364px; overflow: hidden; }}
    .embed-frame iframe {{ display: block; width: 100%; height: 420px; margin-top: -40px; border: 0; border-radius: 12px; background: #fff; }}
    .embed-footer {{ padding: 12px 18px 16px; font: 600 13px/1.2 system-ui, sans-serif; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
    .embed-footer a {{ color: #1f7a82; text-decoration: none; }}
    .embed-action {{ display: inline-flex; align-items: center; justify-content: center; padding: 8px 14px; border-radius: 999px; border: 1px solid #1f7a82; background: #1f7a82; color: #fff !important; }}
    .embed-footer .embed-open {{ color: #1f7a82; font-weight: 700; }}
  </style>
</head>
<body>
  <div class="embed-shell">
    <article class="embed-card">
      <div class="embed-copy">
        <div class="eyebrow">{eyebrow_esc}</div>
        <h1>{title_esc}</h1>
        <p>{desc_esc}</p>
      </div>
      <div class="embed-frame">
        <iframe src="{iframe_esc}" title="{title_esc}" sandbox="allow-scripts allow-same-origin allow-top-navigation allow-downloads" scrolling="no"></iframe>
      </div>
      <div class="embed-footer">{subscribe_link}<a class="embed-open" href="{share_esc}" target="_blank" rel="noreferrer">Open interactive calendar</a></div>
    </article>
  </div>
</body>
</html>""".encode('utf-8')


def marketing_embed_page() -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TimeGrid Calendar</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Georgia, "Times New Roman", serif; background: radial-gradient(circle at top, #f6f1e8 0%, #edf3eb 52%, #e6ece5 100%); color: #201815; }}
    .cover-shell {{ min-height: 100vh; padding: 18px; display: flex; align-items: stretch; }}
    .cover-card {{ width: 100%; border-radius: 22px; overflow: hidden; border: 1px solid #d3cabd; background: linear-gradient(145deg, rgba(248, 244, 236, 0.98), rgba(240, 246, 239, 0.98)); box-shadow: 0 24px 56px rgba(73, 62, 49, 0.18); display: grid; grid-template-columns: 1.1fr 0.9fr; min-height: 380px; }}
    .cover-copy {{ padding: 28px 30px 24px; display: flex; flex-direction: column; justify-content: space-between; }}
    .eyebrow {{ text-transform: uppercase; letter-spacing: 0.2em; font-size: 12px; font-weight: 700; color: #1f7a82; margin-bottom: 10px; }}
    h1 {{ margin: 0 0 10px; font-size: 52px; line-height: 0.95; }}
    .tagline {{ margin: 0 0 18px; font-size: 20px; line-height: 1.35; color: #4e4740; max-width: 560px; }}
    .bullet-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }}
    .bullet {{ border: 1px solid #d7cebf; border-radius: 16px; background: rgba(255,255,255,0.48); padding: 14px 15px; }}
    .bullet strong {{ display: block; margin-bottom: 6px; font-size: 18px; }}
    .bullet span {{ color: #5d5650; font-size: 14px; line-height: 1.35; }}
    .cover-actions {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 22px; font: 600 14px/1.2 system-ui, sans-serif; }}
    .cover-actions a {{ text-decoration: none; border-radius: 999px; padding: 10px 16px; }}
    .cover-actions .primary {{ background: #1f7a82; color: #fff; border: 1px solid #1f7a82; }}
    .cover-actions .secondary {{ color: #1f7a82; border: 1px solid #cfc6b8; background: rgba(255,255,255,0.54); }}
    .cover-visual {{ position: relative; padding: 28px; background: linear-gradient(160deg, rgba(31,122,130,0.12), rgba(198, 132, 39, 0.14)); display: flex; align-items: center; justify-content: center; }}
    .hero-panel {{ width: 100%; max-width: 420px; border-radius: 24px; background: rgba(255,255,255,0.76); border: 1px solid rgba(212, 203, 190, 0.95); padding: 22px; box-shadow: inset 0 1px 0 rgba(255,255,255,0.82), 0 18px 40px rgba(60, 55, 48, 0.12); }}
    .hero-top {{ display: flex; align-items: center; gap: 16px; margin-bottom: 18px; }}
    .hero-top img {{ width: 72px; height: 72px; border-radius: 20px; box-shadow: 0 12px 24px rgba(76, 57, 170, 0.18); }}
    .hero-top strong {{ display: block; font-size: 24px; }}
    .hero-top span {{ color: #5b544d; font-size: 15px; line-height: 1.35; }}
    .mini-stack {{ display: grid; gap: 10px; }}
    .mini-card {{ border-radius: 16px; border: 1px solid #d8cfbf; background: #fffdf8; padding: 14px 15px; }}
    .mini-card strong {{ display: block; font-size: 17px; margin-bottom: 4px; }}
    .mini-card span {{ color: #5c554d; font-size: 13px; line-height: 1.35; }}
    @media (max-width: 860px) {{
      .cover-card {{ grid-template-columns: 1fr; }}
      .cover-shell {{ padding: 12px; }}
      h1 {{ font-size: 42px; }}
      .bullet-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="cover-shell">
    <article class="cover-card">
      <section class="cover-copy">
        <div>
          <div class="eyebrow">TimeGrid Calendar</div>
          <h1>Private timelines. Public share pages.</h1>
          <p class="tagline">Build personal calendars from subscriptions, editable timelines, and merged schedules, then publish clean share pages for Mastodon.</p>
          <div class="bullet-grid">
            <div class="bullet"><strong>Personal workspace</strong><span>Control visibility, colors, merges, trash, and imported timelines in one place.</span></div>
            <div class="bullet"><strong>Editable timelines</strong><span>Create or import events, then keep them synced as your own subscription feeds.</span></div>
            <div class="bullet"><strong>Published bundles</strong><span>Turn selected timelines into shareable public, invited, or private calendar pages.</span></div>
            <div class="bullet"><strong>Mastodon-ready</strong><span>Share directly to social.time-grid.org with link previews built for quick browsing.</span></div>
          </div>
        </div>
        <div class="cover-actions">
          <a class="primary" href="{APP_BASE_URL}">Open TimeGrid</a>
          <a class="secondary" href="{APP_BASE_URL}/published">Browse published calendars</a>
        </div>
      </section>
      <section class="cover-visual">
        <div class="hero-panel">
          <div class="hero-top">
            <img src="{APP_BASE_URL}/timegrids-icon.png" alt="TimeGrid logo" />
            <div>
              <strong>TimeGrid</strong>
              <span>Calendar subscriptions, editable timelines, and publishable schedule pages.</span>
            </div>
          </div>
          <div class="mini-stack">
            <div class="mini-card"><strong>Subscribe and merge</strong><span>Bring together holidays, classes, racing calendars, and your own event timelines.</span></div>
            <div class="mini-card"><strong>Keep control</strong><span>Personal colors, editable wrappers, and selective publishing stay separate for each user.</span></div>
            <div class="mini-card"><strong>Share cleanly</strong><span>Publish a timetable page, send it to Mastodon, and let others subscribe to the live bundle.</span></div>
          </div>
        </div>
      </section>
    </article>
  </div>
</body>
</html>""".encode('utf-8')


def embed_access_page(*, title: str, message: str, action_label: str = '', action_href: str = '', secondary_label: str = '', secondary_href: str = '') -> bytes:
    title_esc = html.escape(title)
    message_esc = html.escape(message)
    action = f'<a class="primary" href="{html.escape(action_href, quote=True)}" target="_blank" rel="noreferrer noopener">{html.escape(action_label)}</a>' if action_label and action_href else ''
    secondary = f'<a class="secondary" href="{html.escape(secondary_href, quote=True)}" target="_blank" rel="noreferrer noopener">{html.escape(secondary_label)}</a>' if secondary_label and secondary_href else ''
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title_esc}</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Georgia, "Times New Roman", serif; background: radial-gradient(circle at top, #f6f1e8 0%, #edf3eb 52%, #e6ece5 100%); color: #201815; }}
    .access-shell {{ min-height: 100vh; padding: 18px; display: flex; align-items: center; justify-content: center; }}
    .access-card {{ width: min(760px, 100%); border-radius: 22px; border: 1px solid #d3cabd; background: linear-gradient(145deg, rgba(248, 244, 236, 0.98), rgba(240, 246, 239, 0.98)); box-shadow: 0 24px 56px rgba(73, 62, 49, 0.18); padding: 28px 30px; }}
    .eyebrow {{ text-transform: uppercase; letter-spacing: 0.2em; font-size: 12px; font-weight: 700; color: #1f7a82; margin-bottom: 10px; }}
    h1 {{ margin: 0 0 10px; font-size: 42px; line-height: 0.95; }}
    p {{ margin: 0; font-size: 18px; line-height: 1.45; color: #514a43; max-width: 640px; }}
    .actions {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 22px; font: 600 14px/1.2 system-ui, sans-serif; }}
    .actions a {{ text-decoration: none; border-radius: 999px; padding: 10px 16px; }}
    .actions .primary {{ background: #1f7a82; color: #fff; border: 1px solid #1f7a82; }}
    .actions .secondary {{ color: #1f7a82; border: 1px solid #cfc6b8; background: rgba(255,255,255,0.54); }}
  </style>
</head>
<body>
  <div class="access-shell">
    <article class="access-card">
      <div class="eyebrow">TimeGrid Calendar</div>
      <h1>{title_esc}</h1>
      <p>{message_esc}</p>
      <div class="actions">{action}{secondary}</div>
    </article>
  </div>
</body>
</html>""".encode('utf-8')


def not_found_page(*, title: str = 'Not found', message: str = 'This timeline is not available.') -> bytes:
    return embed_access_page(
        title=title,
        message=message,
        action_label='Browse published calendars',
        action_href='/published',
        secondary_label='Open TimeGrid',
        secondary_href='/',
    )


def published_embed_page(bundle: dict[str, Any], urls: list[str]) -> bytes:
    title = html.escape(bundle['title'])
    desc_text = f"Published by @{bundle['owner_acct']} with {len(urls)} subscription{'s' if len(urls) != 1 else ''}"
    desc = html.escape(desc_text)
    share_url = f'{APP_BASE_URL}/p/{bundle["slug"]}'
    share = html.escape(share_url, quote=True)
    slug_attr = html.escape(bundle['slug'], quote=True)
    subscribe = html.escape(bundle_subscribe_url(bundle['slug']), quote=True)
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <meta property="og:title" content="{title}" />
  <meta property="og:description" content="{desc}" />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="{share}" />
  <meta property="og:image" content="{APP_BASE_URL}/timegrids-icon.png" />
  <link rel="icon" type="image/png" href="/timegrids-icon.png" />
  <link rel="stylesheet" href="{asset_href("/styles.css", STYLES_CSS)}" />
  {calendar_head()}
</head>
<body class="compact-embed-page" data-page="published-embed" data-published-slug="{slug_attr}" data-subscribe-url="{subscribe}">
  <div id="app"></div>
  <script src="{asset_href("/app.js", APP_JS)}" defer></script>
</body>
</html>'''.encode('utf-8')


def published_page(bundle: dict[str, Any], urls: list[str]) -> bytes:
    title = html.escape(bundle['title'])
    desc_text = f"Published by @{bundle['owner_acct']} with {len(urls)} subscription{'s' if len(urls) != 1 else ''}"
    desc = html.escape(desc_text)
    share_url = f'{APP_BASE_URL}/p/{bundle["slug"]}'
    share = html.escape(share_url, quote=True)
    slug_attr = html.escape(bundle['slug'], quote=True)
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} | TimeGrid</title>
  <meta property="og:title" content="{title}" />
  <meta property="og:description" content="{desc}" />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="{share}" />
  <meta property="og:image" content="{APP_BASE_URL}/timegrids-icon.png" />
  <meta name="twitter:card" content="summary_large_image" />
  <link rel="icon" type="image/png" href="/timegrids-icon.png" />
  <link rel="stylesheet" href="{asset_href("/styles.css", STYLES_CSS)}" />
  {calendar_head()}
</head>
<body class="published-detail-page" data-page="published-detail" data-published-slug="{slug_attr}">
  <div id="app"></div>
  <script src="{asset_href("/app.js", APP_JS)}" defer></script>
</body>
</html>'''.encode('utf-8')


class Handler(BaseHTTPRequestHandler):
    server_version = 'TimeGridCalendar/1.1'

    def log_message(self, fmt: str, *args: Any) -> None:
        print('%s - - [%s] %s' % (self.address_string(), self.log_date_time_string(), fmt % args))

    def parse_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get('Content-Length', '0'))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode('utf-8'))

    def parse_form_body(self) -> dict[str, str]:
        length = int(self.headers.get('Content-Length', '0'))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        parsed = urllib.parse.parse_qs(raw.decode('utf-8'), keep_blank_values=True)
        return {key: values[-1] if values else '' for key, values in parsed.items()}

    def external_callback_url(self, provider_id: str) -> str:
        return f'{APP_BASE_URL}/auth/provider/{provider_id}/callback'

    def start_external_auth(self, provider_id: str, next_path: str) -> None:
        if provider_id in {'google', 'apple'} and supabase_auth_enabled():
            self.redirect(supabase_oauth_authorize_url(provider_id, next_path))
            return
        config = external_provider_config(provider_id)
        if not config:
            self.redirect(f'/auth?next={urllib.parse.quote(next_path, safe="/?=&")}')
            return
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        nonce = secrets.token_urlsafe(24)
        params = {
            'response_type': 'code',
            'client_id': config['client_id'],
            'redirect_uri': self.external_callback_url(provider_id),
            'scope': config.get('scope') or 'openid email profile',
            'state': state,
        }
        if config.get('use_pkce'):
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            params['code_challenge'] = challenge
            params['code_challenge_method'] = 'S256'
        if provider_id == 'apple':
            params['response_mode'] = config.get('response_mode') or 'form_post'
            params['nonce'] = nonce
        pending_auth[state] = {
            'provider': provider_id,
            'verifier': verifier,
            'nonce': nonce,
            'next': next_path,
            'created_at': time.time(),
        }
        save_auth_state()
        self.redirect(f'{config["authorize_url"]}?{urllib.parse.urlencode(params)}')

    def finish_external_auth(self, provider_id: str, params: dict[str, str]) -> None:
        code = str(params.get('code') or '')
        state = str(params.get('state') or '')
        auth_ctx = pending_auth.pop(state, None)
        save_auth_state()
        if not code or not auth_ctx or auth_ctx.get('provider') != provider_id:
            self.redirect('/auth?next=%2F')
            return
        config = external_provider_config(provider_id)
        if not config:
            self.redirect('/auth?next=%2F')
            return
        token_payload = {
            'grant_type': 'authorization_code',
            'code': code,
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
            'redirect_uri': self.external_callback_url(provider_id),
        }
        if config.get('use_pkce'):
            token_payload['code_verifier'] = auth_ctx.get('verifier') or ''
        token_resp = requests.post(config['token_url'], data=token_payload, timeout=20)
        token_resp.raise_for_status()
        token_data = token_resp.json()
        claims: dict[str, Any] = {}
        if token_data.get('id_token'):
            claims = decode_jwt_payload(str(token_data.get('id_token') or ''))
        if config.get('userinfo_url') and token_data.get('access_token'):
            try:
                userinfo_resp = requests.get(
                    config['userinfo_url'],
                    headers={'Authorization': f'Bearer {token_data["access_token"]}'},
                    timeout=20,
                )
                userinfo_resp.raise_for_status()
                claims.update(userinfo_resp.json())
            except Exception:
                pass
        subject = str(claims.get('sub') or '')
        email = str(claims.get('email') or '').strip().lower()
        display_name = str(claims.get('name') or claims.get('preferred_username') or claims.get('given_name') or email.split('@', 1)[0] if email else provider_id).strip()
        avatar = str(claims.get('picture') or '')
        email_verified = str(claims.get('email_verified') or 'true').lower() not in {'false', '0', ''}
        if not subject:
            self.redirect('/auth?next=%2F')
            return
        store = load_store()
        user = resolve_or_create_external_user(
            store,
            provider=provider_id,
            subject=subject,
            email=email,
            display_name=display_name,
            avatar=avatar,
            email_verified=email_verified,
        )
        save_user_fragment(store, user['acct'], identities=True, calendars=True)
        session_id, _session = create_session_for_user(user, provider=provider_id)
        next_path = auth_ctx.get('next') or f'/u/{user["acct"]}'
        next_path = safe_post_auth_path(next_path, user['acct'])
        self.redirect(next_path, headers={'Set-Cookie': make_cookie_header(session_id)})

    def send_bytes(self, status: int, body: bytes, content_type: str = 'text/html; charset=utf-8', headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

    def send_json(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        merged = {'Cache-Control': 'no-store'}
        if headers:
            merged.update(headers)
        self.send_bytes(status, json.dumps(payload).encode('utf-8'), 'application/json; charset=utf-8', merged)

    def mastodon_cors_headers(self) -> dict[str, str]:
        origin = str(self.headers.get('Origin') or '').rstrip('/')
        if origin and origin == MASTODON_BASE_URL:
            return {
                'Access-Control-Allow-Origin': origin,
                'Access-Control-Allow-Credentials': 'true',
                'Vary': 'Origin',
            }
        return {}

    def redirect(self, location: str, headers: dict[str, str] | None = None) -> None:
        merged = {'Location': location}
        if headers:
            merged.update(headers)
        self.send_bytes(HTTPStatus.FOUND, b'', headers=merged)

    def current_session(self) -> dict[str, Any] | None:
        if prune_auth_state():
            save_auth_state()
        raw = self.headers.get('Cookie')
        if not raw:
            return None
        cookie = SimpleCookie()
        cookie.load(raw)
        morsel = cookie.get(SESSION_COOKIE)
        if not morsel:
            return None
        return sessions.get(morsel.value)

    def require_session(self) -> dict[str, Any] | None:
        session = self.current_session()
        if session is None:
            self.redirect(f'/auth?next={urllib.parse.quote(self.path, safe="")}')
            return None
        return session

    def is_admin(self, session: dict[str, Any]) -> bool:
        acct = session.get('acct', '').lower()
        if acct in ADMIN_ACCOUNTS:
            return True
        role = (session.get('role') or '').lower()
        if role in {'admin', 'owner', 'moderator'}:
            return True
        if not ADMIN_ACCOUNTS or not acct:
            return False
        try:
            store = load_store()
            user = ensure_user(store, acct)
            emails = {str(item.get('email') or '').strip().lower() for item in linked_identities(user) if str(item.get('email') or '').strip()}
            return bool(emails & ADMIN_ACCOUNTS)
        except Exception:
            return False

    def can_access_personal(self, acct: str, session: dict[str, Any]) -> bool:
        return session.get('acct') == acct or self.is_admin(session)

    def serve_static(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_json(404, {'error': 'not_found'})
            return
        mime, _ = mimetypes.guess_type(str(path))
        self.send_bytes(200, path.read_bytes(), mime or 'application/octet-stream')

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_OPTIONS(self) -> None:
        headers = {
            'Allow': 'GET, HEAD, POST, PATCH, DELETE, OPTIONS',
            'Access-Control-Allow-Methods': 'GET, HEAD, POST, PATCH, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
            'Cache-Control': 'no-store',
        }
        headers.update(self.mastodon_cors_headers())
        self.send_bytes(HTTPStatus.NO_CONTENT, b'', headers=headers)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)


        if path.startswith('/export/') and path.endswith('.ics'):
            token = path[len('/export/'): -4]
            store = load_store()
            record = store.get('exports', {}).get(token)
            if not record:
                self.send_json(404, {'error': 'not_found'})
                return
            acct = str(record.get('acct') or '').strip()
            if not acct:
                self.send_json(404, {'error': 'not_found'})
                return
            if record.get('kind') == 'dynamic':
                user = ensure_user(store, acct)
                snapshot = build_personal_export_snapshot(acct, user, store, calendar_id=str(record.get('calendar_id') or ''))
                body = snapshot['ics_bytes']
                filename = f"{slugify(snapshot['metadata']['title']) or acct}-dynamic.ics"
                cache_headers = dynamic_calendar_headers(body)
            else:
                body = str(record.get('ics_text') or '').encode('utf-8')
                title = str(record.get('title') or acct)
                filename = f"{slugify(title) or acct}-static.ics"
                cache_headers = {'Cache-Control': 'public, max-age=31536000, immutable'}
            self.send_bytes(200, body, 'text/calendar; charset=utf-8', {'Content-Disposition': f'inline; filename="{filename}"', **cache_headers})
            return

        if path == '/health':
            self.send_json(200, {'ok': True})
            return
        if path == '/api/dev/test-login':
            if not ENABLE_TEST_LOGIN:
                self.send_json(404, {'error': 'not_found'})
                return
            acct = str(query.get('acct', ['sample1'])[0] or 'sample1')
            display_name = str(query.get('display_name', [acct])[0] or acct)
            role = 'admin' if str(query.get('admin', [''])[0]).lower() in {'1', 'true', 'yes'} else ''
            next_path = str(query.get('next', ['/'])[0] or '/')
            session_id, _session, user = create_test_login_session(acct, display_name, role=role)
            self.send_response(302)
            self.send_header('Location', safe_post_auth_path(next_path, user['acct']))
            self.send_header('Set-Cookie', make_cookie_header(session_id))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return
        if path == '/timegrids-icon.png':
            self.serve_static(ICON)
            return
        if path == '/styles.css':
            self.serve_static(STYLES_CSS)
            return
        if path == '/app.js':
            self.serve_static(APP_JS)
            return
        if path == '/schedule-x-frame.js':
            self.serve_static(SCHEDULE_X_FRAME_JS)
            return
        if path == '/timegrid-calendar-domain.js':
            self.serve_static(CALENDAR_DOMAIN_JS)
            return
        if path == '/timegrid-calendar-editor.js':
            self.serve_static(CALENDAR_EDITOR_JS)
            return
        if path == '/timegrid-timeline-controller.js':
            self.serve_static(TIMELINE_CONTROLLER_JS)
            return
        if path == '/schedule-x-readonly.js':
            self.serve_static(SCHEDULE_X_READONLY_JS)
            return
        if path == '/schedule-x-readonly.css':
            self.serve_static(SCHEDULE_X_READONLY_CSS)
            return
        if path == '/auth/login':
            next_path = query.get('next', ['/'])[0]
            state = secrets.token_urlsafe(24)
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            pending_auth[state] = {'verifier': verifier, 'next': next_path, 'created_at': time.time()}
            save_auth_state()
            authorize = (
                f'{MASTODON_BASE_URL}/oauth/authorize?response_type=code'
                f'&client_id={urllib.parse.quote(MASTODON_CLIENT_ID)}'
                f'&redirect_uri={urllib.parse.quote(APP_BASE_URL + "/auth/callback")}'
                f'&scope={urllib.parse.quote("read:accounts")}'
                f'&state={urllib.parse.quote(state)}'
                f'&force_login=true'
                f'&code_challenge={urllib.parse.quote(challenge)}'
                f'&code_challenge_method=S256'
            )
            self.redirect(authorize)
            return
        if path.startswith('/auth/provider/') and path.endswith('/login'):
            provider_id = path.split('/auth/provider/', 1)[1].rsplit('/login', 1)[0].strip('/')
            self.start_external_auth(provider_id, query.get('next', ['/'])[0])
            return
        if path == '/auth/callback':
            code = query.get('code', [''])[0]
            state = query.get('state', [''])[0]
            auth_ctx = pending_auth.pop(state, None)
            save_auth_state()
            if not code or not auth_ctx:
                self.redirect('/auth?next=%2F')
                return
            token_resp = requests.post(
                f'{MASTODON_BASE_URL}/oauth/token',
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'client_id': MASTODON_CLIENT_ID,
                    'client_secret': MASTODON_CLIENT_SECRET,
                    'redirect_uri': APP_BASE_URL + '/auth/callback',
                    'code_verifier': auth_ctx['verifier'],
                    'scope': 'read:accounts',
                },
                timeout=20,
            )
            token_resp.raise_for_status()
            token = token_resp.json()['access_token']
            verify_resp = requests.get(
                f'{MASTODON_BASE_URL}/api/v1/accounts/verify_credentials',
                headers={'Authorization': f'Bearer {token}'},
                timeout=20,
            )
            verify_resp.raise_for_status()
            account = verify_resp.json()
            acct = account.get('acct') or account.get('username')
            role_data = account.get('role') or {}
            role = role_data.get('name', '') if isinstance(role_data, dict) else ''
            store = load_store()
            user = ensure_user(store, acct)
            user['account_id'] = str(account.get('id'))
            user['display_name'] = account.get('display_name') or acct
            user['avatar'] = account.get('avatar') or ''
            user['mastodon_profile'] = {'acct': acct, 'provisioned': True}
            user['onboarding'] = {'calendar_ready': True, 'mastodon_ready': True}
            identities = linked_identities(user)
            if not any(str(item.get('provider') or '') == 'mastodon' and str(item.get('provider_subject') or '') == str(account.get('id')) for item in identities):
                identities.append({
                    'id': new_id('ident'),
                    'provider': 'mastodon',
                    'provider_subject': str(account.get('id') or ''),
                    'email': '',
                    'email_verified': True,
                    'created_at': now_iso(),
                })
            user['updated_at'] = now_iso()
            save_user_fragment(store, acct, identities=True, calendars=True)
            session_id, session_data = create_session_for_user(user, provider='mastodon', role=role)
            session_data['access_token'] = token
            session_data['account_id'] = str(account.get('id'))
            save_auth_state()
            next_path = auth_ctx.get('next') or f'/u/{acct}'
            next_path = safe_post_auth_path(next_path, acct)
            self.redirect(next_path, headers={'Set-Cookie': make_cookie_header(session_id)})
            return
        if path.startswith('/auth/provider/') and path.endswith('/callback'):
            provider_id = path.split('/auth/provider/', 1)[1].rsplit('/callback', 1)[0].strip('/')
            flat = {key: values[-1] if values else '' for key, values in query.items()}
            self.finish_external_auth(provider_id, flat)
            return
        if path == '/auth':
            next_path = query.get('next', ['/'])[0]
            self.send_bytes(
                200,
                page_shell(
                    'Sign in to TimeGrid',
                    'auth',
                    'auth-page',
                    body_attrs=f'data-auth-next="{html.escape(next_path, quote=True)}"',
                    app_html=auth_initial_html(next_path),
                ),
            )
            return
        if path == '/':
            session = self.current_session()
            if session:
                self.redirect(f'/u/{session["acct"]}')
            else:
                self.send_bytes(200, page_shell('TimeGrid Calendar', 'landing', 'landing-page'))
            return
        if path == '/published':
            self.send_bytes(200, page_shell('Published Calendars', 'published', 'published-page'))
            return
        if path == '/people':
            self.send_bytes(200, page_shell('TimeGrid Community', 'community', 'community-page'))
            return
        if path.startswith('/people/'):
            acct = path.split('/people/', 1)[1].strip('/')
            self.send_bytes(200, page_shell(f'{acct} profile', 'community-profile', 'community-page', body_attrs=f'data-profile-acct="{html.escape(acct, quote=True)}"'))
            return
        if path == '/__notfound_test':
            self.send_bytes(404, not_found_page(title='Not found test', message='This is a test of the custom not found page.'))
            return
        if path in ('/embed', '/embed/'):
            self.send_bytes(200, marketing_embed_page())
            return
        if path == '/embed/published':
            self.send_bytes(200, compact_embed_page('Published calendars', 'Browse public merged calendars published by TimeGrid users.', f'{APP_BASE_URL}/published', f'{APP_BASE_URL}/published', 'TimeGrid Calendar'))
            return
        if path.startswith('/embed/p/'):
            slug = path.split('/embed/p/', 1)[1]
            store = load_store()
            bundle = store['published'].get(slug)
            session = self.current_session()
            viewer_user = ensure_user(store, session['acct']) if session else None
            if not bundle:
                body = not_found_page()
                print(f'NOTFOUND_DEBUG /embed/p/{slug} len={len(body)}', flush=True)
                self.send_bytes(404, body)
                return
            if not bundle_discoverable(bundle) and not user_has_bundle_subscription(viewer_user, slug):
                body = not_found_page(message='This published timeline is only available to people who already subscribed.')
                print(f'NOTFOUND_DEBUG /embed/p/{slug} retired len={len(body)}', flush=True)
                self.send_bytes(404, body)
                return
            if not bundle_visible_to_session(bundle, session):
                if session is None:
                    next_path = f'/p/{slug}'
                    self.send_bytes(200, embed_access_page(
                        title='Sign in to view this calendar',
                        message='This published calendar is not public. Sign in to TimeGrid to check whether you have access.',
                        action_label='Open TimeGrid sign in',
                        action_href=f'/auth?next={urllib.parse.quote(next_path, safe="/?=&")}',
                        secondary_label='Open TimeGrid',
                        secondary_href='/',
                    ), headers={'Cache-Control': 'no-store'})
                    return
                self.send_bytes(403, embed_access_page(
                    title='You do not have permission',
                    message='This published calendar is private or invited-only, and your account does not currently have access to it.',
                    action_label='Browse public calendars',
                    action_href='/published',
                    secondary_label='Open TimeGrid',
                    secondary_href='/',
                ), headers={'Cache-Control': 'no-store'})
                return
            self.send_bytes(200, published_embed_page(bundle, bundle_urls(store, bundle, session)), headers={'Cache-Control': 'no-store'})
            return
        if path.startswith('/subscribe/'):
            slug = path.split('/subscribe/', 1)[1].strip('/')
            store = load_store()
            bundle = store['published'].get(slug)
            if not bundle:
                self.send_bytes(404, b'Not found')
                return
            session = self.current_session()
            if session is None:
                next_path = f'/subscribe/{slug}'
                self.redirect(f'/auth?next={urllib.parse.quote(next_path, safe="/?=&")}' )
                return
            viewer_user = ensure_user(store, session['acct'])
            if not bundle_discoverable(bundle) and not user_has_bundle_subscription(viewer_user, slug):
                self.send_bytes(404, b'Not found')
                return
            if not bundle_visible_to_session(bundle, session):
                self.send_bytes(403, b'Forbidden')
                return
            user = viewer_user
            feed_url = bundle_feed_url(slug)
            refs = bundle_component_snapshots(store, bundle, session)
            existing = next((item for item in user.get('subscriptions', []) if item.get('source_bundle_slug') == slug and not item.get('trashed')), None)
            if existing is None and len(refs) <= 1:
                existing = next((item for item in user.get('subscriptions', []) if item.get('url') == feed_url and not item.get('trashed')), None)
            if existing is None:
                if len(refs) > 1:
                    item = {
                        'id': new_id('sub'),
                        'title': bundle.get('title') or slug,
                        'url': feed_url,
                        'visible': True,
                        'trashed': False,
                        'created_at': now_iso(),
                        'color': pick_merge_color(refs),
                        'kind': 'bundle',
                        'components': [component_snapshot(ref) for ref in refs],
                        'source_bundle_slug': slug,
                        'official': bool(bundle.get('official')),
                    }
                else:
                    only = refs[0] if refs else {}
                    item = {
                        'id': new_id('sub'),
                        'title': bundle.get('title') or slug,
                        'url': feed_url,
                        'visible': True,
                        'trashed': False,
                        'created_at': now_iso(),
                        'color': only.get('color') or random_timeline_color(),
                        'author_name': only.get('author_name') or bundle.get('owner_acct') or '',
                        'author_acct': only.get('author_acct') or bundle.get('owner_acct') or '',
                        'source_bundle_slug': slug,
                        'official': bool(bundle.get('official')),
                    }
                user['subscriptions'].insert(0, item)
                sync_group_parent_component(user, item)
                user['updated_at'] = now_iso()
                save_user_fragment(store, session['acct'], calendars=True, subscriptions=True)
                status = 'added'
            else:
                existing['title'] = bundle.get('title') or slug
                existing['url'] = feed_url
                existing['source_bundle_slug'] = slug
                existing['official'] = bool(bundle.get('official'))
                if existing.get('kind') == 'bundle':
                    existing['components'] = [component_snapshot(ref) for ref in refs]
                existing['visible'] = True
                existing['trashed'] = False
                user['updated_at'] = now_iso()
                save_user_fragment(store, session['acct'], calendars=True, subscriptions=True)
                status = 'existing'
            self.redirect(f'/u/{urllib.parse.quote(session["acct"])}?subscribed={status}&title={urllib.parse.quote(bundle.get("title") or slug)}')
            return
        if path.startswith('/bundle/private/') and path.endswith('.ics'):
            parts = [part for part in path.split('/') if part]
            if len(parts) != 4:
                self.send_json(404, {'error': 'not_found'})
                return
            acct = urllib.parse.unquote(parts[2])
            sub_id = urllib.parse.unquote(parts[3][:-4])
            store = load_store()
            user = ensure_user(store, acct)
            item = find_subscription(user, sub_id)
            if not item or item.get('kind') != 'bundle':
                self.send_json(404, {'error': 'not_found'})
                return
            urls = resolve_subscription_urls(user, item)
            title = item.get('title') or sub_id
            data = merged_calendar_bytes(urls, title, f"Merged private timeline from {acct}", '-//TimeGrid//Merged Timeline//EN', store=store, session=self.current_session())
            headers = {'Content-Disposition': f'inline; filename="{slugify(title) or sub_id}.ics"'}
            self.send_bytes(200, data, headers={'Content-Type': 'text/calendar; charset=utf-8', **headers})
            return

        if path.startswith('/bundle/') and path.endswith('.ics'):
            slug = path[len('/bundle/'): -4]
            store = load_store()
            bundle = store['published'].get(slug)
            session = self.current_session()
            viewer_user = ensure_user(store, session['acct']) if session else None
            if not bundle:
                self.send_json(404, {'error': 'not_found'})
                return
            if not bundle_discoverable(bundle) and not user_has_bundle_subscription(viewer_user, slug):
                self.send_json(404, {'error': 'not_found'})
                return
            if not bundle_visible_to_session(bundle, session):
                self.send_json(403, {'error': 'forbidden'})
                return
            urls = bundle_urls(store, bundle, session)
            data = merged_calendar_bytes(urls, bundle.get('title') or slug, f"Published bundle from {APP_BASE_URL}/p/{slug}", '-//TimeGrid//Published Bundle//EN', store=store, session=session)
            headers = {'Content-Disposition': f'inline; filename="{slugify(bundle.get("title") or slug)}.ics"'}
            self.send_bytes(200, data, 'text/calendar; charset=utf-8', headers)
            return
        if path.startswith('/p/'):
            slug = path.split('/p/', 1)[1]
            store = load_store()
            bundle = store['published'].get(slug)
            session = self.current_session()
            viewer_user = ensure_user(store, session['acct']) if session else None
            if not bundle:
                body = not_found_page()
                print(f'NOTFOUND_DEBUG /p/{slug} len={len(body)}', flush=True)
                self.send_bytes(404, body)
                return
            if not bundle_discoverable(bundle) and not user_has_bundle_subscription(viewer_user, slug):
                body = not_found_page(message='This published timeline is only available to people who already subscribed.')
                print(f'NOTFOUND_DEBUG /p/{slug} retired len={len(body)}', flush=True)
                self.send_bytes(404, body)
                return
            if not bundle_visible_to_session(bundle, session):
                if session is None and bundle_visibility(bundle) != 'public':
                    self.redirect(f'/auth?next={urllib.parse.quote(path, safe="/?=&")}')
                else:
                    body = not_found_page()
                    print(f'NOTFOUND_DEBUG /p/{slug} visibility len={len(body)}', flush=True)
                    self.send_bytes(404, body)
                return
            self.send_bytes(200, published_page(bundle, bundle_urls(store, bundle, session)))
            return
        if path.startswith('/ics/') and path.endswith('.ics'):
            rest = path[len('/ics/'): -4]
            if '/' not in rest:
                self.send_json(404, {'error': 'not_found'})
                return
            acct, timeline_id = rest.split('/', 1)
            store = load_store()
            user = ensure_user(store, acct)
            timeline = find_timeline(user, timeline_id)
            if not timeline:
                self.send_json(404, {'error': 'not_found'})
                return
            headers = {'Content-Disposition': f'inline; filename="{slugify(timeline.get("title") or timeline_id)}.ics"'}
            self.send_bytes(200, timeline_to_ics(acct, timeline), 'text/calendar; charset=utf-8', headers)
            return
        if path.startswith('/u/'):
            parts = [part for part in path.split('/') if part]
            acct = parts[1] if len(parts) >= 2 else ''
            session = self.require_session()
            if session is None:
                return
            if not self.can_access_personal(acct, session):
                self.send_bytes(403, b'Forbidden')
                return
            if len(parts) == 2:
                self.send_bytes(200, page_shell(f'{acct} calendar', 'personal', 'personal-page', calendar_head()))
                return
            if len(parts) == 3 and parts[2] == 'creator':
                self.send_bytes(200, page_shell(f'{acct} creator page', 'creator', 'personal-page creator-page', calendar_head()))
                return
            if len(parts) == 3 and parts[2] == 'official':
                self.send_bytes(200, page_shell(f'{acct} official page', 'official', 'personal-page creator-page official-page', calendar_head()))
                return
            if len(parts) == 3 and parts[2] == 'archive':
                self.send_bytes(200, page_shell(f'{acct} archive page', 'archive', 'personal-page archive-page', calendar_head()))
                return
            if len(parts) == 4 and parts[2] == 'timelines' and parts[3] == 'new':
                self.send_bytes(200, timeline_page('Create timeline'))
                return
            if len(parts) == 4 and parts[2] == 'timelines':
                self.send_bytes(200, timeline_page('Edit timeline'))
                return

        if path == '/api/me':
            session = self.current_session()
            if session is None:
                self.send_json(401, {'authenticated': False})
                return
            store = load_store()
            user = ensure_user(store, session['acct'])
            self.send_json(200, {
                'authenticated': True,
                'acct': session['acct'],
                'display_name': session.get('display_name') or session['acct'],
                'avatar': session.get('avatar') or '',
                'role': session.get('role') or '',
                'auth_provider': session.get('auth_provider') or 'mastodon',
                'is_admin': self.is_admin(session),
                'personal_path': f'/u/{session["acct"]}',
                'notifications_unread': unread_notification_count(user),
                'mastodon_ready': bool((user.get('onboarding') or {}).get('mastodon_ready', True)),
            })
            return
        if path == '/api/auth/options':
            next_path = query.get('next', ['/'])[0]
            self.send_json(200, {
                'providers': [serialize_auth_provider(provider, next_path) for provider in configured_auth_providers()],
                'dual_account_model': False,
                'next': next_path,
                'supabase_auth_enabled': supabase_auth_enabled(),
            })
            return
        if path == '/api/notifications':
            session = self.require_session()
            if session is None:
                return
            store = load_store()
            user = ensure_user(store, session['acct'])
            items = [serialize_notification(item) for item in user.get('notifications', [])]
            self.send_json(200, {'items': items, 'unread': unread_notification_count(user)})
            return
        if path == '/api/community':
            store = load_store()
            session = self.current_session()
            viewer_user = ensure_user(store, session['acct']) if session else None
            needle = str(query.get('q', [''])[0]).strip().lower()
            items: list[dict[str, Any]] = []
            for target_user in store.get('users', {}).values():
                if not can_view_profile(viewer_user, target_user):
                    continue
                payload = serialize_public_profile(target_user, store, session, viewer_user)
                if not payload['published_count']:
                    continue
                if needle:
                    haystack = ' '.join([
                        payload.get('acct') or '',
                        payload.get('display_name') or '',
                        payload.get('bio') or '',
                        ' '.join(item.get('title') or '' for item in payload.get('published', [])),
                    ]).lower()
                    if needle not in haystack:
                        continue
                items.append(payload)
            items.sort(key=lambda item: (-(item.get('published_count') or 0), str(item.get('display_name') or '').lower()))
            self.send_json(200, {'items': items, 'q': needle})
            return
        if path.startswith('/api/community/'):
            acct = path.split('/api/community/', 1)[1].strip('/')
            store = load_store()
            session = self.current_session()
            viewer_user = ensure_user(store, session['acct']) if session else None
            target_user = store.get('users', {}).get(acct)
            if not target_user:
                self.send_json(404, {'error': 'not_found'})
                return
            if not can_view_profile(viewer_user, target_user):
                self.send_json(403, {'error': 'forbidden'})
                return
            payload = serialize_public_profile(target_user, store, session, viewer_user)
            self.send_json(200, payload)
            return
        if path == '/api/published':
            store = load_store()
            session = self.current_session()
            viewer_user = ensure_user(store, session['acct']) if session else None
            category = str(query.get('category', ['public'])[0]).strip().lower() or 'public'
            needle = str(query.get('q', [''])[0]).strip().lower()
            items = []
            for item in store['published'].values():
                if not bundle_discoverable(item):
                    continue
                visibility = bundle_visibility(item)
                if category == 'public' and visibility != 'public':
                    continue
                if category == 'invited' and visibility != 'invited':
                    continue
                if category == 'private' and visibility != 'private':
                    continue
                if not bundle_visible_to_session(item, session):
                    continue
                payload = serialize_bundle(item, store, session, viewer_user)
                if needle:
                    haystack = ' '.join([
                        str(payload.get('title') or ''),
                        str(payload.get('owner_acct') or ''),
                        ' '.join(payload.get('hashtags') or []),
                    ]).lower()
                    if needle not in haystack:
                        continue
                items.append(payload)
            bundles = sorted(items, key=lambda item: item.get('created_at', ''), reverse=True)
            self.send_json(200, {'items': bundles, 'category': category, 'q': needle})
            return
        if path.startswith('/api/published/') and path.endswith('/share-meta'):
            slug = path[len('/api/published/'): -len('/share-meta')].rstrip('/')
            store = load_store()
            session = self.current_session()
            bundle = store['published'].get(slug)
            if not bundle:
                self.send_json(404, {'error': 'not_found'}, headers=self.mastodon_cors_headers())
                return
            if not bundle_visible_to_session(bundle, session):
                self.send_json(403, {'error': 'forbidden'}, headers=self.mastodon_cors_headers())
                return
            hashtags = normalize_bundle_hashtags(bundle.get('hashtags'))
            self.send_json(200, {
                'slug': slug,
                'title': bundle.get('title') or slug,
                'share_url': f'{APP_BASE_URL}/p/{slug}',
                'hashtags': hashtags,
                'hashtag_text': ' '.join(f'#{tag}' for tag in hashtags),
            }, headers=self.mastodon_cors_headers())
            return
        if path.startswith('/api/published/'):
            slug = path.split('/api/published/', 1)[1]
            store = load_store()
            session = self.current_session()
            viewer_user = ensure_user(store, session['acct']) if session else None
            bundle = store['published'].get(slug)
            if not bundle:
                self.send_json(404, {'error': 'not_found'})
                return
            if not bundle_visible_to_session(bundle, session):
                self.send_json(403, {'error': 'forbidden'})
                return
            payload = serialize_bundle(bundle, store, session, viewer_user)
            urls = bundle_urls(store, bundle, session)
            payload['urls'] = urls
            payload['embed_url'] = build_embed_url(urls)
            self.send_json(200, payload)
            return
        if path.startswith('/api/creator/'):
            session = self.require_session()
            if session is None:
                return
            parts = [part for part in path.split('/') if part]
            if len(parts) != 3:
                self.send_json(404, {'error': 'not_found'})
                return
            acct = parts[2]
            if not self.can_access_personal(acct, session):
                self.send_json(403, {'error': 'forbidden'})
                return
            store = load_store()
            user = ensure_user(store, acct)
            self.send_json(200, build_workspace_payload(acct, user, store, session, mode='creator', is_admin=self.is_admin(session), calendar_id=query.get('calendar_id', [''])[0]))
            return
        if path.startswith('/api/archive/'):
            session = self.require_session()
            if session is None:
                return
            parts = [part for part in path.split('/') if part]
            if len(parts) != 3:
                self.send_json(404, {'error': 'not_found'})
                return
            acct = parts[2]
            if not self.can_access_personal(acct, session):
                self.send_json(403, {'error': 'forbidden'})
                return
            store = load_store()
            user = ensure_user(store, acct)
            self.send_json(200, build_workspace_payload(acct, user, store, session, mode='archive', is_admin=self.is_admin(session), calendar_id=query.get('calendar_id', [''])[0]))
            return
        if path.startswith('/api/personal/'):
            session = self.require_session()
            if session is None:
                return
            parts = [part for part in path.split('/') if part]
            if len(parts) < 3:
                self.send_json(404, {'error': 'not_found'})
                return
            acct = parts[2]
            if not self.can_access_personal(acct, session):
                self.send_json(403, {'error': 'forbidden'})
                return
            store = load_store()
            user = ensure_user(store, acct)
            if len(parts) == 3:
                self.send_json(200, build_workspace_payload(acct, user, store, session, mode='personal', is_admin=self.is_admin(session), calendar_id=query.get('calendar_id', [''])[0]))
                return
            if len(parts) == 6 and parts[3] == 'subscriptions' and parts[5] == 'source':
                item = find_subscription(user, parts[4])
                if not item or not item.get('url'):
                    self.send_json(404, {'error': 'not_found'})
                    return
                local = local_calendar_bytes(item['url'], store, session)
                if local is not None:
                    self.send_bytes(200, local, 'text/calendar; charset=utf-8')
                    return
                try:
                    resp = requests.get(item['url'], timeout=SOURCE_PROXY_TIMEOUT_SECONDS)
                    resp.raise_for_status()
                except Exception:
                    self.send_json(502, {'error': 'source_fetch_failed'})
                    return
                content_type = resp.headers.get('Content-Type') or 'text/calendar; charset=utf-8'
                self.send_bytes(200, resp.text.encode('utf-8'), content_type)
                return
            if len(parts) == 5 and parts[3] == 'exports' and parts[4].startswith('current.'):
                snapshot = build_personal_export_snapshot(acct, user, store, calendar_id=query.get('calendar_id', [''])[0])
                ext = parts[4].split('.', 1)[1].lower()
                if ext == 'ics':
                    filename = f'{acct}-timegrid-export.ics'
                    self.send_bytes(200, snapshot['ics_bytes'], 'text/calendar; charset=utf-8', headers={
                        'Content-Disposition': f'attachment; filename="{filename}"',
                        **dynamic_calendar_headers(snapshot['ics_bytes']),
                    })
                    return
                if ext == 'csv':
                    filename = f'{acct}-timegrid-export.csv'
                    self.send_bytes(200, export_csv_bytes(snapshot), 'text/csv; charset=utf-8', headers={
                        'Content-Disposition': f'attachment; filename="{filename}"',
                        'Cache-Control': 'no-store',
                    })
                    return
                if ext == 'pdf':
                    view = (query.get('view', ['year'])[0] or 'year').strip().lower()
                    if view not in {'year', 'month', 'week'}:
                        view = 'year'
                    try:
                        year = int((query.get('year', [str(datetime.now(timezone.utc).year)])[0] or '').strip())
                    except ValueError:
                        year = datetime.now(timezone.utc).year
                    filename = f'{acct}-timegrid-export-{view}-{year}.pdf'
                    self.send_bytes(200, export_pdf_bytes(snapshot, year, view), 'application/pdf', headers={
                        'Content-Disposition': f'attachment; filename="{filename}"',
                        'Cache-Control': 'no-store',
                    })
                    return
                self.send_json(404, {'error': 'not_found'})
                return
            if len(parts) == 5 and parts[3] == 'timelines':
                timeline = find_timeline(user, parts[4])
                if not timeline:
                    self.send_json(404, {'error': 'not_found'})
                    return
                payload = build_wrapper_timeline(acct, user, timeline) if timeline.get('kind') == 'wrapper' else serialize_timeline(acct, timeline)
                if payload is None:
                    self.send_json(404, {'error': 'not_found'})
                    return
                self.send_json(200, {'timeline': payload, 'user': {'acct': acct, 'display_name': user.get('display_name') or acct}})
                return

        self.send_json(404, {'error': 'not_found'})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith('/auth/provider/') and path.endswith('/callback'):
            provider_id = path.split('/auth/provider/', 1)[1].rsplit('/callback', 1)[0].strip('/')
            self.finish_external_auth(provider_id, self.parse_form_body())
            return

        if path == '/auth/logout':
            session = self.current_session()
            if session:
                for key, value in list(sessions.items()):
                    if value is session:
                        sessions.pop(key, None)
                        break
                save_auth_state()
            self.send_json(200, {'ok': True}, headers={'Set-Cookie': clear_cookie_header()})
            return

        if path == '/api/auth/email/signup':
            if not email_auth_enabled():
                self.send_json(404, {'error': 'not_found'})
                return
            body = self.parse_json_body()
            email = str(body.get('email') or '').strip().lower()
            password = str(body.get('password') or '')
            display_name = str(body.get('display_name') or email.split('@', 1)[0] if email else '').strip()
            next_path = safe_post_auth_path(str(body.get('next') or '/'), display_name or 'user')
            if not email or '@' not in email:
                self.send_json(400, {'error': 'valid email required'})
                return
            if len(password) < 8:
                self.send_json(400, {'error': 'password must be at least 8 characters'})
                return
            resp = requests.post(
                f'{SUPABASE_URL}/auth/v1/signup',
                headers=supabase_auth_headers(),
                json={
                    'email': email,
                    'password': password,
                    'data': {'display_name': display_name, 'full_name': display_name},
                    'redirect_to': supabase_redirect_url(next_path),
                },
                timeout=20,
            )
            if resp.status_code >= 400:
                self.send_json(resp.status_code if resp.status_code < 500 else 502, {'error': (resp.json().get('msg') if resp.text else '') or 'signup_failed'})
                return
            data = resp.json()
            session_data = data.get('session') or {}
            access_token = session_data.get('access_token')
            if not access_token:
                self.send_json(200, {'ok': True, 'verification_required': True, 'message': 'Check your email to confirm your account, then sign in.'})
                return
            session_id, session, user = create_session_from_supabase_access_token(access_token, provider='email')
            self.send_json(200, {
                'ok': True,
                'user': {'acct': user['acct'], 'display_name': user.get('display_name') or user['acct']},
                'next': safe_post_auth_path(str(body.get('next') or '/'), user['acct']),
            }, headers={'Set-Cookie': make_cookie_header(session_id)})
            return

        if path == '/api/auth/email/login':
            if not email_auth_enabled():
                self.send_json(404, {'error': 'not_found'})
                return
            body = self.parse_json_body()
            email = str(body.get('email') or '').strip().lower()
            password = str(body.get('password') or '')
            resp = requests.post(
                f'{SUPABASE_URL}/auth/v1/token?grant_type=password',
                headers=supabase_auth_headers(),
                json={'email': email, 'password': password},
                timeout=20,
            )
            if resp.status_code >= 400:
                message = 'login_failed'
                try:
                    payload = resp.json()
                    message = payload.get('error_description') or payload.get('msg') or payload.get('error') or message
                except Exception:
                    pass
                self.send_json(401, {'error': message})
                return
            data = resp.json()
            access_token = data.get('access_token')
            if not access_token:
                self.send_json(401, {'error': 'login_failed'})
                return
            session_id, session, user = create_session_from_supabase_access_token(access_token, provider='email')
            self.send_json(200, {
                'ok': True,
                'user': {'acct': user['acct'], 'display_name': user.get('display_name') or user['acct']},
                'next': safe_post_auth_path(str(body.get('next') or '/'), user['acct']),
            }, headers={'Set-Cookie': make_cookie_header(session_id)})
            return

        if path == '/api/auth/supabase/session':
            if not (email_auth_enabled() or external_auth_enabled()):
                self.send_json(404, {'error': 'not_found'})
                return
            body = self.parse_json_body()
            access_token = str(body.get('access_token') or '').strip()
            provider = str(body.get('provider') or '').strip().lower()
            if not access_token:
                self.send_json(400, {'error': 'access_token_required'})
                return
            try:
                session_id, session, user = create_session_from_supabase_access_token(access_token, provider='' if provider in {'', 'supabase', 'oauth'} else provider)
            except Exception:
                self.send_json(401, {'error': 'invalid_supabase_session'})
                return
            self.send_json(200, {
                'ok': True,
                'user': {'acct': user['acct'], 'display_name': user.get('display_name') or user['acct']},
                'next': safe_post_auth_path(str(body.get('next') or '/'), user['acct']),
            }, headers={'Set-Cookie': make_cookie_header(session_id)})
            return

        if path == '/api/dev/test-login':
            if not ENABLE_TEST_LOGIN:
                self.send_json(404, {'error': 'not_found'})
                return
            body = self.parse_json_body()
            role = 'admin' if bool(body.get('admin')) else ''
            session_id, _session, user = create_test_login_session(
                str(body.get('acct') or 'sample1'),
                str(body.get('display_name') or ''),
                role=role,
            )
            self.send_json(200, {
                'ok': True,
                'user': {'acct': user['acct'], 'display_name': user.get('display_name') or user['acct']},
                'next': safe_post_auth_path(str(body.get('next') or '/'), user['acct']),
            }, headers={'Set-Cookie': make_cookie_header(session_id)})
            return

        if path == '/api/notifications':
            session = self.require_session()
            if session is None:
                return
            store = load_store()
            user = ensure_user(store, session['acct'])
            body = self.parse_json_body()
            title = str(body.get('title') or '').strip()[:160]
            notice_body = str(body.get('body') or '').strip()[:600]
            href = str(body.get('href') or '').strip()[:240]
            if not title:
                self.send_json(400, {'error': 'title_required'})
                return
            add_notification(
                user,
                kind='workspace_notice',
                title=title,
                body=notice_body,
                actor_acct=session['acct'],
                href=href,
            )
            user['updated_at'] = now_iso()
            save_user_fragment(store, session['acct'], notifications=True)
            item = serialize_notification(user.get('notifications', [])[0])
            self.send_json(200, {'ok': True, 'item': item, 'unread': unread_notification_count(user)})
            return

        if path == '/api/notifications/read':
            session = self.require_session()
            if session is None:
                return
            store = load_store()
            user = ensure_user(store, session['acct'])
            body = self.parse_json_body()
            target_id = str(body.get('id') or '').strip()
            changed = False
            for item in user.get('notifications', []):
                if target_id and item.get('id') != target_id:
                    continue
                if not item.get('read_at'):
                    item['read_at'] = now_iso()
                    changed = True
                if target_id:
                    break
            if changed:
                user['updated_at'] = now_iso()
                save_user_fragment(store, session['acct'], notifications=True)
            self.send_json(200, {'ok': True, 'unread': unread_notification_count(user)})
            return

        if path.startswith('/api/published/') and path.endswith('/subscribe'):
            session = self.require_session()
            if session is None:
                return
            parts = [part for part in path.split('/') if part]
            if len(parts) != 4:
                self.send_json(404, {'error': 'not_found'})
                return
            slug = parts[2]
            store = load_store()
            bundle = store.get('published', {}).get(slug)
            if not bundle:
                self.send_json(404, {'error': 'not_found'})
                return
            viewer_user = ensure_user(store, session['acct'])
            if not bundle_discoverable(bundle) and not user_has_bundle_subscription(viewer_user, slug):
                self.send_json(404, {'error': 'not_found'})
                return
            if not bundle_visible_to_session(bundle, session):
                self.send_json(403, {'error': 'forbidden'})
                return
            acct = session['acct']
            user = viewer_user
            feed_url = bundle_feed_url(slug)
            refs = bundle_component_snapshots(store, bundle, session)
            existing = next((item for item in user.get('subscriptions', []) if item.get('source_bundle_slug') == slug and not item.get('trashed')), None)
            if existing is None and len(refs) <= 1:
                existing = next((item for item in user.get('subscriptions', []) if item.get('url') == feed_url and not item.get('trashed')), None)
            if existing is None:
                if len(refs) > 1:
                    user['subscriptions'].insert(0, {
                        'id': new_id('sub'),
                        'title': bundle.get('title') or slug,
                        'url': feed_url,
                        'visible': True,
                        'trashed': False,
                        'created_at': now_iso(),
                        'color': pick_merge_color(refs),
                        'kind': 'bundle',
                        'components': [component_snapshot(ref) for ref in refs],
                        'source_bundle_slug': slug,
                        'official': bool(bundle.get('official')),
                    })
                else:
                    only = refs[0] if refs else {}
                    user['subscriptions'].insert(0, {
                        'id': new_id('sub'),
                        'title': bundle.get('title') or slug,
                        'url': feed_url,
                        'visible': True,
                        'trashed': False,
                        'created_at': now_iso(),
                        'color': only.get('color') or random_timeline_color(),
                        'author_name': only.get('author_name') or bundle.get('owner_acct') or '',
                        'author_acct': only.get('author_acct') or bundle.get('owner_acct') or '',
                        'source_bundle_slug': slug,
                        'official': bool(bundle.get('official')),
                    })
            else:
                existing['title'] = bundle.get('title') or slug
                existing['url'] = feed_url
                existing['source_bundle_slug'] = slug
                existing['official'] = bool(bundle.get('official'))
                if existing.get('kind') == 'bundle':
                    existing['components'] = [component_snapshot(ref) for ref in refs]
                existing['visible'] = True
                existing['trashed'] = False
            user['updated_at'] = now_iso()
            save_user_fragment(store, acct, calendars=True, subscriptions=True)
            self.send_json(200, {'ok': True, 'subscribed': True})
            return

        if path.startswith('/api/personal/'):
            session = self.require_session()
            if session is None:
                return
            parts = [part for part in path.split('/') if part]
            if len(parts) < 4:
                self.send_json(404, {'error': 'not_found'})
                return
            acct = parts[2]
            if not self.can_access_personal(acct, session):
                self.send_json(403, {'error': 'forbidden'})
                return
            body = self.parse_json_body()
            store = load_store()
            user = ensure_user(store, acct)

            if parts[3] == 'calendars' and len(parts) == 4:
                workspace = str(body.get('workspace') or 'personal').strip().lower()
                if workspace not in {'personal', 'creator'}:
                    self.send_json(400, {'error': 'invalid workspace'})
                    return
                title = str(body.get('title') or '').strip() or 'New calendar'
                calendar_id = new_id('cal')
                existing_titles = {str(item.get('title') or '').strip().lower() for item in ensure_user_calendars(user) if item.get('workspace') == workspace and not item.get('archived')}
                base_title = title
                counter = 2
                while title.lower() in existing_titles:
                    title = f'{base_title} {counter}'
                    counter += 1
                calendar_record = {
                    'id': calendar_id,
                    'workspace': workspace,
                    'title': title,
                    'color': str(body.get('color') or '').strip() or random_timeline_color(),
                    'position': len([item for item in ensure_user_calendars(user) if item.get('workspace') == workspace]),
                    'is_default': False,
                    'archived': False,
                    'created_at': now_iso(),
                    'updated_at': now_iso(),
                }
                user.setdefault('calendars', []).append(calendar_record)
                user['updated_at'] = now_iso()
                save_user_fragment(store, acct, calendars=True)
                self.send_json(201, {'calendar': calendar_record, 'calendars': [item for item in ensure_user_calendars(user) if item.get('workspace') == workspace and not item.get('archived')]})
                return


            if parts[3] == 'exports' and len(parts) == 4:
                mode = str(body.get('mode') or 'dynamic').strip().lower()
                if mode not in {'dynamic', 'static'}:
                    self.send_json(400, {'error': 'invalid_export_mode'})
                    return
                calendar_id = resolve_calendar_id(user, str(body.get('calendar_id') or ''), 'personal')
                snapshot = build_personal_export_snapshot(acct, user, store, calendar_id=calendar_id)
                token_info = ensure_export_record(store, acct, mode=mode, snapshot=snapshot, calendar_id=calendar_id)
                user['updated_at'] = now_iso()
                save_user_fragment(store, acct, exports=True)
                self.send_json(200, {
                    'ok': True,
                    'mode': mode,
                    'recommended': 'dynamic',
                    'url': export_token_url(token_info['token']),
                    'title': snapshot['metadata']['title'],
                })
                return
            if parts[3] == 'subscriptions' and len(parts) == 4:
                url = str(body.get('url', '')).strip()
                title = str(body.get('title', '')).strip() or url
                if not url.startswith('http://') and not url.startswith('https://'):
                    self.send_json(400, {'error': 'url must start with http:// or https://'})
                    return
                workspace = str(body.get('workspace') or 'personal').strip().lower()
                if workspace not in {'personal', 'creator'}:
                    workspace = 'personal'
                calendar_id = resolve_calendar_id(user, str(body.get('calendar_id') or ''), workspace)
                grouped_in = str(body.get('grouped_in') or '').strip()
                item = {
                    'id': new_id('sub'),
                    'title': title,
                    'url': url,
                    'visible': bool(body.get('visible', True)),
                    'trashed': False,
                    'created_at': now_iso(),
                    'color': str(body.get('color') or '').strip() or random_timeline_color(),
                    'calendar_id': calendar_id,
                    'author_name': user.get('display_name') or acct,
                    'author_acct': acct,
                    'official': bool(body.get('official')),
                    'source_code': str(body.get('source_code') or '').strip()[:80],
                    'source_format': str(body.get('source_format') or '').strip().lower()[:24],
                    'hashtags': normalize_bundle_hashtags(body.get('hashtags')),
                    'description': str(body.get('description') or '').strip(),
                    'workspace': workspace,
                }
                if grouped_in and find_subscription(user, grouped_in):
                    item['grouped_in'] = grouped_in
                user['subscriptions'].insert(0, item)
                user['updated_at'] = now_iso()
                save_user_fragment(store, acct, subscriptions=True)
                self.send_json(201, serialize_subscription(acct, item, user))
                return

            if parts[3] == 'timelines' and len(parts) == 4:
                title = str(body.get('title', '')).strip() or 'Untitled timeline'
                description = str(body.get('description', '')).strip()
                events = body.get('events') or []
                workspace = str(body.get('workspace') or 'personal').strip().lower()
                if workspace not in {'personal', 'creator'}:
                    workspace = 'personal'
                calendar_id = resolve_calendar_id(user, str(body.get('calendar_id') or ''), workspace)
                timeline = {
                    'id': new_id('tl'),
                    'title': title,
                    'description': description,
                    'events': events,
                    'created_at': now_iso(),
                    'updated_at': now_iso(),
                    'color': random_timeline_color(),
                    'calendar_id': calendar_id,
                    'workspace': workspace,
                }
                sync_timeline_subscription(acct, user, timeline)
                sub = find_subscription(user, timeline.get('subscription_id', ''))
                if sub:
                    sub['calendar_id'] = calendar_id
                    sub['workspace'] = workspace
                user['timelines'].insert(0, timeline)
                user['updated_at'] = now_iso()
                save_user_fragment(store, acct, subscriptions=True, timelines=True)
                self.send_json(201, {'timeline': serialize_timeline(acct, timeline), 'subscription': serialize_subscription(acct, find_subscription(user, timeline['subscription_id']), user)})
                return

            if len(parts) == 6 and parts[3] == 'subscriptions' and parts[5] == 'editor':
                item = find_subscription(user, parts[4])
                if not item:
                    self.send_json(404, {'error': 'not_found'})
                    return
                try:
                    timeline, target = ensure_subscription_editor(acct, user, item)
                except KeyError:
                    self.send_json(404, {'error': 'not_found'})
                    return
                user['updated_at'] = now_iso()
                save_user_fragment(store, acct, subscriptions=True, timelines=True)
                self.send_json(200, {
                    'timeline': serialize_timeline(acct, timeline) if timeline.get('kind') != 'wrapper' else build_wrapper_timeline(acct, user, timeline),
                    'subscription': serialize_subscription(acct, target, user),
                    'edit_url': timeline_edit_url(acct, timeline['id']),
                })
                return

            if parts[3] == 'merge' and len(parts) == 4:
                title = str(body.get('title', '')).strip() or 'Merged timeline'
                subscription_ids = [str(x) for x in body.get('subscription_ids', []) if str(x)]
                unique_ids = []
                for sub_id in subscription_ids:
                    if sub_id not in unique_ids:
                        unique_ids.append(sub_id)
                selected = [find_subscription(user, sub_id) for sub_id in unique_ids]
                selected = [item for item in selected if item and not item.get('trashed') and not item.get('grouped_in')]
                leafs: list[dict[str, Any]] = []
                trashed_bundles: list[dict[str, Any]] = []
                for item in selected:
                    if item.get('kind') == 'bundle':
                        trashed_bundles.append(item)
                    for child in leaf_subscriptions(user, item):
                        if child not in leafs:
                            leafs.append(child)
                if len(leafs) < 2:
                    self.send_json(400, {'error': 'select at least two timelines to merge'})
                    return
                merged = {
                    'id': new_id('sub'),
                    'title': title,
                    'url': '',
                    'visible': any(item.get('visible') for item in leafs),
                    'trashed': False,
                    'created_at': now_iso(),
                    'kind': 'bundle',
                    'components': [component_snapshot(item) for item in leafs],
                    'color': pick_merge_color(leafs),
                }
                user['subscriptions'].insert(0, merged)
                for item in leafs:
                    item['grouped_in'] = merged['id']
                    item['trashed'] = False
                for item in trashed_bundles:
                    for child in grouped_children(user, item.get('id', '')):
                        if child in leafs:
                            child['grouped_in'] = merged['id']
                    item['components'] = []
                    item['trashed'] = True
                    item['visible'] = False
                user['updated_at'] = now_iso()
                save_user_fragment(store, acct, subscriptions=True)
                payload = serialize_subscription(acct, merged, user)
                payload['components'] = [serialize_subscription(acct, child, user) for child in component_entries(user, merged)]
                payload['component_count'] = len(payload['components'])
                self.send_json(201, {'subscription': payload})
                return

            if parts[3] == 'published' and len(parts) == 4:
                title = str(body.get('title', '')).strip() or 'Published calendar'
                subscription_ids = [str(x) for x in body.get('subscription_ids', []) if str(x)]
                valid_ids = [sub_id for sub_id in subscription_ids if find_subscription(user, sub_id) and not find_subscription(user, sub_id).get('trashed')]
                if not valid_ids:
                    self.send_json(400, {'error': 'select at least one subscription'})
                    return
                first_sub = find_subscription(user, valid_ids[0])
                calendar_id = resolve_calendar_id(user, str(body.get('calendar_id') or (first_sub or {}).get('calendar_id') or ''), 'creator')
                slug_base = slugify(title)
                slug = slug_base
                while slug in store['published']:
                    slug = f'{slug_base}-{secrets.token_hex(2)}'
                invited = [normalize_invite_token(str(x)) for x in (body.get('invited') or []) if normalize_invite_token(str(x))]
                visibility = bundle_visibility({'visibility': body.get('visibility')})
                bundle = {
                    'id': new_id('pub'),
                    'slug': slug,
                    'title': title,
                    'owner_acct': acct,
                    'calendar_id': calendar_id,
                    'subscription_ids': valid_ids,
                    'subscription_count': len(valid_ids),
                    'created_at': now_iso(),
                    'share_url': f'{APP_BASE_URL}/p/{slug}',
                    'visibility': visibility,
                    'invited': invited,
                    'hashtags': normalize_bundle_hashtags(body.get('hashtags')),
                    'allow_hard_copy': False,
                    'archived': False,
                    'listed': True,
                    'owner_detached': False,
                }
                store['published'][slug] = bundle
                user['published'].insert(0, bundle)
                notify_bundle_invites(store, bundle, acct)
                user['updated_at'] = now_iso()
                save_store(store)
                self.send_json(201, serialize_bundle(bundle, store))
                return

            if len(parts) == 6 and parts[3] == 'subscriptions' and parts[5] == 'separate':
                item = find_subscription(user, parts[4])
                if not item or item.get('kind') != 'bundle':
                    self.send_json(404, {'error': 'not_found'})
                    return
                children = component_entries(user, item)
                selected_ids = [str(x) for x in body.get('subscription_ids', []) if str(x)]
                if selected_ids:
                    selected_children = [child for child in children if child.get('id') in selected_ids]
                else:
                    selected_children = children
                if not selected_children:
                    self.send_json(400, {'error': 'select at least one internal subscription'})
                    return
                selected_keys = {component_identity(child) for child in selected_children}
                component_color_map = {component_identity(ref): ref.get('color') or random_timeline_color() for ref in item.get('components', []) or []}
                restored_children: list[dict[str, Any]] = []
                for child in selected_children:
                    child_key = component_identity(child)
                    existing_child = find_subscription(user, child.get('id', '')) if child.get('id') else None
                    if existing_child and existing_child.get('grouped_in') == item.get('id'):
                        existing_child['grouped_in'] = ''
                        restored_color = component_color_map.get(child_key) or existing_child.get('color') or random_timeline_color()
                        existing_child['color'] = restored_color
                        restored_children.append(existing_child)
                    else:
                        restored_children.append(materialize_component_subscription(user, {
                            'title': child.get('title') or '',
                            'url': child.get('url') or '',
                            'color': component_color_map.get(child_key) or child.get('color') or random_timeline_color(),
                            'author_name': child.get('author_name') or '',
                            'author_acct': child.get('author_acct') or '',
                        }, visible=True))
                trash_original = bool(body.get('trash_original', False))
                if trash_original:
                    for child in children:
                        if child.get('id') and child.get('grouped_in') == item.get('id'):
                            child['grouped_in'] = ''
                    item['trashed'] = True
                    item['visible'] = False
                    item['components'] = []
                else:
                    item['components'] = [ref for ref in item.get('components', []) if component_identity(ref) not in selected_keys]
                    if not item['components']:
                        item['trashed'] = True
                        item['visible'] = False
                user['updated_at'] = now_iso()
                save_user_fragment(store, acct, subscriptions=True)
                self.send_json(200, {'restored': [serialize_subscription(acct, sub, user, store, session) for sub in restored_children], 'original': serialize_subscription(acct, item, user, store, session)})
                return

            if len(parts) == 6 and parts[3] == 'subscriptions' and parts[5] in {'trash', 'restore'}:
                item = find_subscription(user, parts[4])
                if not item:
                    self.send_json(404, {'error': 'not_found'})
                    return
                item['trashed'] = parts[5] == 'trash'
                if item.get('kind') == 'bundle':
                    children = grouped_children(user, item.get('id', ''))
                    if item['trashed']:
                        item['visible'] = False
                        for child in children:
                            child['visible'] = False
                    else:
                        for child in children:
                            child['trashed'] = False
                elif item['trashed']:
                    item['visible'] = False
                sync_group_parent_component(user, item)
                user['updated_at'] = now_iso()
                save_user_fragment(store, acct, subscriptions=True)
                self.send_json(200, serialize_subscription(acct, item, user))
                return

        self.send_json(404, {'error': 'not_found'})

    def do_PATCH(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith('/api/personal/'):
            session = self.require_session()
            if session is None:
                return
            parts = [part for part in path.split('/') if part]
            if len(parts) < 5:
                self.send_json(404, {'error': 'not_found'})
                return
            acct = parts[2]
            if not self.can_access_personal(acct, session):
                self.send_json(403, {'error': 'forbidden'})
                return
            body = self.parse_json_body()
            store = load_store()
            user = ensure_user(store, acct)

            if parts[3] == 'calendars':
                calendar = next((item for item in ensure_user_calendars(user) if item.get('id') == parts[4]), None)
                if not calendar:
                    self.send_json(404, {'error': 'not_found'})
                    return
                workspace = str(calendar.get('workspace') or 'personal')
                if 'title' in body:
                    title = str(body.get('title') or '').strip()
                    if title:
                        calendar['title'] = title
                if 'color' in body:
                    color = str(body.get('color') or '').strip()
                    if color:
                        calendar['color'] = color
                if 'position' in body:
                    workspace_calendars = [item for item in ensure_user_calendars(user) if item.get('workspace') == workspace and not item.get('archived')]
                    move_item_to_position(workspace_calendars, calendar, body.get('position'))
                    position_by_id = {item.get('id'): item.get('position') for item in workspace_calendars}
                    for item in user.get('calendars', []):
                        if item.get('workspace') == workspace and item.get('id') in position_by_id:
                            item['position'] = position_by_id[item.get('id')]
                calendar['updated_at'] = now_iso()
                user['updated_at'] = now_iso()
                save_user_fragment(store, acct, calendars=True)
                self.send_json(200, {'calendar': calendar, 'calendars': [item for item in ensure_user_calendars(user) if item.get('workspace') == workspace and not item.get('archived')]})
                return

            if parts[3] == 'published':
                slug = parts[4]
                bundle = store.get('published', {}).get(slug)
                if not bundle or bundle.get('owner_acct') != acct:
                    self.send_json(404, {'error': 'not_found'})
                    return
                if bundle_owner_detached(bundle):
                    self.send_json(404, {'error': 'not_found'})
                    return
                if 'title' in body:
                    bundle['title'] = str(body.get('title') or '').strip() or bundle.get('title') or 'Published calendar'
                if 'visibility' in body:
                    bundle['visibility'] = bundle_visibility({'visibility': body.get('visibility')})
                if 'invited' in body:
                    raw_invited = body.get('invited') or []
                    if isinstance(raw_invited, str):
                        raw_invited = raw_invited.split(',')
                    bundle['invited'] = [normalize_invite_token(str(item)) for item in raw_invited if normalize_invite_token(str(item))]
                if 'hashtags' in body:
                    bundle['hashtags'] = normalize_bundle_hashtags(body.get('hashtags'))
                if 'listed' in body:
                    bundle['listed'] = bool(body.get('listed'))
                if 'archived' in body:
                    bundle['archived'] = bool(body.get('archived'))
                    if bundle['archived']:
                        bundle['listed'] = False
                if bundle_listed(bundle):
                    bundle['archived'] = False
                bundle['allow_hard_copy'] = False
                bundle['subscription_count'] = len(bundle.get('subscription_ids', []))
                for user_bundle in user.get('published', []):
                    if user_bundle.get('slug') == slug:
                        user_bundle['visibility'] = bundle.get('visibility', 'public')
                        user_bundle['invited'] = list(bundle.get('invited', []))
                        user_bundle['title'] = bundle.get('title', user_bundle.get('title'))
                        user_bundle['hashtags'] = list(normalize_bundle_hashtags(bundle.get('hashtags')))
                        user_bundle['allow_hard_copy'] = False
                        user_bundle['listed'] = bundle_listed(bundle)
                        user_bundle['archived'] = bundle_archived(bundle)
                        user_bundle['owner_detached'] = bundle_owner_detached(bundle)
                        break
                notify_bundle_invites(store, bundle, acct)
                user['updated_at'] = now_iso()
                save_store(store)
                self.send_json(200, serialize_bundle(bundle, store, session, user))
                return

            if parts[3] == 'subscriptions':
                item = find_subscription(user, parts[4])
                if not item:
                    self.send_json(404, {'error': 'not_found'})
                    return
                if 'title' in body and not item.get('owned_timeline_id'):
                    item['title'] = str(body['title']).strip() or item['url']
                if 'url' in body and not item.get('owned_timeline_id'):
                    url = str(body['url']).strip()
                    if not url.startswith('http://') and not url.startswith('https://'):
                        self.send_json(400, {'error': 'url must start with http:// or https://'})
                        return
                    item['url'] = url
                if 'visible' in body:
                    item['visible'] = bool(body['visible'])
                    if item.get('kind') == 'bundle':
                        for child in grouped_children(user, item.get('id', '')):
                            child['visible'] = item['visible']
                if 'source_code' in body:
                    item['source_code'] = str(body.get('source_code') or '').strip()[:80]
                if 'source_format' in body:
                    item['source_format'] = str(body.get('source_format') or '').strip().lower()[:24]
                if 'hashtags' in body:
                    item['hashtags'] = normalize_bundle_hashtags(body.get('hashtags'))
                if 'description' in body:
                    item['description'] = str(body.get('description') or '').strip()
                if 'workspace' in body:
                    workspace = str(body.get('workspace') or 'personal').strip().lower()
                    if workspace not in {'personal', 'creator', 'archive'}:
                        self.send_json(400, {'error': 'invalid workspace'})
                        return
                    if workspace == 'archive':
                        refs = subscription_related_refs(user, item.get('id', ''))
                        if not refs.get('published_slugs'):
                            self.send_json(400, {'error': 'only published timelines can be archived'})
                            return
                    item['workspace'] = workspace
                    item['creator_archived'] = workspace == 'archive'
                if 'creator_archived' in body:
                    item['workspace'] = 'archive' if bool(body['creator_archived']) else 'creator'
                    item['creator_archived'] = bool(body['creator_archived'])
                if 'calendar_id' in body:
                    workspace = str(body.get('workspace') or item.get('workspace') or 'personal').strip().lower()
                    if workspace not in {'personal', 'creator'}:
                        workspace = 'creator' if workspace == 'archive' else 'personal'
                    calendar_id = resolve_calendar_id(user, str(body.get('calendar_id') or ''), workspace)
                    move_subscription_to_calendar(user, item, calendar_id, workspace)
                if 'color' in body:
                    color = str(body['color']).strip()
                    if color:
                        item['color'] = color
                        timeline_id = item.get('owned_timeline_id')
                        timeline = find_timeline(user, timeline_id) if timeline_id else None
                        if timeline:
                            timeline['color'] = color
                if 'trashed' in body:
                    item['trashed'] = bool(body['trashed'])
                    if item['trashed']:
                        item['visible'] = False
                if 'position' in body:
                    move_subscription_to_position(user, item, body.get('position'))
                user['updated_at'] = now_iso()
                save_user_fragment(store, acct, subscriptions=True, timelines=True)
                self.send_json(200, serialize_subscription(acct, item, user))
                return

            if parts[3] == 'timelines':
                timeline = find_timeline(user, parts[4])
                if not timeline:
                    self.send_json(404, {'error': 'not_found'})
                    return
                if timeline.get('kind') == 'wrapper':
                    target = find_subscription(user, timeline.get('target_subscription_id', ''))
                    if not target or target.get('kind') != 'bundle':
                        self.send_json(404, {'error': 'not_found'})
                        return
                    if 'title' in body:
                        title = str(body['title']).strip() or 'Merged timeline'
                        timeline['title'] = title
                        target['title'] = title
                    if 'description' in body:
                        timeline['description'] = str(body['description']).strip()
                    if 'color' in body:
                        color = str(body['color']).strip()
                        if color:
                            timeline['color'] = color
                            target['color'] = color
                    overlay_timeline, _overlay_sub = ensure_bundle_overlay_timeline(acct, user, target)
                    managed_ids = {sub.get('owned_timeline_id') for sub in leaf_subscriptions(user, target) if sub.get('owned_timeline_id')}
                    managed_ids = {item for item in managed_ids if item}
                    incoming = body.get('events') or []
                    grouped: dict[str, list[dict[str, Any]]] = {timeline_id: [] for timeline_id in managed_ids}
                    for event in incoming:
                        if event.get('editable') is False:
                            continue
                        source_timeline_id = str(event.get('source_timeline_id') or overlay_timeline['id'])
                        if source_timeline_id not in managed_ids:
                            source_timeline_id = overlay_timeline['id']
                        grouped.setdefault(source_timeline_id, []).append(strip_editor_event(event))
                    for timeline_id in managed_ids:
                        child = find_timeline(user, timeline_id)
                        if child and child.get('kind') != 'wrapper':
                            child['events'] = grouped.get(timeline_id, [])
                            child['updated_at'] = now_iso()
                            sync_timeline_subscription(acct, user, child)
                    timeline['overlay_timeline_id'] = overlay_timeline['id']
                    timeline['updated_at'] = now_iso()
                    user['updated_at'] = now_iso()
                    save_user_fragment(store, acct, subscriptions=True, timelines=True)
                    payload = build_wrapper_timeline(acct, user, timeline)
                    self.send_json(200, {'timeline': payload, 'subscription': serialize_subscription(acct, target, user)})
                    return
                if 'title' in body:
                    timeline['title'] = str(body['title']).strip() or 'Untitled timeline'
                if 'description' in body:
                    timeline['description'] = str(body['description']).strip()
                if 'color' in body:
                    color = str(body['color']).strip()
                    if color:
                        timeline['color'] = color
                if 'events' in body:
                    timeline['events'] = [strip_editor_event(item) for item in (body.get('events') or [])]
                if 'calendar_id' in body:
                    workspace = str(body.get('workspace') or timeline.get('workspace') or 'personal').strip().lower()
                    if workspace not in {'personal', 'creator'}:
                        workspace = 'personal'
                    timeline['calendar_id'] = resolve_calendar_id(user, str(body.get('calendar_id') or ''), workspace)
                    timeline['workspace'] = workspace
                timeline['updated_at'] = now_iso()
                sub = sync_timeline_subscription(acct, user, timeline)
                if sub and timeline.get('color'):
                    sub['color'] = timeline.get('color')
                if sub and timeline.get('calendar_id'):
                    move_subscription_to_calendar(user, sub, timeline.get('calendar_id'), timeline.get('workspace') or 'personal')
                user['updated_at'] = now_iso()
                save_user_fragment(store, acct, subscriptions=True, timelines=True)
                sub = find_subscription(user, timeline['subscription_id'])
                self.send_json(200, {'timeline': serialize_timeline(acct, timeline), 'subscription': serialize_subscription(acct, sub, user) if sub else None})
                return

        self.send_json(404, {'error': 'not_found'})

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith('/api/personal/'):
            session = self.require_session()
            if session is None:
                return
            parts = [part for part in path.split('/') if part]
            if len(parts) == 5 and parts[3] == 'published':
                acct = parts[2]
                if not self.can_access_personal(acct, session):
                    self.send_json(403, {'error': 'forbidden'})
                    return
                store = load_store()
                user = ensure_user(store, acct)
                slug = parts[4]
                bundle = store.get('published', {}).get(slug)
                if not bundle or bundle.get('owner_acct') != acct or bundle_owner_detached(bundle):
                    self.send_json(404, {'error': 'not_found'})
                    return
                mode = (urllib.parse.parse_qs(parsed.query).get('mode', ['permanent'])[0] or 'permanent').lower()
                if mode == 'archive':
                    bundle['archived'] = True
                    bundle['listed'] = False
                    for user_bundle in user.get('published', []):
                        if user_bundle.get('slug') == slug:
                            user_bundle['archived'] = True
                            user_bundle['listed'] = False
                            break
                    response = {'ok': True, 'mode': 'archive'}
                elif mode == 'remove':
                    bundle['listed'] = False
                    bundle['archived'] = False
                    for user_bundle in user.get('published', []):
                        if user_bundle.get('slug') == slug:
                            user_bundle['listed'] = False
                            user_bundle['archived'] = False
                            break
                    response = {'ok': True, 'mode': 'remove'}
                else:
                    bundle['owner_detached'] = True
                    bundle['listed'] = False
                    bundle['archived'] = False
                    user['published'] = [entry for entry in user.get('published', []) if entry.get('slug') != slug]
                    response = {'ok': True, 'mode': 'permanent'}
                user['updated_at'] = now_iso()
                save_store(store)
                self.send_json(200, response)
                return
            if len(parts) == 5 and parts[3] == 'subscriptions':
                acct = parts[2]
                if not self.can_access_personal(acct, session):
                    self.send_json(403, {'error': 'forbidden'})
                    return
                store = load_store()
                user = ensure_user(store, acct)
                item = find_subscription(user, parts[4])
                if not item:
                    self.send_json(404, {'error': 'not_found'})
                    return
                mode = (urllib.parse.parse_qs(parsed.query).get('mode', ['detach'])[0] or 'detach').lower()
                refs = subscription_related_refs(user, item.get('id', ''))
                detached = False
                if mode == 'permanent':
                    purge_subscription(store, user, acct, item)
                    user['updated_at'] = now_iso()
                    save_store(store)
                else:
                    item['detached'] = True
                    item['visible'] = False
                    item['trashed'] = False
                    detached = True
                    user['updated_at'] = now_iso()
                    save_user_fragment(store, acct, subscriptions=True, timelines=True)
                self.send_json(200, {'ok': True, 'mode': 'detach' if detached else mode, 'references': refs})
                return
        self.send_json(404, {'error': 'not_found'})


def main() -> None:
    ensure_store()
    load_auth_state()
    if STORAGE is not None:
        load_store()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(f'Listening on http://127.0.0.1:{PORT}')
    server.serve_forever()


if __name__ == '__main__':
    main()
