const root = document.getElementById('app');
function pageFromPath(pathname = window.location.pathname) {
  const fallback = document.body.dataset.page || 'landing';
  const parts = pathname.split('/').filter(Boolean);
  if (!parts.length) return fallback;
  if (parts[0] === 'auth') return 'auth';
  if (parts[0] === 'community' || parts[0] === 'people') return parts[1] ? 'community-profile' : 'community';
  if (parts[0] === 'published') return parts[1] ? 'published-detail' : 'published';
  if (parts[0] === 'u') {
    if (parts[2] === 'creator') return 'creator';
    if (parts[2] === 'official') return 'official';
    if (parts[2] === 'archive') return 'archive';
    if (parts[2] === 'timelines') return 'timeline';
    return 'personal';
  }
  return fallback;
}

let page = pageFromPath();
document.body.dataset.page = page;
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

const state = {
  me: null,
  personal: null,
  published: [],
  publishedDetail: null,
  publishedQuery: '',
  message: '',
  error: '',
  publishOpen: false,
  importOpen: false,
  actionMenuOpen: false,
  routeSwitching: false,
  exportOpen: false,
  exportPdfView: 'month',
  exportYear: new Date().getFullYear(),
  exportLinkMode: 'dynamic',
  exportLinkUrl: '',
  activeCalendarByMode: {},
  mergeToolOpen: false,
  mergeToolSourceId: '',
  colorMenuOpenId: '',
  publishedCategory: 'public',
  publishedSort: 'newest',
  publishedManageSlug: '',
  signInChooserOpen: false,
  notices: [],
  noticesUnread: 0,
  noticesOpen: false,
  noticeToast: null,
  community: [],
  communityQuery: '',
  communityProfile: null,
  authOptions: [],
  authMode: 'signup',
  authSignupStatus: '',
  authSignupError: '',
  authEmail: '',
  authDisplayName: '',
  draggedSubscriptionId: '',
  draggedCalendarId: '',
  dragSubscriptionInsertId: '',
  dragSubscriptionInsertAfter: false,
  dragCalendarInsertId: '',
  dragCalendarInsertAfter: false,
  workspaceLoadSeq: 0,
  workspaceBusyLabel: '',
  timeline: null,
  draftEvent: null,
  selectedEventId: null,
  selectedOccurrence: null,
  calendar: null,
  readonlyCalendar: null,
  readonlyLoadToken: 0,
  readonlyRendererLoadPromise: null,
  timelineView: '',
  timelineDate: null,
  timelineHint: '',
  recurrenceConversion: null,
  eventDetail: null,
  eventEditModal: null,
};

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function timelineColor(value) {
  const color = String(value || '').trim();
  return /^#[0-9a-f]{6}$/i.test(color) ? color : '#146c73';
}

function calendarViewStorageKey(scope) {
  return `timegrid_calendar_view:${scope || 'default'}`;
}

function savedCalendarView(scope) {
  try {
    return window.localStorage.getItem(calendarViewStorageKey(scope)) || '';
  } catch (_error) {
    return '';
  }
}

function saveCalendarView(scope, view) {
  if (!view) return;
  try {
    window.localStorage.setItem(calendarViewStorageKey(scope), view);
  } catch (_error) {}
}

function responsiveCalendarGridHeight() {
  const width = window.innerWidth || 1280;
  const height = window.innerHeight || 800;
  if (width < 640) return Math.max(620, Math.min(760, height - 120));
  if (width > 1600) return Math.max(760, Math.min(980, height - 260));
  return Math.max(700, Math.min(900, height - 220));
}

async function api(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    credentials: 'same-origin',
    cache: 'no-store',
    ...options,
  });
  if (res.status === 204) return null;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(data?.error || data?.message || `Request failed (${res.status})`);
  return data;
}

async function loadMe() {
  state.me = await api('/api/me').catch(() => ({ authenticated: false }));
  if (state.me?.authenticated) saveRecentAccount(state.me);
}

function currentAcct() {
  const parts = window.location.pathname.split('/').filter(Boolean);
  return parts[1] || state.me?.acct || '';
}

function currentTimelineId() {
  const parts = window.location.pathname.split('/').filter(Boolean);
  if (parts[2] !== 'timelines') return null;
  return parts[3] === 'new' ? null : parts[3];
}

function closeEventDetail() {
  state.eventDetail = null;
  render();
}

function formatEventDateRange(startIso, endIso) {
  if (!startIso) return '';
  const start = new Date(startIso);
  const end = endIso ? new Date(endIso) : null;
  if (Number.isNaN(start.getTime())) return '';
  const sameDay = end && !Number.isNaN(end.getTime()) && start.toDateString() === end.toDateString();
  const dayText = new Intl.DateTimeFormat(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(start);
  const startTime = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(start);
  if (!end || Number.isNaN(end.getTime())) return `${dayText} · ${startTime}`;
  const endTime = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(end);
  if (sameDay) return `${dayText} · ${startTime} - ${endTime}`;
  const endDayText = new Intl.DateTimeFormat(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(end);
  return `${dayText} · ${startTime} -> ${endDayText} · ${endTime}`;
}

function eventDetailModal() {
  const detail = state.eventDetail;
  if (!detail) return '';
  const canEditHere = page === 'timeline' && detail.editable !== false;
  const sourceLabel = detail.source_title ? `<div class="meta-card"><span class="meta-label">Source</span><strong>${escapeHtml(detail.source_title)}</strong></div>` : '';
  const locationLabel = detail.location ? `<div class="meta-card"><span class="meta-label">Location</span><strong>${escapeHtml(detail.location)}</strong></div>` : '';
  const linkLabel = detail.url ? `<div class="meta-card"><span class="meta-label">Link</span><a href="${escapeHtml(detail.url)}" target="_blank" rel="noreferrer">${escapeHtml(detail.url)}</a></div>` : '';
  return `
    <div class="modal-backdrop" data-action="close-event-detail">
      <div class="modal event-detail-modal" onclick="event.stopPropagation()">
        <div class="modal-header">
          <div>
            <div class="eyebrow">Event details</div>
            <h2>${escapeHtml(detail.title || 'Untitled event')}</h2>
            <p class="muted">${escapeHtml(formatEventDateRange(detail.start, detail.end))}</p>
          </div>
          <button class="modal-close" data-action="close-event-detail" aria-label="Close event details">×</button>
        </div>
        <div class="meta-list event-detail-grid">
          ${sourceLabel}
          ${locationLabel}
          ${linkLabel}
          ${detail.description ? `<div class="meta-card event-detail-description"><span class="meta-label">Notes</span><div>${escapeHtml(detail.description).replaceAll('\n', '<br />')}</div></div>` : ''}
        </div>
        <div class="modal-actions">
          ${canEditHere ? '<button class="primary" data-action="event-detail-edit">Edit this event</button>' : ''}
          <button data-action="close-event-detail">Close</button>
        </div>
      </div>
    </div>`;
}

function currentWorkspaceMode() {
  if (page === 'creator' || page === 'official') return 'creator';
  if (page === 'archive') return 'archive';
  return 'personal';
}

async function loadWorkspace(modeOverride = '', calendarIdOverride = '') {
  const mode = modeOverride || currentWorkspaceMode();
  const loadSeq = ++state.workspaceLoadSeq;
  const activeCalendarId = calendarIdOverride || state.activeCalendarByMode[mode] || '';
  const endpoint = page === 'official' || page === 'creator'
    ? `/api/creator/${encodeURIComponent(currentAcct())}`
    : mode === 'creator'
      ? `/api/creator/${encodeURIComponent(currentAcct())}`
      : mode === 'archive'
      ? `/api/archive/${encodeURIComponent(currentAcct())}`
      : `/api/personal/${encodeURIComponent(currentAcct())}`;
  const params = new URLSearchParams();
  if (activeCalendarId) params.set('calendar_id', activeCalendarId);
  const data = await api(`${endpoint}${params.toString() ? `?${params.toString()}` : ''}`);
  if (loadSeq !== state.workspaceLoadSeq) return false;
  state.personal = data;
  if (state.personal?.active_calendar_id) state.activeCalendarByMode[mode] = state.personal.active_calendar_id;
  if (currentAcct() === 'official' && page !== 'official') {
    state.personal.subscriptions = [];
    state.personal.visible_sources = [];
    state.personal.published = [];
    state.personal.archived_published = [];
    state.personal.publish_candidates = [];
    state.personal.timelines = [];
    state.personal.trash = [];
  } else if (page === 'creator') {
    state.personal.subscriptions = (state.personal.subscriptions || []).filter((item) => !item.official);
    const visibleIds = new Set((state.personal.subscriptions || []).map((item) => item.id));
    state.personal.visible_sources = (state.personal.visible_sources || []).filter((item) => visibleIds.has(item.id));
    state.personal.published = (state.personal.published || []).filter((item) => !item.official);
    state.personal.publish_candidates = (state.personal.publish_candidates || state.personal.subscriptions || []).filter((item) => !item.official && !item.trashed && !item.grouped_in);
  }
  return true;
}

function updateSubscriptionColorState(subscriptionId, color) {
  if (!state.personal) return;
  (state.personal.subscriptions || []).forEach((item) => {
    if (item.id === subscriptionId) item.color = color;
    (item.components || []).forEach((child) => {
      if (child.id === subscriptionId) child.color = color;
    });
  });
  (state.personal.trash || []).forEach((item) => {
    if (item.id === subscriptionId) item.color = color;
  });
  (state.personal.visible_sources || []).forEach((item) => {
    if (item.id === subscriptionId || item.subscription_id === subscriptionId) item.color = color;
  });
  (state.personal.timelines || []).forEach((item) => {
    if (item.subscription_id === subscriptionId) item.color = color;
  });
}

function updateSubscriptionColorDom(subscriptionId, color) {
  if (!subscriptionId || !window.CSS?.escape) return;
  document.querySelectorAll(`[data-id="${CSS.escape(subscriptionId)}"]`).forEach((node) => {
    const card = node.closest('.sub-card, .trash-card');
    if (card) card.style.setProperty('--timeline-color', color);
    if (node.matches('[data-action="toggle-color-menu"]')) node.style.setProperty('--swatch-color', color);
  });
  document.querySelectorAll(`[data-action="subscription-color-choice"][data-id="${CSS.escape(subscriptionId)}"]`).forEach((button) => {
    const active = (button.dataset.color || '').toLowerCase() === color.toLowerCase();
    button.classList.toggle('active', active);
    button.setAttribute('aria-checked', String(active));
  });
}

async function refreshWorkspaceCalendarColors() {
  if (!['personal', 'creator', 'archive'].includes(page)) return;
  if (!state.personal?.visible_sources?.length) return;
  await initReadonlyCalendar('personal-calendar', state.personal.visible_sources, 'personal-calendar-hint');
}

async function loadPublished() {
  const params = new URLSearchParams({
    category: state.publishedCategory || 'public',
  });
  if (state.publishedQuery.trim()) params.set('q', state.publishedQuery.trim());
  const data = await api(`/api/published?${params.toString()}`);
  state.published = data.items || [];
}

function currentPublishedSlug() {
  return document.body.dataset.publishedSlug || window.location.pathname.split('/').filter(Boolean)[1] || '';
}

function currentProfileAcct() {
  return document.body.dataset.profileAcct || window.location.pathname.split('/').filter(Boolean)[1] || '';
}

async function loadPublishedDetail() {
  state.publishedDetail = await api(`/api/published/${encodeURIComponent(currentPublishedSlug())}`);
}

async function loadNotifications() {
  if (!state.me?.authenticated) {
    state.notices = [];
    state.noticesUnread = 0;
    return;
  }
  const data = await api('/api/notifications');
  state.notices = data.items || [];
  state.noticesUnread = Number(data.unread || 0);
}

async function markNotificationsRead(id = '') {
  if (!state.me?.authenticated) return;
  const data = await api('/api/notifications/read', {
    method: 'POST',
    body: JSON.stringify(id ? { id } : {}),
  });
  state.noticesUnread = Number(data?.unread || 0);
  state.notices = (state.notices || []).map((item) => (id && item.id !== id) ? item : ({ ...item, read_at: item.read_at || new Date().toISOString() }));
}

async function loadCommunity() {
  const params = new URLSearchParams();
  if (state.communityQuery.trim()) params.set('q', state.communityQuery.trim());
  const data = await api(`/api/community${params.toString() ? `?${params.toString()}` : ''}`);
  state.community = data.items || [];
}

async function loadCommunityProfile() {
  state.communityProfile = await api(`/api/community/${encodeURIComponent(currentProfileAcct())}`);
}

async function loadAuthOptions() {
  const nextPath = authNextPath();
  const data = await api(`/api/auth/options?next=${encodeURIComponent(nextPath)}`);
  state.authOptions = data.providers || [];
}

async function submitAuthSignupIntent(payload) {
  const data = await api('/api/auth/signup-intents', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return data.intent || null;
}

async function submitEmailSignup(payload) {
  const data = await api('/api/auth/email/signup', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return data.user || null;
}

async function submitEmailLogin(payload) {
  const data = await api('/api/auth/email/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return data.user || null;
}

async function completeSupabaseSession(accessToken, provider = 'supabase') {
  const data = await api('/api/auth/supabase/session', {
    method: 'POST',
    body: JSON.stringify({ access_token: accessToken, provider, next: authNextPath() }),
  });
  return data;
}

async function handleSupabaseRedirect() {
  if (page !== 'auth') return false;
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  const accessToken = hash.get('access_token');
  if (!accessToken) return false;
  state.authSignupStatus = 'Finishing sign in...';
  renderAuthHub();
  const data = await completeSupabaseSession(accessToken);
  history.replaceState({}, '', `${window.location.pathname}${window.location.search}`);
  window.location.href = data.next || (data.user?.acct ? `/u/${encodeURIComponent(data.user.acct)}` : '/');
  return true;
}

async function fetchWrapperSourceEvents(source) {
  const res = await fetch(source.fetch_url, { credentials: 'same-origin' });
  if (!res.ok) throw new Error('Could not load source calendar.');
  const text = await res.text();
  const parsed = parseIcsFile(text, source.title || 'Imported calendar');
  return (parsed.events || []).map((event) => ({
    ...event,
    source_subscription_id: source.subscription_id,
    source_timeline_id: '',
    source_title: source.title || 'Read-only source',
    source_color: source.color || '',
    editable: false,
  }));
}

async function hydrateWrapperTimeline() {
  if (!state.timeline || state.timeline.kind !== 'wrapper') return;
  const external = state.timeline.external_sources || [];
  if (!external.length) return;
  const chunks = await Promise.all(external.map(async (source) => {
    try {
      return await fetchWrapperSourceEvents(source);
    } catch (_error) {
      return [];
    }
  }));
  state.timeline.events = [...(state.timeline.events || []), ...chunks.flat()];
}

async function loadTimeline() {
  const id = currentTimelineId();
  const origin = currentTimelineOrigin();
  const requestedCalendarId = new URLSearchParams(window.location.search).get('calendar_id') || '';
  if (requestedCalendarId) state.activeCalendarByMode[origin] = requestedCalendarId;
  state.timelineView = '';
  state.timelineDate = null;
  if (!id) {
    await loadWorkspace(origin);
    state.timeline = {
      id: null,
      title: 'New timeline',
      description: '',
      events: [],
      created_at: null,
      updated_at: null,
      ics_url: '',
      edit_url: window.location.pathname,
      calendar_id: state.personal?.active_calendar_id || requestedCalendarId,
    };
    return;
  }
  const data = await api(`/api/personal/${encodeURIComponent(currentAcct())}/timelines/${encodeURIComponent(id)}`);
  state.timeline = data.timeline;
  if (state.timeline?.calendar_id) state.activeCalendarByMode[origin] = state.timeline.calendar_id;
  await loadWorkspace(origin);
  await hydrateWrapperTimeline();
}

function noticeTitleAndBody(message, isError = false) {
  const text = String(message || '').trim();
  if (text.length <= 90) return { title: text, body: '' };
  return { title: isError ? 'TimeGrid error' : 'TimeGrid update', body: text };
}

function scheduleNoticeToastDismiss() {
  window.clearTimeout(window.__tgNoticeToastExitTimer);
  window.clearTimeout(window.__tgNoticeToastClearTimer);
  window.__tgNoticeToastExitTimer = window.setTimeout(() => {
    if (!state.noticeToast) return;
    state.noticeToast = { ...state.noticeToast, exiting: true };
    refreshNoticeUi();
  }, 5000);
  window.__tgNoticeToastClearTimer = window.setTimeout(() => {
    state.noticeToast = null;
    refreshNoticeUi();
  }, 5400);
}

function recordNotice(message, isError = false) {
  if (!state.me?.authenticated || !message) return;
  const { title, body } = noticeTitleAndBody(message, isError);
  const href = window.location.pathname;
  const localId = `local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const localItem = {
    id: localId,
    kind: isError ? 'workspace_error' : 'workspace_notice',
    title,
    body,
    href,
    actor_acct: state.me?.acct || '',
    created_at: new Date().toISOString(),
    read_at: '',
  };
  state.notices = [localItem, ...(state.notices || [])].slice(0, 60);
  state.noticesUnread = Number(state.noticesUnread || 0) + 1;
  api('/api/notifications', {
    method: 'POST',
    body: JSON.stringify({ title, body, href }),
  }).then((data) => {
    if (data?.item) {
      state.notices = (state.notices || []).map((item) => item.id === localId ? data.item : item);
    }
    if (Number.isFinite(Number(data?.unread))) state.noticesUnread = Number(data.unread);
    updateNoticeBadgeDom();
  }).catch(() => {});
}

function updateNoticeBadgeDom() {
  document.querySelectorAll('.notification-button__badge').forEach((badge) => {
    badge.textContent = String(state.noticesUnread || '');
    badge.setAttribute('aria-label', `${state.noticesUnread || 0} unread notifications`);
    badge.classList.toggle('hidden', !state.noticesUnread);
  });
}

function refreshNoticeUi() {
  if (page === 'timeline' && document.getElementById('timeline-overlay-shell')) {
    renderTimelineEditorOnly();
    return;
  }
  render();
}

function setBanner(message = '', error = '') {
  state.message = '';
  state.error = '';
  const text = message || error;
  if (text) {
    state.noticeToast = { message: text, error: Boolean(error), exiting: false };
    scheduleNoticeToastDismiss();
  }
  if (text) recordNotice(text, Boolean(error));
  refreshNoticeUi();
}

function showToast(message) {
  setBanner(message, '');
}

function closeShareSheet() {
  document.querySelector('.share-sheet')?.remove();
}

function openShareSheet(shareUrl, title = '') {
  closeShareSheet();
  const overlay = document.createElement('div');
  overlay.className = 'modal-backdrop share-sheet';
  overlay.innerHTML = `
    <div class="modal" onclick="event.stopPropagation()">
      <div>
        <div class="eyebrow">Share</div>
        <h2>How do you want to share this timetable?</h2>
        <p class="muted">Choose Mastodon compose or copy the share link.</p>
      </div>
      <div class="modal-actions">
        <button class="primary" data-action="share-mastodon">Share to Mastodon</button>
        <button data-action="share-copy">Copy link</button>
        <button data-action="close-share-sheet">Cancel</button>
      </div>
    </div>`;
  overlay.addEventListener('click', closeShareSheet);
  document.body.appendChild(overlay);
  overlay.querySelector('[data-action="close-share-sheet"]')?.addEventListener('click', closeShareSheet);
  overlay.querySelector('[data-action="share-mastodon"]')?.addEventListener('click', () => {
    window.open(`https://social.time-grid.org/share?text=${encodeURIComponent(`${title} ${shareUrl}`.trim())}`, '_blank', 'noopener');
    closeShareSheet();
  });
  overlay.querySelector('[data-action="share-copy"]')?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      showToast('Copied');
      closeShareSheet();
    } catch (_error) {
      showToast('Copy failed');
    }
  });
}

function loginHref(nextPath = window.location.pathname) {
  return `/auth?next=${encodeURIComponent(nextPath)}`;
}

function mastodonLoginHref(nextPath = window.location.pathname) {
  return `/auth/login?next=${encodeURIComponent(nextPath)}`;
}

function mastodonSignupHref() {
  return 'https://social.time-grid.org/auth/sign_up';
}

function readRecentAccounts() {
  try {
    return JSON.parse(window.localStorage.getItem('timegrid_recent_accounts') || '[]').filter((item) => item && item.acct);
  } catch (_error) {
    return [];
  }
}

function saveRecentAccount(account) {
  if (!account?.acct) return;
  const recent = readRecentAccounts().filter((item) => item.acct !== account.acct);
  recent.unshift({ acct: account.acct, display_name: account.display_name || account.acct, avatar: account.avatar || '' });
  window.localStorage.setItem('timegrid_recent_accounts', JSON.stringify(recent.slice(0, 6)));
}

function signInChooserModal() {
  if (!state.signInChooserOpen) return '';
  const recent = readRecentAccounts();
  return `
    <div class="modal-backdrop" data-action="close-signin-chooser">
      <div class="modal" onclick="event.stopPropagation()">
        <div class="modal-header">
          <div>
            <div class="eyebrow">Sign in</div>
            <h2>Choose an account</h2>
            <p class="muted">Every sign-in now goes through Mastodon OAuth again. Choose a recent account to continue, or start a fresh sign-in.</p>
          </div>
          <button class="modal-close" data-action="close-signin-chooser" aria-label="Close sign in chooser">×</button>
        </div>
        ${recent.length ? `<div class="check-list">${recent.map((item) => `<button class="account-choice" data-action="signin-recent" data-acct="${escapeHtml(item.acct)}"><span><strong>${escapeHtml(item.display_name || item.acct)}</strong><span class="muted">@${escapeHtml(item.acct)}</span></span><span class="muted">Continue</span></button>`).join('')}</div>` : '<div class="empty">No recently used accounts on this device yet.</div>'}
        <div class="modal-actions">
          <a class="button primary" href="${mastodonLoginHref('/')}">Continue with Mastodon</a>
          <button data-action="close-signin-chooser">Cancel</button>
        </div>
      </div>
    </div>`;
}

function authNextPath() {
  return document.body.dataset.authNext || '/';
}

function authProviderCard({ provider, label, copy, badge = '', actionHtml = '' }) {
  return `
    <article class="auth-provider ${provider ? `provider-${escapeHtml(provider)}` : ''}">
      <div class="auth-provider-top">
        <strong>${escapeHtml(label)}</strong>
        ${badge ? `<span class="auth-provider-badge">${escapeHtml(badge)}</span>` : ''}
      </div>
      <p class="muted">${escapeHtml(copy)}</p>
      ${actionHtml}
    </article>`;
}

function mastodonProvisioningBanner() {
  if (!state.me?.authenticated || state.me?.mastodon_ready !== false) return '';
  return `
    <div class="banner">
      <strong>TimeGrid account ready.</strong>
      Your calendar account works now, but the linked Mastodon account is still being provisioned.
      You can keep building calendars while we finish the social account setup.
    </div>`;
}

function noticeToastMarkup() {
  const notice = state.noticeToast;
  if (!notice?.message) return '';
  const classes = ['notice-toast'];
  if (notice.error) classes.push('error');
  if (notice.exiting) classes.push('exiting');
  return `<div class="${classes.join(' ')}" role="status">${escapeHtml(notice.message)}</div>`;
}

function notificationsButton() {
  if (!state.me?.authenticated) return '';
  return `
    <div class="notification-anchor">
      <button class="notification-button" data-action="open-notices" aria-label="Open Notification Center">
        <span class="notification-button__icon" aria-hidden="true">🔔</span>
        ${state.noticesUnread ? `<span class="notification-button__badge" aria-label="${escapeHtml(String(state.noticesUnread))} unread notifications">${escapeHtml(String(state.noticesUnread))}</span>` : ''}
      </button>
      ${noticeToastMarkup()}
    </div>`;
}

function notificationsModal() {
  if (!state.noticesOpen || !state.me?.authenticated) return '';
  return `
    <div class="modal-backdrop" data-action="close-notices">
      <div class="modal" onclick="event.stopPropagation()">
        <div class="modal-header">
          <div>
            <div class="eyebrow">Notification Center</div>
            <h2>Updates for you</h2>
            <p class="muted">Invites and other TimeGrid activity appear here.</p>
          </div>
          <button class="modal-close" data-action="close-notices" aria-label="Close notices">×</button>
        </div>
        <div class="check-list">
          ${state.notices.length ? state.notices.map((item) => `
            <article class="check-row ${item.read_at ? '' : 'notice-unread'}">
              <div class="check-copy">
                <strong>${escapeHtml(item.title)}</strong>
                ${item.body ? `<div class="muted">${escapeHtml(item.body)}</div>` : ''}
                <div class="muted">${new Date(item.created_at || '').toLocaleString()}</div>
                <div class="modal-actions">
                  ${item.href ? `<a class="button primary" href="${escapeHtml(item.href)}">Open</a>` : ''}
                  ${item.read_at ? '' : `<button data-action="mark-notice-read" data-id="${escapeHtml(item.id)}">Mark read</button>`}
                </div>
              </div>
            </article>
          `).join('') : '<div class="empty">No notices yet.</div>'}
        </div>
        <div class="modal-actions">
          <button data-action="mark-all-read">Mark all read</button>
          <button data-action="close-notices">Close</button>
        </div>
      </div>
    </div>`;
}

async function logout() {
  await api('/auth/logout', { method: 'POST' });
  window.location.href = '/';
}

function bindNoticeActions(renderFn) {
  document.querySelector('[data-action="open-notices"]')?.addEventListener('click', async () => {
    state.noticesOpen = true;
    renderFn();
  });
  document.querySelectorAll('[data-action="close-notices"]').forEach((node) => node.addEventListener('click', () => {
    state.noticesOpen = false;
    renderFn();
  }));
  document.querySelectorAll('[data-action="mark-notice-read"]').forEach((node) => node.addEventListener('click', async () => {
    await markNotificationsRead(node.dataset.id || '');
    renderFn();
  }));
  document.querySelector('[data-action="mark-all-read"]')?.addEventListener('click', async () => {
    await markNotificationsRead();
    renderFn();
  });
}

function goToMastodonHome() {
  window.location.href = 'https://social.time-grid.org/home';
}


function exportDownloadUrl(kind) {
  const base = `/api/personal/${encodeURIComponent(currentAcct())}/exports/current.${kind}`;
  const calendarId = state.personal?.active_calendar_id || '';
  if (kind !== 'pdf') return calendarId ? `${base}?calendar_id=${encodeURIComponent(calendarId)}` : base;
  const params = new URLSearchParams({
    view: 'month',
    year: String(state.exportYear || new Date().getFullYear()),
  });
  if (calendarId) params.set('calendar_id', calendarId);
  return `${base}?${params.toString()}`;
}

function exportModal() {
  if (!state.exportOpen || currentWorkspaceMode() !== 'personal') return '';
  const calendars = state.personal?.calendars || [];
  const activeCalendarId = state.personal?.active_calendar_id || calendars[0]?.id || '';
  return `
    <div class="modal-backdrop" data-action="close-export">
      <div class="modal" onclick="event.stopPropagation()">
        <div class="modal-header">
          <div>
            <div class="eyebrow">Export</div>
            <h2>Export personal calendar</h2>
            <p class="muted">Export only the visible timelines from your personal page. Dynamic iCal link is recommended.</p>
          </div>
          <button class="modal-close" data-action="close-export" aria-label="Close export">×</button>
        </div>
        <div class="check-list export-check-list">
          <article class="check-row export-card export-card--compact">
            <div class="check-copy">
              <strong>Calendar</strong>
              <div class="muted">Choose which calendar folder to export.</div>
              <select data-action="export-calendar">
                ${calendars.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === activeCalendarId ? 'selected' : ''}>${escapeHtml(item.title || 'Calendar')}</option>`).join('')}
              </select>
            </div>
          </article>
          <article class="check-row export-card">
            <div class="check-copy">
              <strong>iCal link <span class="eyebrow">Recommended</span></strong>
              <div class="muted">Use in Apple Calendar or Google Calendar subscription. Static is frozen; dynamic updates when your visible personal page changes.</div>
              <div class="modal-actions export-link-actions">
                <div class="export-mode-switch" role="radiogroup" aria-label="Export link type">
                  <label class="${state.exportLinkMode === 'dynamic' ? 'active' : ''}"><input type="radio" name="export-link-mode" value="dynamic" ${state.exportLinkMode === 'dynamic' ? 'checked' : ''} /> <span>Dynamic</span></label>
                  <label class="${state.exportLinkMode === 'static' ? 'active' : ''}"><input type="radio" name="export-link-mode" value="static" ${state.exportLinkMode === 'static' ? 'checked' : ''} /> <span>Static</span></label>
                </div>
                <button class="primary" data-action="generate-export-link">Generate link</button>
              </div>
              ${state.exportLinkUrl ? `<div class="meta-list"><input value="${escapeHtml(state.exportLinkUrl)}" readonly data-export-link-value /><div class="modal-actions"><button data-action="copy-export-link">Copy link</button><a class="button" href="${escapeHtml(state.exportLinkUrl)}" target="_blank" rel="noreferrer">Open link</a></div></div>` : ''}
            </div>
          </article>
          <article class="check-row export-card export-card--compact">
            <div class="check-copy">
              <div class="export-row-heading">
                <strong>File export</strong>
                <div class="modal-actions export-inline-actions">
                  <button class="primary" data-action="download-export-ics">Download iCal file</button>
                  <button data-action="download-export-csv">Download CSV file</button>
                </div>
              </div>
            </div>
          </article>
          <article class="check-row export-card export-card--compact">
            <div class="check-copy">
              <div class="export-row-heading">
                <strong>PDF</strong>
                <div class="modal-actions export-inline-actions export-pdf-actions">
                  <label>Year
                    <input type="number" min="2000" max="2100" value="${escapeHtml(state.exportYear)}" data-action="export-year" />
                  </label>
                  <button class="primary" data-action="download-export-pdf">Download Month PDF</button>
                </div>
              </div>
            </div>
          </article>
        </div>
      </div>
    </div>`;
}

function bindExportActions() {
  document.querySelector('[data-action="open-export"]')?.addEventListener('click', () => {
    state.exportOpen = true;
    render();
  });
  document.querySelectorAll('[data-action="close-export"]').forEach((node) => node.addEventListener('click', () => {
    state.exportOpen = false;
    render();
  }));
  document.querySelector('[data-action="download-export-ics"]')?.addEventListener('click', () => {
    window.open(exportDownloadUrl('ics'), '_blank', 'noopener');
  });
  document.querySelector('[data-action="download-export-csv"]')?.addEventListener('click', () => {
    window.open(exportDownloadUrl('csv'), '_blank', 'noopener');
  });
  document.querySelector('[data-action="download-export-pdf"]')?.addEventListener('click', () => {
    window.open(exportDownloadUrl('pdf'), '_blank', 'noopener');
  });
  document.querySelector('[data-action="export-calendar"]')?.addEventListener('change', async (event) => {
    state.activeCalendarByMode.personal = event.target.value || '';
    state.exportLinkUrl = '';
    await loadWorkspace();
    state.exportOpen = true;
    render();
  });
  document.querySelector('[data-action="export-pdf-view"]')?.addEventListener('change', (event) => {
    state.exportPdfView = 'month';
  });
  document.querySelector('[data-action="export-year"]')?.addEventListener('change', (event) => {
    const year = Number(event.target.value);
    state.exportYear = Number.isFinite(year) ? year : new Date().getFullYear();
  });
  document.querySelectorAll('input[name="export-link-mode"]').forEach((node) => node.addEventListener('change', (event) => {
    state.exportLinkMode = event.target.value || 'dynamic';
  }));
  document.querySelector('[data-action="generate-export-link"]')?.addEventListener('click', async () => {
    try {
      const data = await api(`/api/personal/${encodeURIComponent(currentAcct())}/exports`, {
        method: 'POST',
        body: JSON.stringify({ mode: state.exportLinkMode || 'dynamic', calendar_id: state.personal?.active_calendar_id || '' }),
      });
      state.exportLinkUrl = data.url || '';
      showToast(`${data.mode === 'dynamic' ? 'Dynamic' : 'Static'} link ready`);
      render();
    } catch (error) {
      setBanner('', error.message || 'Could not create export link');
    }
  });
  document.querySelector('[data-action="copy-export-link"]')?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(state.exportLinkUrl || '');
      showToast('Copied');
    } catch (_error) {
      showToast('Copy failed');
    }
  });
}

function currentTopSection() {
  if (page === 'community' || page === 'community-profile') return 'community';
  if (page === 'published' || page === 'published-detail' || page === 'published-embed') return 'published';
  if (page === 'official' && currentAcct() === 'official') return 'official';
  if (page === 'timeline') return currentTimelineOrigin();
  if (page === 'creator') return 'creator';
  if (page === 'archive') return 'archive';
  return 'personal';
}

function currentTimelineOrigin() {
  const raw = new URLSearchParams(window.location.search).get('from') || window.sessionStorage.getItem('timegrid_timeline_origin') || 'personal';
  return raw === 'creator' ? 'creator' : 'personal';
}

function rememberTimelineOrigin(origin = currentTopSection()) {
  const value = origin === 'creator' ? 'creator' : 'personal';
  window.sessionStorage.setItem('timegrid_timeline_origin', value);
  return value;
}

function withTimelineOrigin(url, origin = currentTopSection()) {
  if (!url || url === '#') return url;
  try {
    const parsed = new URL(url, window.location.origin);
    parsed.searchParams.set('from', rememberTimelineOrigin(origin));
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch (_error) {
    return url;
  }
}

function topNavAcct(section = '') {
  if (section === 'official') return 'official';
  if (page === 'official') return state.me?.acct || currentAcct() || '';
  if (page === 'personal' || page === 'creator' || page === 'official' || page === 'archive' || page === 'timeline') return currentAcct() || state.me?.acct || '';
  return state.me?.acct || '';
}

function topNavHref(section) {
  const acct = topNavAcct(section);
  if (section === 'community') return '/people';
  if (section === 'published') return '/published';
  if (section === 'official') return '/u/official/official';
  if (!acct) return loginHref(window.location.pathname || '/');
  if (section === 'creator') return `/u/${encodeURIComponent(acct)}/creator`;
  if (section === 'archive') return `/u/${encodeURIComponent(acct)}/archive`;
  return `/u/${encodeURIComponent(acct)}`;
}

function sectionNav() {
  const current = currentTopSection();
  const tabs = [
    { key: 'personal', label: 'Personal' },
    { key: 'creator', label: 'Creator' },
    { key: 'archive', label: 'Archive' },
    { key: 'community', label: 'Community' },
    { key: 'published', label: 'Published' },
  ];
  if (state.me?.is_admin) tabs.push({ key: 'official', label: 'Official' });
  return `
    <nav class="section-nav" aria-label="Primary sections" data-section-nav>
      <span class="section-nav__indicator" aria-hidden="true"></span>
      ${tabs.map((tab) => {
        const editorMarker = page === 'timeline' && tab.key === current ? '<small>Editor</small>' : '';
        return `<a class="section-nav__link ${current === tab.key ? 'active' : ''}" data-section-key="${tab.key}" href="${topNavHref(tab.key)}">${tab.label}${editorMarker}</a>`;
      }).join('')}
    </nav>`;
}

function positionSectionIndicator() {
  const nav = document.querySelector('[data-section-nav]');
  const active = nav?.querySelector('.section-nav__link.active');
  if (!nav || !active) return;
  const navRect = nav.getBoundingClientRect();
  const activeRect = active.getBoundingClientRect();
  nav.style.setProperty('--nav-indicator-left', `${activeRect.left - navRect.left}px`);
  nav.style.setProperty('--nav-indicator-width', `${activeRect.width}px`);
}

window.addEventListener('resize', () => requestAnimationFrame(positionSectionIndicator));

function isOfficialRegistryMode() {
  const user = state.personal?.user;
  return page === 'official' && currentWorkspaceMode() === 'creator' && user?.is_admin && user?.acct === 'official';
}

function parseHashtagText(value) {
  return String(value || '')
    .replaceAll('#', ' ')
    .split(/[,\s]+/)
    .map((entry) => entry.trim().toLowerCase())
    .filter(Boolean);
}

function formatHashtagText(tags) {
  return (tags || []).map((tag) => `#${tag}`).join(' ');
}

function officialRegistryTableMarkup() {
  const rows = state.personal?.official_registry_rows || [];
  return `
    <section class="official-registry-section">
      <div class="section-header">
        <div>
          <div class="eyebrow">Official source manager</div>
          <h2>Official publish registry</h2>
          <p class="muted">Admin spreadsheet for official source links. Each row controls source code, feed link, hashtags, description, format, and visibility.</p>
        </div>
        <button class="primary" data-action="official-add-row">Add row</button>
      </div>
      <div class="official-registry-wrap">
        <table class="official-registry-table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Title</th>
              <th>Subscribe link</th>
              <th>Format</th>
              <th>Hashtags</th>
              <th>Description</th>
              <th>Visibility</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="official-registry-body">
            ${rows.map((row) => `
              <tr data-row-id="${row.id}">
                <td><input data-field="source_code" value="${escapeHtml(row.source_code || '')}" placeholder="F1" /></td>
                <td><input data-field="title" value="${escapeHtml(row.title || '')}" placeholder="Formula 1" /></td>
                <td><input data-field="url" value="${escapeHtml(row.url || '')}" placeholder="https://...ics/.csv" /></td>
                <td>
                  <select data-field="source_format">
                    <option value="" ${!row.source_format ? 'selected' : ''}>Auto</option>
                    <option value="ical" ${row.source_format === 'ical' ? 'selected' : ''}>iCal</option>
                    <option value="csv" ${row.source_format === 'csv' ? 'selected' : ''}>CSV</option>
                  </select>
                </td>
                <td><input data-field="hashtags" value="${escapeHtml(formatHashtagText(row.hashtags || []))}" placeholder="#official #f1" /></td>
                <td><textarea data-field="description" placeholder="Short source description">${escapeHtml(row.description || '')}</textarea></td>
                <td>
                  <select data-field="visible">
                    <option value="true" ${row.visible ? 'selected' : ''}>Visible</option>
                    <option value="false" ${row.visible ? '' : 'selected'}>Hidden</option>
                  </select>
                </td>
                <td class="row-actions">
                  <button data-action="official-save-row" data-id="${row.id}">Save</button>
                  <button class="danger" data-action="official-trash-row" data-id="${row.id}">Trash</button>
                </td>
              </tr>
            `).join('')}
          </tbody>
          <tbody id="official-registry-drafts"></tbody>
        </table>
      </div>
    </section>`;
}

function officialDraftRowMarkup(index) {
  return `
    <tr data-draft-index="${index}">
      <td><input data-field="source_code" placeholder="Code" /></td>
      <td><input data-field="title" placeholder="Title" /></td>
      <td><input data-field="url" placeholder="https://...ics/.csv" /></td>
      <td>
        <select data-field="source_format">
          <option value="" selected>Auto</option>
          <option value="ical">iCal</option>
          <option value="csv">CSV</option>
        </select>
      </td>
      <td><input data-field="hashtags" placeholder="#official" /></td>
      <td><textarea data-field="description" placeholder="Description"></textarea></td>
      <td>
        <select data-field="visible">
          <option value="true" selected>Visible</option>
          <option value="false">Hidden</option>
        </select>
      </td>
      <td class="row-actions">
        <button class="primary" data-action="official-create-row">Create</button>
        <button data-action="official-remove-draft">Remove</button>
      </td>
    </tr>`;
}

function toolbarMoreMenu() {
  return `<div class="toolbar-menu-wrap">
    <button type="button" class="button icon-button more-button" data-action="toggle-action-menu" aria-label="More actions"><span class="more-icon" aria-hidden="true"><span></span><span></span><span></span></span></button>
    ${state.actionMenuOpen ? `<div class="toolbar-menu" role="menu">
      <button type="button" role="menuitem" data-action="open-merge-tool">Merge & separate</button>
    </div>` : ''}
  </div>`;
}

function colorSwatches(id, currentColor, title = '') {
  const palette = ['#2a9d8f', '#577590', '#3bb8ba', '#c06c84', '#8a5a3b', '#7c6ee6', '#e76f51', '#146c73'];
  const color = timelineColor(currentColor);
  const colors = [color, ...palette.filter((item) => item.toLowerCase() !== color.toLowerCase())].slice(0, 6);
  const open = state.colorMenuOpenId === id;
  return `<div class="color-menu-wrap">
    <button type="button" class="color-menu-button" data-action="toggle-color-menu" data-id="${escapeHtml(id)}" style="--swatch-color:${escapeHtml(color)}" aria-label="Timeline color for ${escapeHtml(title)}" aria-expanded="${open}"></button>
    ${open ? `<div class="color-menu" role="radiogroup" aria-label="Timeline color for ${escapeHtml(title)}">
      ${colors.map((item) => `<button type="button" class="color-swatch ${item.toLowerCase() === color.toLowerCase() ? 'active' : ''}" data-action="subscription-color-choice" data-id="${escapeHtml(id)}" data-color="${escapeHtml(item)}" style="--swatch-color:${escapeHtml(item)}" aria-label="Use ${escapeHtml(item)}" aria-checked="${item.toLowerCase() === color.toLowerCase()}" role="radio"></button>`).join('')}
    </div>` : ''}
  </div>`;
}

function personalToolbar() {
  const me = state.me;
  const user = state.personal.user;
  const creatorMode = currentWorkspaceMode() === 'creator';
  const archiveMode = currentWorkspaceMode() === 'archive';
  const moreMenu = toolbarMoreMenu();
  const calendarParam = state.personal?.active_calendar_id ? `&calendar_id=${encodeURIComponent(state.personal.active_calendar_id)}` : '';
  const newTimelineAction = `<a class="button primary toolbar-main-action toolbar-create-action" href="${withTimelineOrigin(`/u/${encodeURIComponent(user.acct)}/timelines/new`, currentTopSection())}${calendarParam}">Create a new timeline</a>`;
  const importAction = `<button class="button tinted toolbar-main-action toolbar-import-action" type="button" data-action="open-import-menu">Import</button>`;
  const exportAction = `<button class="button toolbar-export-action" type="button" data-action="open-export">Export</button>`;
  const actions = user.is_owner ? (creatorMode
    ? `${newTimelineAction}${importAction}<button class="primary toolbar-publish-action" data-action="open-publish">Publish</button>${moreMenu}`
    : archiveMode
      ? ''
      : `${newTimelineAction}${importAction}${exportAction}${moreMenu}`)
    : '';
  return `
    <header class="topbar">
      <div class="topbar-main">
        <div class="brand">
          <div class="eyebrow">TimeGrid Calendar</div>
          <div class="brand-title-row">
            <h1>${escapeHtml(user.display_name || user.acct)}</h1>
          </div>
        </div>
        <div class="topbar-utility">
          <button data-action="logout">Sign out</button>
          ${notificationsButton()}
        </div>
      </div>
      ${sectionNav()}
      ${actions ? `<div class="toolbar toolbar--context">${actions}</div>` : ''}
  </header>`;
}

function calendarTabs() {
  const calendars = state.personal?.calendars || [];
  const mode = page === 'timeline' ? currentTimelineOrigin() : currentWorkspaceMode();
  if (!calendars.length || mode === 'archive') return '';
  const activeId = state.personal?.active_calendar_id || calendars[0]?.id || '';
  return `<nav class="calendar-tabs" aria-label="Calendars">
    ${calendars.map((item, index) => `
      <button type="button" class="calendar-tab ${item.id === activeId ? 'active' : ''}" data-action="switch-calendar" data-id="${escapeHtml(item.id)}" data-index="${index}" draggable="true" title="${escapeHtml(item.title)}">
        <span class="calendar-tab-color" style="background:${escapeHtml(timelineColor(item.color || '#2f7d80'))}"></span>
        <span>${escapeHtml(item.title || 'Calendar')}</span>
      </button>`).join('')}
    <button type="button" class="calendar-tab calendar-tab-add" data-action="create-calendar" title="New calendar">+</button>
  </nav>`;
}

function workspaceProgress() {
  if (!state.workspaceBusyLabel) return '';
  return `<div class="workspace-progress" role="status" aria-live="polite">
    <div class="workspace-progress__bar"></div>
    <span>${escapeHtml(state.workspaceBusyLabel)}</span>
  </div>`;
}

function setWorkspaceBusy(label = '') {
  state.workspaceBusyLabel = label;
}

function clearDragInsertMarkers() {
  state.dragSubscriptionInsertId = '';
  state.dragSubscriptionInsertAfter = false;
  state.dragCalendarInsertId = '';
  state.dragCalendarInsertAfter = false;
  document.querySelectorAll('.insert-before, .insert-after, .drag-over').forEach((node) => {
    node.classList.remove('insert-before', 'insert-after', 'drag-over');
  });
}

function markInsertTarget(node, after) {
  if (!node) return;
  node.classList.toggle('insert-before', !after);
  node.classList.toggle('insert-after', after);
}

function isAfterPointer(event, node, axis = 'y') {
  const rect = node.getBoundingClientRect();
  if (axis === 'x') return event.clientX > rect.left + rect.width / 2;
  return event.clientY > rect.top + rect.height / 2;
}

function moveArrayItem(items, itemId, targetIndex) {
  const fromIndex = items.findIndex((item) => item.id === itemId);
  if (fromIndex < 0) return false;
  const nextIndex = Math.max(0, Math.min(targetIndex, items.length));
  const [item] = items.splice(fromIndex, 1);
  const adjustedIndex = fromIndex < nextIndex ? nextIndex - 1 : nextIndex;
  items.splice(Math.max(0, adjustedIndex), 0, item);
  items.forEach((entry, index) => { entry.position = index; });
  return true;
}

function appendArrayItem(items, itemId) {
  const fromIndex = items.findIndex((item) => item.id === itemId);
  if (fromIndex < 0) return false;
  const [item] = items.splice(fromIndex, 1);
  items.push(item);
  items.forEach((entry, index) => { entry.position = index; });
  return true;
}

async function persistSubscriptionPosition(subscriptionId, position, snapshot, fallbackMessage = 'Could not reorder timeline') {
  try {
    await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${encodeURIComponent(subscriptionId)}`, {
      method: 'PATCH',
      body: JSON.stringify({ position }),
    });
  } catch (error) {
    if (state.personal) state.personal.subscriptions = snapshot;
    setBanner('', error.message || fallbackMessage);
    render();
  }
}

async function persistCalendarPosition(calendarId, position, snapshot) {
  try {
    await api(`/api/personal/${encodeURIComponent(currentAcct())}/calendars/${encodeURIComponent(calendarId)}`, {
      method: 'PATCH',
      body: JSON.stringify({ position }),
    });
  } catch (error) {
    if (state.personal) state.personal.calendars = snapshot;
    setBanner('', error.message || 'Could not reorder calendar');
    render();
  }
}

function bindCalendarTabActions() {
  document.querySelectorAll('[data-action="switch-calendar"]').forEach((button) => button.addEventListener('click', async () => {
    const mode = page === 'timeline' ? currentTimelineOrigin() : currentWorkspaceMode();
    state.activeCalendarByMode[mode] = button.dataset.id || '';
    state.exportLinkUrl = '';
    setWorkspaceBusy('Switching calendar...');
    if (page !== 'timeline') render();
    if (page === 'timeline' && !state.timeline?.id) {
      state.timeline.calendar_id = button.dataset.id || '';
      await loadWorkspace(mode);
      setWorkspaceBusy('');
      renderTimeline();
      return;
    }
    if (page === 'timeline') {
      await loadWorkspace(mode);
      setWorkspaceBusy('');
      renderTimeline();
      return;
    }
    await loadWorkspace();
    setWorkspaceBusy('');
    render();
  }));
  document.querySelector('[data-action="create-calendar"]')?.addEventListener('click', async () => {
    const title = window.prompt('Calendar name', 'New calendar');
    if (!title) return;
    try {
      const mode = page === 'timeline' ? currentTimelineOrigin() : currentWorkspaceMode();
      const workspace = mode === 'creator' ? 'creator' : 'personal';
      setWorkspaceBusy('Creating calendar...');
      if (page !== 'timeline') render();
      const data = await api(`/api/personal/${encodeURIComponent(currentAcct())}/calendars`, {
        method: 'POST',
        body: JSON.stringify({ title, workspace }),
      });
      const calendarId = data.calendar?.id || '';
      state.activeCalendarByMode[mode] = calendarId;
      if (state.personal && Array.isArray(data.calendars)) {
        state.personal.calendars = data.calendars;
        state.personal.active_calendar_id = calendarId;
      }
      await loadWorkspace(mode, calendarId);
      setWorkspaceBusy('');
      if (page === 'timeline') {
        if (!state.timeline?.id) state.timeline.calendar_id = calendarId;
        renderTimeline();
      } else {
        render();
      }
    } catch (error) {
      setWorkspaceBusy('');
      setBanner('', error.message);
    }
  });
  document.querySelectorAll('[data-action="switch-calendar"]').forEach((button) => {
    button.addEventListener('dragstart', (event) => {
      state.draggedCalendarId = button.dataset.id || '';
      event.dataTransfer?.setData('text/timegrid-calendar', state.draggedCalendarId);
      event.dataTransfer?.setData('text/plain', state.draggedCalendarId);
      if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
    });
    button.addEventListener('dragover', (event) => {
      if (state.draggedSubscriptionId || state.draggedCalendarId) {
        event.preventDefault();
        const after = isAfterPointer(event, button);
        state.dragCalendarInsertId = button.dataset.id || '';
        state.dragCalendarInsertAfter = after;
        document.querySelectorAll('.calendar-tab.insert-before, .calendar-tab.insert-after, .calendar-tab.drag-over').forEach((node) => {
          node.classList.remove('insert-before', 'insert-after', 'drag-over');
        });
        if (state.draggedSubscriptionId) {
          button.classList.add('drag-over');
        } else {
          markInsertTarget(button, after);
        }
      }
    });
    button.addEventListener('dragleave', () => {
      button.classList.remove('drag-over', 'insert-before', 'insert-after');
    });
    button.addEventListener('drop', async (event) => {
      event.preventDefault();
      button.classList.remove('drag-over', 'insert-before', 'insert-after');
      const targetCalendarId = button.dataset.id || '';
      try {
        if (state.draggedSubscriptionId) {
          const subscriptionId = state.draggedSubscriptionId;
          const mode = page === 'timeline' ? currentTimelineOrigin() : currentWorkspaceMode();
          const workspace = mode === 'creator' ? 'creator' : 'personal';
          const snapshot = state.personal?.subscriptions ? state.personal.subscriptions.map((item) => ({ ...item })) : [];
          if (state.personal) state.personal.subscriptions = state.personal.subscriptions.filter((item) => item.id !== subscriptionId);
          render();
          api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${encodeURIComponent(subscriptionId)}`, {
            method: 'PATCH',
            body: JSON.stringify({ calendar_id: targetCalendarId, workspace }),
          }).then(async () => {
            state.activeCalendarByMode[mode] = targetCalendarId;
            state.draggedSubscriptionId = '';
            await loadWorkspace(mode);
            render();
          }).catch((error) => {
            if (state.personal) state.personal.subscriptions = snapshot;
            state.draggedSubscriptionId = '';
            setBanner('', error.message || 'Could not move timeline');
            render();
          });
          return;
        }
        if (state.draggedCalendarId && state.draggedCalendarId !== targetCalendarId) {
          const calendarId = state.draggedCalendarId;
          const targetIndex = Math.max(0, Number(button.dataset.index || 0) + (state.dragCalendarInsertAfter ? 1 : 0));
          const snapshot = state.personal?.calendars ? state.personal.calendars.map((item) => ({ ...item })) : [];
          if (state.personal) moveArrayItem(state.personal.calendars, calendarId, targetIndex);
          state.draggedCalendarId = '';
          clearDragInsertMarkers();
          render();
          persistCalendarPosition(calendarId, targetIndex, snapshot);
        }
      } catch (error) {
        state.draggedSubscriptionId = '';
        state.draggedCalendarId = '';
        setBanner('', error.message || 'Could not move item');
      }
    });
    button.addEventListener('dragend', () => {
      state.draggedCalendarId = '';
      clearDragInsertMarkers();
    });
  });
}

function bindSubscriptionDragActions() {
  document.querySelectorAll('[data-draggable-subscription="true"]').forEach((card) => {
    card.addEventListener('dragstart', (event) => {
      state.draggedSubscriptionId = card.dataset.id || '';
      event.dataTransfer?.setData('text/timegrid-subscription', state.draggedSubscriptionId);
      event.dataTransfer?.setData('text/plain', state.draggedSubscriptionId);
      if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
      card.classList.add('dragging');
    });
    card.addEventListener('dragover', (event) => {
      if (!state.draggedSubscriptionId || state.draggedSubscriptionId === card.dataset.id) return;
      event.preventDefault();
      const after = isAfterPointer(event, card);
      state.dragSubscriptionInsertId = card.dataset.id || '';
      state.dragSubscriptionInsertAfter = after;
      document.querySelectorAll('.sub-card.insert-before, .sub-card.insert-after, .sub-card.drag-over').forEach((node) => {
        node.classList.remove('insert-before', 'insert-after', 'drag-over');
      });
      markInsertTarget(card, after);
    });
    card.addEventListener('dragleave', () => card.classList.remove('drag-over', 'insert-before', 'insert-after'));
    card.addEventListener('drop', async (event) => {
      event.preventDefault();
      card.classList.remove('drag-over', 'insert-before', 'insert-after');
      const targetId = card.dataset.id || '';
      if (!state.draggedSubscriptionId || state.draggedSubscriptionId === targetId) return;
      const subscriptionId = state.draggedSubscriptionId;
      const cards = [...document.querySelectorAll('[data-draggable-subscription="true"]')];
      const baseIndex = Math.max(0, cards.findIndex((node) => node.dataset.id === targetId));
      const targetIndex = baseIndex + (state.dragSubscriptionInsertAfter ? 1 : 0);
      const snapshot = state.personal?.subscriptions ? state.personal.subscriptions.map((item) => ({ ...item })) : [];
      if (state.personal && moveArrayItem(state.personal.subscriptions, subscriptionId, targetIndex)) {
        state.draggedSubscriptionId = '';
        clearDragInsertMarkers();
        render();
        persistSubscriptionPosition(subscriptionId, targetIndex, snapshot);
      }
    });
    card.addEventListener('dragend', () => {
      state.draggedSubscriptionId = '';
      card.classList.remove('dragging');
      clearDragInsertMarkers();
    });
  });
  document.querySelector('[data-drop-subscription-list="true"]')?.addEventListener('dragover', (event) => {
    if (state.draggedSubscriptionId) {
      event.preventDefault();
      event.currentTarget.classList.add('drag-over');
    }
  });
  document.querySelector('[data-drop-subscription-list="true"]')?.addEventListener('dragleave', (event) => {
    event.currentTarget.classList.remove('drag-over');
  });
  document.querySelector('[data-drop-subscription-list="true"]')?.addEventListener('drop', async (event) => {
    event.preventDefault();
    event.currentTarget.classList.remove('drag-over');
    if (!state.draggedSubscriptionId) return;
    const subscriptionId = state.draggedSubscriptionId;
    const snapshot = state.personal?.subscriptions ? state.personal.subscriptions.map((item) => ({ ...item })) : [];
    if (state.personal && appendArrayItem(state.personal.subscriptions, subscriptionId)) {
      state.draggedSubscriptionId = '';
      clearDragInsertMarkers();
      render();
      persistSubscriptionPosition(subscriptionId, Math.max(0, state.personal.subscriptions.length - 1), snapshot);
    }
  });
}

function subscriptionCard(item) {
  const color = timelineColor(item.color || '#146c73');
  const isVisible = item.visible !== false;
  const creatorMode = page === 'creator' || page === 'official';
  const archiveMode = page === 'archive';
  const editControl = item.edit_url
    ? `<a class="button" href="${escapeHtml(withTimelineOrigin(item.edit_url, currentTopSection()))}">Edit</a>`
    : item.editable_shell
      ? `<button type="button" data-action="edit-subscription" data-id="${item.id}">Edit</button>`
      : '';
  const toggleControl = `<button type="button" data-action="toggle-visible" data-id="${item.id}" data-visible="${String(!isVisible)}">${isVisible ? 'Hide' : 'Show'}</button>`;
  const trashControl = `<button type="button" class="danger" data-action="trash" data-id="${item.id}">Trash</button>`;
  const moveControl = archiveMode
    ? `<button type="button" data-action="move-workspace" data-id="${item.id}" data-workspace="creator">Move to Creator manager</button>`
    : creatorMode
      ? `${item.archive_allowed ? `<button type="button" data-action="move-workspace" data-id="${item.id}" data-workspace="archive">Move to Archive</button>` : ''}<button type="button" data-action="detach-delete" data-id="${item.id}">Detach&delete</button><button type="button" class="danger" data-action="permanent-delete" data-id="${item.id}">Permanent delete</button>`
      : `<button type="button" data-action="move-workspace" data-id="${item.id}" data-workspace="creator">Move to Creator manager</button>`;
  const moreControl = moveControl ? `<details class="sub-more"><summary aria-label="More actions"><span class="more-icon" aria-hidden="true"><span></span><span></span><span></span></span></summary><div class="sub-more-menu">${moveControl}</div></details>` : '';
  const cardClass = item.is_bundle ? 'sub-card bundle-card active' : 'sub-card active';
  const dragAttrs = archiveMode ? '' : `draggable="true" data-draggable-subscription="true" data-id="${escapeHtml(item.id)}"`;
  if (item.is_bundle) {
    return `<article class="${cardClass}" ${dragAttrs} style="--timeline-color:${color}">
      <header>
        <div>
          <strong>${escapeHtml(item.title)}</strong>
          <div class="sub-meta-row">${subscriptionKindBadge(item)}</div>
        </div>
        <div class="sub-actions">
          ${editControl}${toggleControl}${archiveMode ? '' : trashControl}${moreControl}
        </div>
      </header>
      <details>
        <summary>Included calendars</summary>
        ${(item.children || []).map((child) => `<div class="bundle-child"><span>${escapeHtml(child.title)}</span><a href="${child.url}">${escapeHtml(child.url)}</a></div>`).join('')}
      </details>
    </article>`;
  }
  return `<article class="${cardClass}" ${dragAttrs} style="--timeline-color:${color}">
    <header>
      <div>
        <strong>${escapeHtml(item.title)}</strong>
        <div class="sub-meta-row">${subscriptionKindBadge(item)}${colorSwatches(item.id, color, item.title)}</div>
      </div>
      <div class="sub-actions">
        ${editControl}${toggleControl}${archiveMode ? '' : trashControl}${moreControl}
      </div>
    </header>
  </article>`;
}

function trashCard(item) {
  const color = timelineColor(item.color || item.source_color || '#146c73');
  return `
    <article class="trash-card" style="--timeline-color:${color}">
      <header>
        <div>
          <strong>${escapeHtml(item.title)}</strong>
          <div class="sub-meta-row">${subscriptionKindBadge(item)}<span class="timeline-color-dot" aria-label="Timeline color" style="background:${color}"></span></div>
        </div>
        <div class="sub-actions">
          ${item.edit_url ? `<a class="button" href="${escapeHtml(withTimelineOrigin(item.edit_url, currentTopSection()))}">Edit</a>` : ''}
          <button data-action="restore" data-id="${item.id}">Restore</button>
          <button class="danger" data-action="delete" data-id="${item.id}">Delete</button>
        </div>
      </header>
    </article>`;
}

function publishCard(item, options = {}) {
  const manage = !!options.manage;
  const visibilityLabel = item.visibility === 'private' ? 'Private' : (item.visibility === 'invited' ? `Invited${item.invited?.length ? ` · ${item.invited.length} invited` : ''}` : 'Public');
  const subscribeControl = item.subscribed
    ? '<button class="button" disabled>Added to my calendar</button>'
    : `<a class="button" href="${escapeHtml(item.subscribe_url || '')}">Add to my calendar</a>`;
  const contributorText = (item.contributors || []).map((entry) => `${entry.name}${entry.count > 1 ? ` (${entry.count})` : ''}`).join(', ');
  const publishStateLabel = item.publish_state === 'archived'
    ? 'Archived'
    : item.publish_state === 'removed_from_publishing'
      ? 'Removed from publishing'
      : item.publish_state === 'removed_permanently'
        ? 'Owner removed'
        : '';
  return `
    <article class="public-card">
      <header>
        <div>
          <strong>${escapeHtml(item.title)}</strong>
          <div class="muted">${escapeHtml(item.subscription_count)} subscriptions</div>
          <div class="muted">${escapeHtml(visibilityLabel)}</div>
          <div class="muted">By <a href="/people/${encodeURIComponent(item.owner_acct || '')}">@${escapeHtml(item.owner_acct || '')}</a></div>
          ${publishStateLabel ? `<div class="muted">${escapeHtml(publishStateLabel)}</div>` : ''}
          ${contributorText ? `<div class="muted">Authors: ${escapeHtml(contributorText)}</div>` : ''}
        </div>
        <div class="sub-actions">
          <a class="button primary" href="/p/${encodeURIComponent(item.slug)}" target="_blank" rel="noreferrer">Open</a>
          ${subscribeControl}
          <button class="button" data-action="share-bundle" data-url="${escapeHtml(item.share_url)}" data-title="${escapeHtml(item.title)}">Share</button>
          ${manage ? `<button class="button" data-action="manage-published" data-slug="${escapeHtml(item.slug)}">Manage</button>` : ''}
        </div>
      </header>
      <a class="share-link" href="${escapeHtml(item.share_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.share_url)}</a>
    </article>`;
}

function publishedManageModal() {
  if (!state.publishedManageSlug) return '';
  const item = (state.personal?.published || []).find((entry) => entry.slug === state.publishedManageSlug);
  if (!item) return '';
  return `
    <div class="modal-backdrop" data-action="close-published-manage">
      <div class="modal" onclick="event.stopPropagation()">
        <div class="modal-header">
          <div>
            <div class="eyebrow">Published access</div>
            <h2>${escapeHtml(item.title)}</h2>
            <p class="muted">Choose who can view this published timeline.</p>
          </div>
          <button class="modal-close" data-action="close-published-manage" aria-label="Close published access menu">×</button>
        </div>
        <label>
          <div class="muted">Visibility</div>
          <select id="published-visibility">
            <option value="public" ${item.visibility === 'public' ? 'selected' : ''}>Public</option>
            <option value="invited" ${item.visibility === 'invited' ? 'selected' : ''}>Invited</option>
            <option value="private" ${item.visibility === 'private' ? 'selected' : ''}>Private</option>
          </select>
        </label>
        <label>
          <div class="muted">Invited usernames or emails</div>
          <textarea id="published-invited" placeholder="alice, bob@example.com">${escapeHtml((item.invited || []).join(', '))}</textarea>
        </label>
        <label>
          <div class="muted">Share hashtags (max 20)</div>
          <textarea id="published-hashtags" placeholder="#school #uoft #toronto">${escapeHtml(item.hashtag_text || (item.hashtags || []).map((tag) => `#${tag}`).join(' '))}</textarea>
        </label>
        <div class="modal-actions">
          <button data-action="archive-published" data-slug="${escapeHtml(item.slug)}">Archive</button>
          <button data-action="remove-published" data-slug="${escapeHtml(item.slug)}">Removed from publishing</button>
          <button class="danger" data-action="permanent-remove-published" data-slug="${escapeHtml(item.slug)}">Permanent remove</button>
        </div>
        <div class="modal-actions">
          <button class="primary" data-action="save-published-manage">Save access</button>
          <button data-action="close-published-manage">Cancel</button>
        </div>
      </div>
    </div>`;
}

function timelineMiniCard(item) {
  return `
    <article class="public-card">
      <header>
        <div>
          <strong>${escapeHtml(item.title)}</strong>
          <div class="muted">${(item.events || []).length} events</div>
        </div>
        <a class="button" href="${escapeHtml(item.edit_url)}">Edit</a>
      </header>
      <a class="share-link" href="${escapeHtml(item.ics_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.ics_url)}</a>
    </article>`;
}


function subscriptionKindBadge(item) {
  let label = 'External';
  if (item.is_bundle) label = 'Bundle';
  else if (item.is_self_owned || item.owned_timeline_id) label = 'Self-owned';
  else if (item.editable_shell) label = 'Editable shell';
  else if (item.visible === false) label = 'Hidden';
  return `<span class="status-bubble">${escapeHtml(label)}</span>`;
}

function importMenuModal() {
  if (!state.importOpen) return '';
  return `
    <div class="modal-backdrop" data-action="close-import-menu">
      <div class="modal import-menu-modal" onclick="event.stopPropagation()">
        <div class="modal-header">
          <div>
            <div class="eyebrow">Import</div>
            <h2>Add a subscription or import a calendar file</h2>
            <p class="muted">Type a title and calendar URL, or import an .ics, .ical, or .csv file into a new editable timeline.</p>
          </div>
          <button class="modal-close" data-action="close-import-menu" aria-label="Close import menu">×</button>
        </div>
        <form id="import-form" class="import-menu-form">
          <label>
            <div class="muted">Title</div>
            <input name="title" placeholder="Subscription title" />
          </label>
          <label>
            <div class="muted">Calendar URL</div>
            <input name="url" placeholder="https://...ics or https://calendar.time-grid.org/p/..." />
          </label>
          <div class="modal-actions import-menu-actions">
            <button class="primary" type="submit">Add subscription</button>
            <button type="button" data-action="import-personal">Import calendar files</button>
            <button type="button" data-action="close-import-menu">Cancel</button>
          </div>
        </form>
      </div>
    </div>`;
}

function mergeToolModal() {
  if (!state.mergeToolOpen) return '';
  const subs = state.personal.subscriptions || [];
  const mergeCandidates = subs;
  const bundleOptions = subs.filter((item) => item.is_bundle);
  const selectedBundle = bundleOptions.find((item) => item.id === state.mergeToolSourceId) || bundleOptions[0] || null;
  if (selectedBundle && state.mergeToolSourceId !== selectedBundle.id) state.mergeToolSourceId = selectedBundle.id;
  if (!selectedBundle) state.mergeToolSourceId = '';
  const components = selectedBundle?.components || [];
  return `
    <div class="modal-backdrop" data-action="close-merge-tool">
      <div class="modal" onclick="event.stopPropagation()">
        <div class="modal-header">
          <div>
            <div class="eyebrow">Merge & separate</div>
          <h2>Merge timelines or break them apart</h2>
          <p class="muted">Merge top-level timelines into one folder, or pull one or more internal timelines back out.</p>
          </div>
          <button class="modal-close" data-action="close-merge-tool" aria-label="Close merge menu">×</button>
        </div>
        <section>
          <div class="section-header"><h3>Merge</h3><span class="muted">Selected timelines stay active but become hidden under the new merged folder</span></div>
          <label>
            <div class="muted">New timeline name</div>
            <input id="merge-title" placeholder="Merged study plan" />
          </label>
          <div class="check-list">
            ${mergeCandidates.length ? mergeCandidates.map((item) => `
              <label class="check-row">
                <input class="check-input" type="checkbox" value="${item.id}" data-merge-sub />
                <div class="check-copy">
                  <strong>${escapeHtml(item.title)}</strong>
                  <div class="muted">${item.is_bundle ? `${item.component_count || 0} internal timelines will be flattened into the new merge` : escapeHtml(item.url || '')}</div>
                  ${item.author_name ? `<div class="muted">Author: ${escapeHtml(item.author_name)}</div>` : ''}
                </div>
              </label>`).join('') : '<div class="empty">Add or create timelines first.</div>'}
          </div>
          <div class="modal-actions">
            <button class="primary" data-action="submit-merge">Create merged timeline</button>
          </div>
        </section>
        <section>
          <div class="section-header"><h3>Separate</h3><span class="muted">Extract one or more internal timelines from a merged folder</span></div>
          ${bundleOptions.length ? `
            <label>
              <div class="muted">Merged timeline</div>
              <select id="separate-source">
                ${bundleOptions.map((item) => `<option value="${item.id}" ${item.id === state.mergeToolSourceId ? 'selected' : ''}>${escapeHtml(item.title)}</option>`).join('')}
              </select>
            </label>
            <div class="check-list">
              ${components.length ? components.map((item) => `
                <label class="check-row">
                  <input class="check-input" type="checkbox" value="${item.id || ''}" data-separate-sub checked />
                  <div class="check-copy">
                    <strong>${escapeHtml(item.title || item.url || 'Timeline')}</strong>
                    ${item.author_name ? `<div class="muted">Author: ${escapeHtml(item.author_name)}</div>` : ''}
                    <div class="muted">${escapeHtml(item.url || '')}</div>
                  </div>
                </label>`).join('') : '<div class="empty">This merged timeline has no internal timelines.</div>'}
            </div>
            <label class="check-row">
              <input class="check-input" type="checkbox" id="separate-trash-original" checked />
              <div class="check-copy">
                <strong>Move original merged timeline to trash</strong>
                <div class="muted">If unchecked, the remaining hidden timelines stay grouped underneath it.</div>
              </div>
            </label>
            <div class="modal-actions">
              <button class="primary" data-action="submit-separate">Extract selected timelines</button>
            </div>` : '<div class="empty">Create a merged timeline first.</div>'}
        </section>
        <div class="modal-actions">
          <button data-action="close-merge-tool">Close</button>
        </div>
      </div>
    </div>`;
}

function publishModal() {
  if (!state.publishOpen) return '';
  const subs = state.personal.publish_candidates || [];
  const groups = [
    { key: 'creator', label: 'Creator' },
    { key: 'personal', label: 'Personal' },
    { key: 'archive', label: 'Archive' },
  ];
  return `
    <div class="modal-backdrop" data-action="close-modal">
      <div class="modal" onclick="event.stopPropagation()">
        <div class="modal-header">
          <div>
            <div class="eyebrow">Publish</div>
            <h2>Build a shareable calendar</h2>
            <p class="muted">Select the subscriptions you want to merge into one public link for Mastodon.</p>
          </div>
          <button class="modal-close" data-action="close-modal" aria-label="Close publish menu">×</button>
        </div>
        <label>
          <div class="muted">Title</div>
          <input id="publish-title" placeholder="My published calendar" />
        </label>
        <label>
          <div class="muted">Visibility</div>
          <select id="publish-visibility">
            <option value="public">Public</option>
            <option value="invited">Invited</option>
            <option value="private">Private</option>
          </select>
        </label>
        <label>
          <div class="muted">Invited usernames or emails</div>
          <textarea id="publish-invited" placeholder="alice, bob@example.com"></textarea>
        </label>
        <label>
          <div class="muted">Share hashtags (max 20)</div>
          <textarea id="publish-hashtags" placeholder="#school #uoft #toronto"></textarea>
        </label>
        <div class="check-list">
          ${subs.length ? groups.map((group) => {
            const items = subs.filter((item) => (item.workspace || 'personal') === group.key);
            if (!items.length) return '';
            return `
              <div class="check-group">
                <div class="check-group-title">${group.label}</div>
                ${items.map(item => `
                  <label class="check-row">
                    <input class="check-input" type="checkbox" value="${item.id}" data-publish-sub ${item.visible ? 'checked' : ''} />
                    <div class="check-copy">
                      <strong>${escapeHtml(item.title)}</strong>
                      <div class="muted">${escapeHtml(item.url || '')}</div>
                    </div>
                  </label>
                `).join('')}
              </div>`;
          }).join('') : '<div class="empty">Add a subscription first.</div>'}
        </div>
        <div class="modal-actions">
          <button class="primary" data-action="submit-publish">Create published link</button>
          <button data-action="close-modal">Cancel</button>
        </div>
      </div>
    </div>`;
}

function renderPersonal() {
  const user = state.personal.user;
  const creatorMode = currentWorkspaceMode() === 'creator';
  const archiveMode = currentWorkspaceMode() === 'archive';
  const subscriptionLabel = archiveMode ? 'Archived timelines' : 'Subscriptions';
  const workspaceLabel = creatorMode ? 'Creator page' : archiveMode ? 'Archive page' : 'Personal page';
  const timelineLabel = archiveMode ? 'Archived timelines' : 'Timelines created by me';
  root.innerHTML = `
    <div class="page-shell">
      ${personalToolbar()}
      <div class="grid workspace-grid">
        <aside class="sidebar workspace-sidebar">
          ${workspaceProgress()}
          <div class="workspace-left-stack">
            ${calendarTabs()}
            <section class="sidebar-section subscriptions-panel">
              <div class="section-header subscriptions-section-header">
                <h2>${subscriptionLabel} <span class="section-count">${state.personal.subscriptions.length}</span></h2>
                ${archiveMode || !state.personal.subscriptions.length ? '' : `<button type="button" class="subtle" data-action="toggle-all-visible" data-visible="${String(!state.personal.subscriptions.every((item) => item.visible !== false))}">${state.personal.subscriptions.every((item) => item.visible !== false) ? 'Hide all' : 'Show all'}</button>`}
              </div>
              ${state.personal.subscriptions.length ? '' : (archiveMode ? '<div class="empty">Archived timelines stay on the server but are not meant for active editing.</div>' : '<div class="muted sidebar-note">Use Import to add a URL or calendar file.</div>')}
              <div class="sub-list" data-drop-subscription-list="true">
                ${state.personal.subscriptions.length ? state.personal.subscriptions.map(subscriptionCard).join('') : `<div class="empty">${archiveMode ? 'No archived timelines yet.' : 'No subscriptions yet. Add holiday links, F1 schedules, or other TimeGrid pages here.'}</div>`}
              </div>
            </section>
          </div>
          ${archiveMode ? '' : `<section class="sidebar-section">
            <div class="section-header trash-section-header">
              <h3>Trash <span class="section-count">${state.personal.trash.length}</span></h3>
              <button class="danger" data-action="empty-trash" ${state.personal.trash.length ? '' : 'disabled'}>Empty trash</button>
            </div>
            <div class="sub-list">
              ${state.personal.trash.length ? state.personal.trash.map(trashCard).join('') : '<div class="empty">Trash is empty.</div>'}
            </div>
          </section>`}
        </aside>
        <main class="main-panel workspace-main">
          <div class="calendar-stage">
            ${mastodonProvisioningBanner()}
            <section class="calendar-view-section">
              <div class="section-header calendar-view-header">
                <div>
                  <div class="eyebrow">${workspaceLabel}</div>
                </div>
                <div class="view-meta">
                  <span>${state.personal.visible_sources?.length || 0} visible</span>
                  <span>${state.personal.subscriptions.length} sources</span>
                </div>
              </div>
              ${state.personal.visible_sources?.length ? '<div id="personal-calendar-hint" class="calendar-tip hidden"></div><div id="personal-calendar" class="readonly-calendar-shell"></div>' : `<div class="empty">${archiveMode ? 'Archive page does not provide editing controls, but visibility can still show what remains on the server.' : 'Turn on visibility for at least one subscription to render the calendar.'}</div>`}
            </section>
            <section class="workspace-section">
              <div class="section-header">
                <h3>${timelineLabel}</h3>
                ${archiveMode ? '' : `<a class="button" href="${withTimelineOrigin(`/u/${encodeURIComponent(user.acct)}/timelines/new`, currentTopSection())}${state.personal?.active_calendar_id ? `&calendar_id=${encodeURIComponent(state.personal.active_calendar_id)}` : ''}">New timeline</a>`}
              </div>
              <div class="public-list">
                ${state.personal.timelines.length ? state.personal.timelines.map(timelineMiniCard).join('') : `<div class="empty">${archiveMode ? 'No archived timelines yet.' : 'No self-owned timelines yet.'}</div>`}
              </div>
            </section>
            ${archiveMode ? `<section class="workspace-section">
              <div class="section-header">
                <h3>Archived published timelines</h3>
                <span class="muted">Published bundles kept for current subscribers without active management</span>
              </div>
              <div class="public-list">
                ${state.personal.archived_published?.length ? state.personal.archived_published.map((item) => publishCard(item, { manage: false })).join('') : '<div class="empty">No archived published timelines yet.</div>'}
              </div>
            </section>` : ''}
          </div>
        </main>
      </div>
      ${exportModal()}
      ${importMenuModal()}
      ${creatorMode ? publishModal() : ''}
      ${mergeToolModal()}
      ${creatorMode ? publishedManageModal() : ''}
      ${notificationsModal()}
      ${eventDetailModal()}
      <input id="personal-import-input" type="file" accept=".ics,.ical,.csv,text/calendar,text/csv" class="hidden" />
    </div>`;

  const officialRegistryMode = isOfficialRegistryMode();
  if (officialRegistryMode) {
    const grid = root.querySelector('.grid');
    const main = root.querySelector('.main-panel');
    if (grid && main) {
      root.querySelector('.sidebar')?.remove();
      grid.classList.add('official-registry-layout');
      main.innerHTML = `
        <div class="calendar-stage">
          ${mastodonProvisioningBanner()}
          ${officialRegistryTableMarkup()}
        </div>`;
    }
  }

  document.querySelector('[data-action="logout"]')?.addEventListener('click', logout);
  document.querySelector('[data-action="go-mastodon-home"]')?.addEventListener('click', goToMastodonHome);
  bindCalendarTabActions();
  bindSubscriptionDragActions();
  bindNoticeActions(renderPersonal);
  bindEventDetailActions();
  bindExportActions();
  document.querySelector('[data-action="open-merge-tool"]')?.addEventListener('click', () => {
    state.mergeToolOpen = true;
    state.actionMenuOpen = false;
    state.mergeToolSourceId = state.personal?.subscriptions?.find((item) => item.is_bundle)?.id || '';
    render();
  });
  document.querySelector('[data-action="toggle-action-menu"]')?.addEventListener('click', () => {
    state.actionMenuOpen = !state.actionMenuOpen;
    render();
  });
  document.querySelector('[data-action="open-publish"]')?.addEventListener('click', () => {
    state.publishOpen = true;
    render();
  });
  document.querySelector('[data-action="open-import-menu"]')?.addEventListener('click', () => {
    state.importOpen = true;
    state.actionMenuOpen = false;
    render();
  });
  document.querySelectorAll('[data-action="close-import-menu"]').forEach((node) => node.addEventListener('click', () => {
    state.importOpen = false;
    render();
  }));
  document.querySelector('[data-action="import-personal"]')?.addEventListener('click', () => {
    document.getElementById('personal-import-input')?.click();
  });
  document.getElementById('personal-import-input')?.addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    try {
      const data = await importTimelineFromFile(file, page === 'creator' ? 'creator' : 'personal');
      state.importOpen = false;
      if (data?.timeline?.edit_url) setBanner(`Imported ${file.name} as a new editable timeline. Edit: ${data.timeline.edit_url}`);
    } catch (error) {
      setBanner('', error.message);
    }
  });
  document.querySelectorAll('[data-action="close-merge-tool"]').forEach((node) => node.addEventListener('click', () => {
    state.mergeToolOpen = false;
    state.mergeToolSourceId = '';
    render();
  }));
  document.getElementById('separate-source')?.addEventListener('change', (event) => { state.mergeToolSourceId = event.target.value; render(); });
  document.querySelectorAll('[data-action="toggle-color-menu"]').forEach((button) => button.addEventListener('click', (event) => {
    event.stopPropagation();
    state.colorMenuOpenId = state.colorMenuOpenId === button.dataset.id ? '' : (button.dataset.id || '');
    renderPersonal();
  }));
  document.querySelectorAll('[data-action="close-modal"]').forEach((node) => node.addEventListener('click', () => {
    state.publishOpen = false;
    render();
  }));
  document.querySelectorAll('[data-action="close-published-manage"]').forEach((node) => node.addEventListener('click', () => {
    state.publishedManageSlug = '';
    render();
  }));
  document.querySelectorAll('[data-action="share-bundle"]').forEach((button) => button.addEventListener('click', () => openShareSheet(button.dataset.url, button.dataset.title || '')));
  document.querySelectorAll('[data-action="manage-published"]').forEach((button) => button.addEventListener('click', () => { state.publishedManageSlug = button.dataset.slug || ''; render(); }));
  document.querySelectorAll('[data-action="edit-subscription"]').forEach((button) => button.addEventListener('click', async () => {
    try {
      const data = await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${encodeURIComponent(button.dataset.id)}/editor`, { method: 'POST', body: '{}' });
      window.location.href = data.edit_url;
    } catch (error) {
      setBanner('', error.message);
    }
  }));
  document.getElementById('import-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions`, {
        method: 'POST',
        body: JSON.stringify({ title: form.get('title') || '', url: form.get('url') || '', calendar_id: state.personal?.active_calendar_id || '', workspace: currentWorkspaceMode() === 'creator' ? 'creator' : 'personal' }),
      });
      await loadWorkspace();
      state.importOpen = false;
      setBanner('Subscription added.');
    } catch (error) {
      setBanner('', error.message);
    }
  });
  document.querySelectorAll('[data-action="toggle-visible"]').forEach((button) => button.addEventListener('click', async () => {
    try {
      const item = state.personal.subscriptions.find((entry) => entry.id === button.dataset.id);
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${button.dataset.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ visible: !item.visible }),
      });
      await loadWorkspace();
      setBanner('Visibility updated.');
    } catch (error) {
      setBanner('', error.message);
    }
  }));
  document.querySelector('[data-action="toggle-all-visible"]')?.addEventListener('click', async (event) => {
    try {
      const visible = event.currentTarget.dataset.visible === 'true';
      for (const item of state.personal.subscriptions) {
        await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${encodeURIComponent(item.id)}`, {
          method: 'PATCH',
          body: JSON.stringify({ visible }),
        });
      }
      await loadWorkspace();
      setBanner('Visibility updated.');
    } catch (error) {
      setBanner('', error.message);
    }
  });
  document.querySelectorAll('[data-action="move-workspace"]').forEach((button) => button.addEventListener('click', async () => {
    try {
      const workspace = button.dataset.workspace || 'personal';
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${button.dataset.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ workspace }),
      });
      await loadWorkspace();
      setBanner(workspace === 'creator' ? 'Timeline moved to creator management.' : workspace === 'archive' ? 'Timeline moved to archive page.' : 'Timeline moved to the personal page.');
    } catch (error) {
      setBanner('', error.message);
    }
  }));
  document.querySelectorAll('[data-action="subscription-color-choice"]').forEach((input) => input.addEventListener('click', async (event) => {
    event.stopPropagation();
    const nextColor = timelineColor(input.dataset.color || '');
    try {
      updateSubscriptionColorState(input.dataset.id, nextColor);
      updateSubscriptionColorDom(input.dataset.id, nextColor);
      state.colorMenuOpenId = '';
      renderPersonal();
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${input.dataset.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ color: nextColor }),
      });
      await refreshWorkspaceCalendarColors();
      showToast('Color updated');
    } catch (error) {
      setBanner('', error.message);
    }
  }));
  document.querySelectorAll('[data-action="trash"]').forEach((button) => button.addEventListener('click', async () => {
    try {
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${button.dataset.id}/trash`, { method: 'POST', body: '{}' });
      await loadWorkspace();
      setBanner('Subscription moved to trash.');
    } catch (error) {
      setBanner('', error.message);
    }
  }));
  document.querySelectorAll('[data-action="restore"]').forEach((button) => button.addEventListener('click', async () => {
    try {
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${button.dataset.id}/restore`, { method: 'POST', body: '{}' });
      await loadWorkspace();
      setBanner('Subscription restored.');
    } catch (error) {
      setBanner('', error.message);
    }
  }));
  document.querySelectorAll('[data-action="detach-delete"]').forEach((button) => button.addEventListener('click', async () => {
    const ok = window.confirm('Detach&delete keeps the timeline on the server, but removes your editing access. Continue?');
    if (!ok) return;
    try {
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${button.dataset.id}?mode=detach`, { method: 'DELETE' });
      await loadWorkspace();
      setBanner('Timeline detached. It stays on the server, but your editing access is removed.');
    } catch (error) {
      setBanner('', error.message);
    }
  }));
  document.querySelectorAll('[data-action="permanent-delete"]').forEach((button) => button.addEventListener('click', async () => {
    const ok = window.confirm('Permanent delete removes this timeline from the server. Related published or merged timelines will lose it. Continue?');
    if (!ok) return;
    try {
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${button.dataset.id}?mode=permanent`, { method: 'DELETE' });
      await loadWorkspace();
      setBanner('Timeline permanently deleted from the server.');
    } catch (error) {
      setBanner('', error.message);
    }
  }));
  document.querySelectorAll('[data-action="delete"]').forEach((button) => button.addEventListener('click', async () => {
    const ok = window.confirm('Delete this trashed timeline permanently from the server?');
    if (!ok) return;
    try {
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${button.dataset.id}?mode=permanent`, { method: 'DELETE' });
      await loadWorkspace();
      setBanner('Timeline permanently deleted from the server.');
    } catch (error) {
      setBanner('', error.message);
    }
  }));
  document.querySelector('[data-action="empty-trash"]')?.addEventListener('click', async () => {
    const items = state.personal?.trash || [];
    if (!items.length) return;
    const ok = window.confirm('Delete all trashed timelines permanently from the server?');
    if (!ok) return;
    try {
      for (const item of items) {
        await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${encodeURIComponent(item.id)}?mode=permanent`, { method: 'DELETE' });
      }
      await loadWorkspace();
      setBanner('Timeline permanently deleted from the server.');
    } catch (error) {
      setBanner('', error.message);
    }
  });
  document.querySelector('[data-action="submit-merge"]')?.addEventListener('click', async () => {
    const title = document.getElementById('merge-title').value.trim();
    const selected = Array.from(document.querySelectorAll('[data-merge-sub]:checked')).map((node) => node.value);
    try {
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/merge`, {
        method: 'POST',
        body: JSON.stringify({ title, subscription_ids: selected }),
      });
      await loadWorkspace();
      state.mergeToolOpen = false;
      state.mergeToolSourceId = '';
      setBanner('Merged timeline created. Selected merged folders were flattened into the new one.');
    } catch (error) {
      setBanner('', error.message);
    }
  });
  document.querySelector('[data-action="submit-separate"]')?.addEventListener('click', async () => {
    const sourceId = document.getElementById('separate-source')?.value;
    const selected = Array.from(document.querySelectorAll('[data-separate-sub]:checked')).map((node) => node.value).filter(Boolean);
    const trashOriginal = Boolean(document.getElementById('separate-trash-original')?.checked);
    if (!sourceId) return;
    try {
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${encodeURIComponent(sourceId)}/separate`, {
        method: 'POST',
        body: JSON.stringify({ subscription_ids: selected, trash_original: trashOriginal }),
      });
      await loadWorkspace();
      state.mergeToolOpen = false;
      state.mergeToolSourceId = '';
      setBanner('Selected internal timelines were extracted from the merged folder.');
    } catch (error) {
      setBanner('', error.message);
    }
  });
  document.querySelector('[data-action="save-published-manage"]')?.addEventListener('click', async () => {
    const slug = state.publishedManageSlug;
    if (!slug) return;
    const visibility = document.getElementById('published-visibility')?.value || 'public';
    const invited = (document.getElementById('published-invited')?.value || '').split(',').map((item) => item.trim()).filter(Boolean);
    const hashtags = document.getElementById('published-hashtags')?.value || '';
    try {
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/published/${encodeURIComponent(slug)}`, {
        method: 'PATCH',
        body: JSON.stringify({ visibility, invited, hashtags }),
      });
      await loadWorkspace();
      state.publishedManageSlug = '';
      setBanner('Published access updated.');
    } catch (error) {
      setBanner('', error.message);
    }
  });
  document.querySelector('[data-action="archive-published"]')?.addEventListener('click', async () => {
    const slug = state.publishedManageSlug;
    if (!slug) return;
    const ok = window.confirm('Archive keeps this published timeline on the server for current subscribers, removes it from public publishing, and moves it to the archive page. Continue?');
    if (!ok) return;
    try {
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/published/${encodeURIComponent(slug)}?mode=archive`, { method: 'DELETE' });
      await loadWorkspace();
      state.publishedManageSlug = '';
      setBanner('Published timeline archived.');
    } catch (error) {
      setBanner('', error.message);
    }
  });
  document.querySelector('[data-action="remove-published"]')?.addEventListener('click', async () => {
    const slug = state.publishedManageSlug;
    if (!slug) return;
    const ok = window.confirm('Removed from publishing hides this timeline from new users, but current subscribers can still access it. Continue?');
    if (!ok) return;
    try {
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/published/${encodeURIComponent(slug)}?mode=remove`, { method: 'DELETE' });
      await loadWorkspace();
      state.publishedManageSlug = '';
      setBanner('Published timeline removed from public publishing.');
    } catch (error) {
      setBanner('', error.message);
    }
  });
  document.querySelector('[data-action="permanent-remove-published"]')?.addEventListener('click', async () => {
    const slug = state.publishedManageSlug;
    if (!slug) return;
    const ok = window.confirm('Permanent remove gives up your permission to manage this published timeline and removes it from your published list. Current subscribers can still access it. Continue?');
    if (!ok) return;
    try {
      await api(`/api/personal/${encodeURIComponent(currentAcct())}/published/${encodeURIComponent(slug)}?mode=permanent`, { method: 'DELETE' });
      await loadWorkspace();
      state.publishedManageSlug = '';
      setBanner('Published timeline permanently removed from your management.');
    } catch (error) {
      setBanner('', error.message);
    }
  });
  if (officialRegistryMode) {
    let draftIndex = 0;
    const draftBody = document.getElementById('official-registry-drafts');
    document.querySelector('[data-action="official-add-row"]')?.addEventListener('click', () => {
      if (!draftBody) return;
      draftIndex += 1;
      draftBody.insertAdjacentHTML('beforeend', officialDraftRowMarkup(draftIndex));
    });
    root.querySelectorAll('[data-action="official-save-row"]').forEach((button) => button.addEventListener('click', async () => {
      const row = button.closest('tr');
      if (!row) return;
      const id = button.dataset.id || '';
      const payload = {
        source_code: row.querySelector('[data-field="source_code"]')?.value || '',
        title: row.querySelector('[data-field="title"]')?.value || '',
        url: row.querySelector('[data-field="url"]')?.value || '',
        source_format: row.querySelector('[data-field="source_format"]')?.value || '',
        hashtags: parseHashtagText(row.querySelector('[data-field="hashtags"]')?.value || ''),
        description: row.querySelector('[data-field="description"]')?.value || '',
        visible: (row.querySelector('[data-field="visible"]')?.value || 'true') === 'true',
      };
      try {
        await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        });
        await loadWorkspace();
        setBanner('Official source row saved.');
      } catch (error) {
        setBanner('', error.message || 'Failed to save official row');
      }
    }));
    root.querySelectorAll('[data-action="official-trash-row"]').forEach((button) => button.addEventListener('click', async () => {
      const id = button.dataset.id || '';
      const ok = window.confirm('Move this official row to trash?');
      if (!ok) return;
      try {
        await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions/${encodeURIComponent(id)}/trash`, { method: 'POST', body: '{}' });
        await loadWorkspace();
        setBanner('Official source row moved to trash.');
      } catch (error) {
        setBanner('', error.message || 'Failed to trash official row');
      }
    }));
    root.addEventListener('click', async (event) => {
      const createBtn = event.target.closest('[data-action="official-create-row"]');
      if (createBtn) {
        const row = createBtn.closest('tr');
        if (!row) return;
        const payload = {
          official: true,
          source_code: row.querySelector('[data-field="source_code"]')?.value || '',
          title: row.querySelector('[data-field="title"]')?.value || '',
          url: row.querySelector('[data-field="url"]')?.value || '',
          source_format: row.querySelector('[data-field="source_format"]')?.value || '',
          hashtags: parseHashtagText(row.querySelector('[data-field="hashtags"]')?.value || ''),
          description: row.querySelector('[data-field="description"]')?.value || '',
          visible: (row.querySelector('[data-field="visible"]')?.value || 'true') === 'true',
        };
        try {
          await api(`/api/personal/${encodeURIComponent(currentAcct())}/subscriptions`, {
            method: 'POST',
            body: JSON.stringify(payload),
          });
          await loadWorkspace();
          setBanner('Official source row created.');
        } catch (error) {
          setBanner('', error.message || 'Failed to create official row');
        }
        return;
      }
      const removeBtn = event.target.closest('[data-action="official-remove-draft"]');
      if (removeBtn) removeBtn.closest('tr')?.remove();
    });
  }

  document.querySelector('[data-action="submit-publish"]')?.addEventListener('click', async () => {
    const title = document.getElementById('publish-title').value.trim();
    const selected = Array.from(document.querySelectorAll('[data-publish-sub]:checked')).map((node) => node.value);
    try {
      const invited = (document.getElementById('publish-invited')?.value || '').split(',').map((item) => item.trim()).filter(Boolean);
      const visibility = document.getElementById('publish-visibility')?.value || 'public';
      const hashtags = document.getElementById('publish-hashtags')?.value || '';
      const bundle = await api(`/api/personal/${encodeURIComponent(currentAcct())}/published`, {
        method: 'POST',
        body: JSON.stringify({ title, subscription_ids: selected, visibility, invited, hashtags, calendar_id: state.personal?.active_calendar_id || '' }),
      });
      await loadWorkspace();
      state.publishOpen = false;
      setBanner(`Published link created: ${bundle.share_url}`);
    } catch (error) {
      setBanner('', error.message);
    }
  });  initReadonlyCalendar('personal-calendar', state.personal.visible_sources || [], 'personal-calendar-hint');
}

function emptyEvent(startIso, endIso) {
  return window.TimeGridCalendarEditor?.emptyEvent?.({
    start: startIso || new Date().toISOString(),
    end: endIso || new Date(Date.now() + 60 * 60 * 1000).toISOString(),
    timeline: state.timeline,
  }) || {
    id: `evt_${Math.random().toString(36).slice(2, 10)}`,
    title: 'New event',
    start: startIso || new Date().toISOString(),
    end: endIso || new Date(Date.now() + 60 * 60 * 1000).toISOString(),
    description: '',
    location: '',
    url: '',
    recurrence: null,
    exdates: [],
    overrides: [],
    editable: true,
    source_timeline_id: state.timeline?.overlay_timeline_id || state.timeline?.id || '',
    source_subscription_id: state.timeline?.overlay_subscription_id || '',
    source_title: state.timeline?.title || 'New timeline',
    source_color: state.timeline?.overlay_color || state.timeline?.color || '',
  };
}

function formDateTimeValue(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const offset = d.getTimezoneOffset();
  const local = new Date(d.getTime() - offset * 60000);
  return local.toISOString().slice(0, 16);
}

function recurrenceUntilDateValue(iso) {
  return iso ? iso.slice(0, 10) : '';
}

function normalizeTimelineEvents() {
  state.timeline.events = window.TimeGridCalendarDomain?.normalizeEvents?.(state.timeline.events || []) || (state.timeline.events || []);
}

function eventDurationMs(event) {
  return window.TimeGridCalendarDomain?.eventDurationMs?.(event) || (new Date(event.end).getTime() - new Date(event.start).getTime());
}

function applyOverride(base, occurrenceId) {
  return window.TimeGridCalendarDomain?.applyOverride?.(base, occurrenceId) || null;
}

function expandEventsList(events) {
  return window.TimeGridCalendarDomain?.expandEventsList?.(events || []) || (events || []);
}

function expandedCalendarEvents() {
  normalizeTimelineEvents();
  return expandEventsList(state.timeline.events || []);
}

function eventColor(item) {
  return item?.source_color || state.timeline?.overlay_color || state.timeline?.color || '';
}

function selectedSeriesEvent() {
  return state.timeline?.events?.find((item) => item.id === state.selectedEventId) || null;
}

function selectedEvent() {
  const base = selectedSeriesEvent();
  if (state.selectedOccurrence && base) {
    return applyOverride(base, state.selectedOccurrence.recurrenceId) || {
      ...base,
      start: state.selectedOccurrence.recurrenceId,
      end: new Date(new Date(state.selectedOccurrence.recurrenceId).getTime() + eventDurationMs(base)).toISOString(),
      recurrence: null,
    };
  }
  return base || state.draftEvent;
}

function recurrenceSummary(event) {
  if (!event?.recurrence?.freq) return 'One-time event';
  const freq = String(event.recurrence.freq).toLowerCase();
  const days = event.recurrence.byweekday?.length ? ` on ${event.recurrence.byweekday.join(', ')}` : '';
  const until = event.recurrence.until ? ` until ${new Date(event.recurrence.until).toLocaleDateString()}` : '';
  return `Repeats ${freq}${days}${until}`;
}


function isRecurringTimelineEvent(event) {
  return !!event?.recurrence?.freq;
}

function timelineEventGroupId(event) {
  return event?.series_group_id || event?.id || '';
}

function timelineSingleEvents() {
  return (state.timeline?.events || []).filter((item) => !isRecurringTimelineEvent(item));
}

function timelineSeriesGroups() {
  const groups = new Map();
  (state.timeline?.events || []).filter(isRecurringTimelineEvent).forEach((item) => {
    const id = timelineEventGroupId(item);
    if (!groups.has(id)) groups.set(id, []);
    groups.get(id).push(item);
  });
  return Array.from(groups.entries()).map(([id, items]) => {
    const sorted = items.sort((a, b) => new Date(a.start) - new Date(b.start));
    const title = sorted.find((item) => item.title)?.title || 'Untitled series';
    return { id, title, items: sorted };
  });
}

function eventEditModal() {
  const modal = state.eventEditModal;
  if (!modal?.id) return '';
  if (modal.kind === 'skipper') return skipperModal(modal.id);
  if (modal.kind === 'series') return seriesManageModal(modal.id);
  return singleEventEditModal(modal.id);
}

function editModalShell(title, body) {
  return `
    <div class="modal-backdrop" data-action="close-event-editor">
      <div class="modal event-edit-modal" onclick="event.stopPropagation()">
        <div class="modal-header">
          <div><div class="eyebrow">Creator editor</div><h2>${escapeHtml(title)}</h2></div>
          <button class="modal-close" data-action="close-event-editor" aria-label="Close editor">×</button>
        </div>
        ${body}
      </div>
    </div>`;
}

function singleEventEditModal(eventId) {
  const item = (state.timeline?.events || []).find((entry) => entry.id === eventId);
  if (!item) return '';
  const disabled = item.editable === false ? 'disabled' : '';
  return editModalShell(item.editable === false ? 'View single event' : 'Edit single event', `
    ${item.editable === false ? '<div class="banner">This source is read-only.</div>' : ''}
    <div class="event-form-grid modal-event-form" data-single-event-id="${escapeHtml(item.id)}">
      <label><div class="muted">Title</div><input id="modal-event-title" value="${escapeHtml(item.title || '')}" ${disabled} /></label>
      <div class="event-form-row event-form-row--split">
        <label><div class="muted">Start</div><input id="modal-event-start" type="datetime-local" value="${escapeHtml(formDateTimeValue(item.start))}" ${disabled} /></label>
        <label><div class="muted">End</div><input id="modal-event-end" type="datetime-local" value="${escapeHtml(formDateTimeValue(item.end))}" ${disabled} /></label>
      </div>
      <label><div class="muted">Location</div><input id="modal-event-location" value="${escapeHtml(item.location || '')}" ${disabled} /></label>
      <label><div class="muted">URL</div><input id="modal-event-url" value="${escapeHtml(item.url || '')}" ${disabled} /></label>
      <label><div class="muted">Description</div><textarea id="modal-event-description" ${disabled}>${escapeHtml(item.description || '')}</textarea></label>
      <div class="modal-actions">
        ${item.editable === false ? '' : '<button class="primary" data-action="save-single-event-modal">Save single event</button><button class="danger" data-action="delete-single-event-modal">Delete single event</button>'}
        <button data-action="close-event-editor">Close</button>
      </div>
    </div>`);
}

function seriesManageModal(groupId) {
  const group = timelineSeriesGroups().find((entry) => entry.id === groupId);
  if (!group) return '';
  const readOnly = group.items.every((item) => item.editable === false);
  return editModalShell(readOnly ? 'View series' : 'Manage series', `
    ${readOnly ? '<div class="banner">Every segment in this series group is read-only.</div>' : '<p class="section-copy">Use segments for breaks in the middle of a course series. Break rows stay in one series and share one title; details can change per break.</p>'}
    <label class="series-title-field"><div class="muted">Series title</div><input id="series-shared-title" value="${escapeHtml(group.title)}" ${readOnly ? 'disabled' : ''} /></label>
    <div class="series-segment-list" data-series-group-id="${escapeHtml(group.id)}">
      ${group.items.map((item, index) => seriesSegmentRow(item, index)).join('')}
    </div>
    <div class="modal-actions">
      ${readOnly ? '' : '<button data-action="add-series-break">Add break</button><button class="primary" data-action="save-series-modal">Save series</button><button data-action="open-skipper-modal">Skipper</button><button class="danger" data-action="delete-series-group-modal">Delete series</button>'}
      <button data-action="close-event-editor">Close</button>
    </div>`);
}

function seriesSegmentRow(item, index) {
  const disabled = item.editable === false ? 'disabled' : '';
  const repeatValue = item.recurrence?.freq || 'WEEKLY';
  return `
    <article class="series-segment-row" data-series-row-id="${escapeHtml(item.id)}">
      <header>
        <strong>${index === 0 ? 'Start date / end date' : `Start date-${index} / end date-${index}`}</strong>
        <div class="sub-actions">${item.editable === false ? '<span class="muted">Read-only</span>' : (index === 0 ? '' : '<button class="danger" data-action="remove-series-segment" data-id="' + escapeHtml(item.id) + '">Remove break</button>')}</div>
      </header>
      <div class="event-form-grid">
        <div class="event-form-row event-form-row--split">
          <label><div class="muted">Start</div><input data-field="start" type="datetime-local" value="${escapeHtml(formDateTimeValue(item.start))}" ${disabled} /></label>
          <label><div class="muted">End</div><input data-field="end" type="datetime-local" value="${escapeHtml(formDateTimeValue(item.end))}" ${disabled} /></label>
        </div>
        <div class="event-form-row event-form-row--split">
          <label><div class="muted">Repeat frequency</div><select data-field="repeat" ${disabled}><option value="WEEKLY" ${repeatValue === 'WEEKLY' ? 'selected' : ''}>Weekly</option><option value="DAILY" ${repeatValue === 'DAILY' ? 'selected' : ''}>Daily</option></select></label>
          <label><div class="muted">Repeat until</div><input data-field="until" type="date" value="${escapeHtml(recurrenceUntilDateValue(item.recurrence?.until || ''))}" ${disabled} /></label>
        </div>
        <label><div class="muted">Location</div><input data-field="location" value="${escapeHtml(item.location || '')}" ${disabled} /></label>
        <label><div class="muted">URL</div><input data-field="url" value="${escapeHtml(item.url || '')}" ${disabled} /></label>
        <label><div class="muted">Description</div><textarea data-field="description" ${disabled}>${escapeHtml(item.description || '')}</textarea></label>
      </div>
    </article>`;
}

function skipperModal(groupId) {
  const group = timelineSeriesGroups().find((entry) => entry.id === groupId);
  if (!group) return '';
  const rows = group.items.flatMap((item) => (window.TimeGridCalendarDomain?.listSeriesOccurrences?.({ ...item, exdates: [] }, { limit: 80, horizonDays: 1460 }) || []).map((occurrence) => ({ item, occurrence })))
    .sort((a, b) => new Date(a.occurrence.start) - new Date(b.occurrence.start));
  return editModalShell('Skipper', `
    <p class="section-copy">Uncheck a time period to skip that occurrence in this series. This is the simple gap tool.</p>
    <div class="skipper-list" data-series-group-id="${escapeHtml(group.id)}">
      ${rows.length ? rows.map(({ item, occurrence }) => `
        <label class="skipper-row">
          <input type="checkbox" data-series-id="${escapeHtml(item.id)}" data-occurrence-id="${escapeHtml(occurrence._occurrenceId || occurrence.start)}" ${(item.exdates || []).includes(occurrence._occurrenceId || occurrence.start) ? '' : 'checked'} ${item.editable === false ? 'disabled' : ''} />
          <span><strong>${escapeHtml(occurrence.title || item.title || 'Untitled event')}</strong><small>${new Date(occurrence.start).toLocaleString()} to ${new Date(occurrence.end).toLocaleString()}</small></span>
        </label>`).join('') : '<div class="empty">No upcoming periods found for this series.</div>'}
    </div>
    <div class="modal-actions"><button class="primary" data-action="save-skipper-modal">Save skipper</button><button data-action="open-series-modal" data-id="${escapeHtml(group.id)}">Back to series</button><button data-action="close-event-editor">Close</button></div>`);
}

function selectedEventReadOnly() {
  const event = selectedEvent();
  return !!(event && event.editable === false);
}

function recurringConversionChoices(event) {
  if (!event?.recurrence?.freq) return [];
  return window.TimeGridCalendarDomain?.listSeriesOccurrences?.(event, { limit: 18, horizonDays: 1460 }) || [];
}

function ensureRecurrenceConversionState(event, repeatValue) {
  if (!event?.recurrence?.freq || repeatValue !== 'none' || state.selectedOccurrence) {
    state.recurrenceConversion = null;
    return null;
  }
  const choices = recurringConversionChoices(event);
  const validIds = new Set(choices.map((item) => item.start));
  const mode = state.recurrenceConversion?.mode === 'multiple' ? 'multiple' : 'single';
  let occurrenceIds = Array.isArray(state.recurrenceConversion?.occurrenceIds)
    ? state.recurrenceConversion.occurrenceIds.filter((value) => validIds.has(value))
    : [];
  if (!occurrenceIds.length && choices.length) {
    const upcoming = choices.find((item) => new Date(item.start) >= new Date());
    occurrenceIds = [(upcoming || choices[choices.length - 1] || choices[0]).start];
  }
  if (mode === 'single' && occurrenceIds.length > 1) occurrenceIds = [occurrenceIds[0]];
  state.recurrenceConversion = { mode, occurrenceIds };
  return state.recurrenceConversion;
}

function editorContext() {
  return window.TimeGridScheduleXFrame?.buildLegacyEditorContext?.(state) || {
    mode: state.selectedOccurrence ? 'edit_occurrence' : state.selectedEventId ? 'edit_series' : 'create_single',
    isReadOnly: selectedEventReadOnly(),
    labels: {
      panelTitle: state.selectedOccurrence ? 'Edit occurrence' : (state.selectedEventId ? 'Edit series' : 'Create event'),
      saveAction: state.selectedEventId || state.selectedOccurrence ? 'Save changes' : 'Add event',
    },
  };
}

function preferredCalendarDate() {
  if (state.timelineDate) return state.timelineDate;
  return new Date().toISOString();
}

function calendarFocus(events, options = {}) {
  const automatic = window.TimeGridCalendarDomain?.findCalendarFocus?.(events || []) || {
    selectedDate: new Date().toISOString(),
    legacyView: 'dayGridMonth',
    hint: '',
  };
  return {
    selectedDate: options.selectedDate || automatic.selectedDate,
    initialView: options.initialView || automatic.legacyView,
    hint: automatic.hint || '',
  };
}

function calendarGapHint(events, rangeStartIso, rangeEndIso) {
  if (!(events || []).length) return 'No events yet.';
  if (!rangeStartIso || !rangeEndIso) return '';
  const rangeStart = new Date(rangeStartIso);
  const rangeEnd = new Date(rangeEndIso);
  if (Number.isNaN(rangeStart.getTime()) || Number.isNaN(rangeEnd.getTime())) return '';
  const expanded = (events || []).filter((item) => item.start).map((item) => ({ ...item, _startDate: new Date(item.start) })).filter((item) => !Number.isNaN(item._startDate.getTime())).sort((a, b) => a._startDate - b._startDate);
  const hasVisible = expanded.some((item) => item._startDate >= rangeStart && item._startDate < rangeEnd);
  if (hasVisible) return '';
  const next = expanded.find((item) => item._startDate >= rangeEnd);
  if (!next) return 'No events in future.';
  const quietUntil = new Date(rangeStart.getTime() + 14 * 86400000);
  if (next._startDate < quietUntil) return '';
  return `Next event: ${next._startDate.toLocaleString()}`;
}

function setCalendarHint(elementId, message) {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (!message) {
    el.textContent = '';
    el.classList.add('hidden');
    return;
  }
  el.textContent = message;
  el.classList.remove('hidden');
}

function timelineToolbar() {
  const title = state.timeline?.title || 'Timeline editor';
  const origin = currentTimelineOrigin();
  const backLabel = origin === 'creator' ? 'Back to creator' : 'Back to personal';
  const backHref = `/u/${encodeURIComponent(state.timeline?.owner_acct || document.body.dataset.acct || location.pathname.split('/')[2] || '')}${origin === 'creator' ? '/creator' : ''}`;
  const intro = state.timeline?.kind === 'wrapper'
    ? 'Edit a merged timeline or shell calendar here. New events go into your own internal timeline, while existing owned child timelines stay editable in place.'
    : 'Create or import repeating course schedules, then publish them as your own ICS subscription for the personal calendar page.';
  return `
    <header class="topbar">
      <div class="topbar-main">
        <div class="brand">
          <div class="eyebrow">Timeline editor</div>
          <h1>${escapeHtml(title)}</h1>
          <p>${escapeHtml(intro)}</p>
        </div>
        <div class="topbar-utility">
          <button data-action="logout">Sign out</button>
          ${notificationsButton()}
        </div>
      </div>
      ${sectionNav()}
      <div class="toolbar toolbar--context">
        <button data-action="import-editor">Import file</button>
        <button class="primary" data-action="save-timeline">Save timeline</button>
        <button data-action="return-workspace" data-href="${escapeHtml(backHref)}">${escapeHtml(backLabel)}</button>
      </div>
    </header>`;
}

function timelineSidebarMarkup() {
  const event = state.draftEvent;
  const readOnly = false;
  const disabled = '';
  const repeatValue = event?.recurrence?.freq || 'none';
  const showRepeatUntil = repeatValue !== 'none';
  const advancedOpen = !!(event?.location || event?.url || event?.description);
  const singles = timelineSingleEvents();
  const seriesGroups = timelineSeriesGroups();

  return `
    ${mastodonProvisioningBanner()}
    <section class="event-composer-card">
      <div class="section-header"><h3>Create event</h3></div>
      <p class="section-copy">Use this panel only for adding new events. Existing single events and series are edited from their own buttons below.</p>
      ${readOnly ? '<div class="banner">Read-only source events stay visible here, but only your own internal timelines can be changed or deleted.</div>' : ''}
      <div class="event-form-grid">
        <label><div class="muted">Title</div><input id="event-title" value="${escapeHtml(event?.title || '')}" ${disabled} /></label>
        <div class="event-form-row">
          <label><div class="muted">Start</div><input id="event-start" type="datetime-local" value="${escapeHtml(formDateTimeValue(event?.start))}" ${disabled} /></label>
          <label><div class="muted">End</div><input id="event-end" type="datetime-local" value="${escapeHtml(formDateTimeValue(event?.end))}" ${disabled} /></label>
        </div>
        <div class="event-form-row"><label><div class="muted">Repeat</div><select id="event-repeat" ${disabled}><option value="none" ${repeatValue === 'none' ? 'selected' : ''}>No repeat</option><option value="WEEKLY" ${repeatValue === 'WEEKLY' ? 'selected' : ''}>Weekly</option><option value="DAILY" ${repeatValue === 'DAILY' ? 'selected' : ''}>Daily</option></select></label><label id="event-repeat-until-wrap" class="${showRepeatUntil ? '' : 'field-hidden'}"><div class="muted">Repeat until</div><input id="event-repeat-until" type="date" value="${escapeHtml(recurrenceUntilDateValue(event?.recurrence?.until || ''))}" ${disabled} /></label></div>
        <details class="event-advanced"${advancedOpen ? ' open' : ''}>
          <summary>More details</summary>
          <div class="event-supporting">
            <label><div class="muted">Location</div><input id="event-location" value="${escapeHtml(event?.location || '')}" ${disabled} /></label>
            <label><div class="muted">URL</div><input id="event-url" value="${escapeHtml(event?.url || '')}" ${disabled} /></label>
            <label><div class="muted">Description</div><textarea id="event-description" ${disabled}>${escapeHtml(event?.description || '')}</textarea></label>
          </div>
        </details>
        <div class="event-actions">
          ${readOnly ? '' : `<button class="primary" data-action="apply-event">Add event</button>`}
          ${state.draftEvent ? '<button data-action="clear-event">Clear form</button>' : ''}
        </div>
      </div>
    </section>
    <details class="timeline-settings timeline-settings-card">
      <summary>Timeline settings</summary>
      <div class="meta-list">
        <label><div class="muted">Timeline title</div><input id="timeline-title" value="${escapeHtml(state.timeline.title || '')}" placeholder="My timeline" /></label>
        <label><div class="muted">Description</div><textarea id="timeline-description" placeholder="What is this timeline for?">${escapeHtml(state.timeline.description || '')}</textarea></label>
        <label><div class="muted">Timeline color</div><input id="timeline-color" class="color-picker" type="color" value="${escapeHtml(state.timeline.color || state.timeline.overlay_color || '#1f7a8c')}" /></label>
        <a class="button" href="${escapeHtml(state.timeline.ics_url || '#')}" target="_blank" rel="noreferrer">Open ICS feed</a>
      </div>
    </details>
    <details class="timeline-series timeline-series-card"${singles.length ? ' open' : ''}>
      <summary>Single events</summary>
      <div class="sub-list">
        ${singles.length ? singles.map((item) => `
          <article class="sub-card">
            <header>
              <div>
                <strong>${escapeHtml(item.title || 'Untitled event')}</strong>
                <div class="muted">${new Date(item.start).toLocaleString()} to ${new Date(item.end).toLocaleString()}</div>
                <div class="muted">${item.editable === false ? 'Read-only source' : 'Editable source'}${item.source_title ? ` • ${escapeHtml(item.source_title)}` : ''}</div>
              </div>
              <div class="sub-actions"><button data-action="open-single-modal" data-id="${item.id}">${item.editable === false ? 'View' : 'Edit'}</button>${item.editable === false ? '' : `<button class="danger" data-action="delete-event" data-id="${item.id}">Delete</button>`}</div>
            </header>
          </article>`).join('') : '<div class="empty">No single events yet.</div>'}
      </div>
    </details>
    <details class="timeline-series timeline-series-card"${seriesGroups.length ? ' open' : ''}>
      <summary>Series</summary>
      <div class="sub-list">
        ${seriesGroups.length ? seriesGroups.map((group) => {
          const first = group.items[0];
          const readOnlyGroup = group.items.every((item) => item.editable === false);
          return `
          <article class="sub-card">
            <header>
              <div>
                <strong>${escapeHtml(first.title || 'Untitled series')}</strong>
                <div class="muted">${group.items.length} ${group.items.length === 1 ? 'segment' : 'segments'} • ${escapeHtml(recurrenceSummary(first))}</div>
                <div class="muted">${readOnlyGroup ? 'Read-only source' : 'Editable source'}${first.source_title ? ` • ${escapeHtml(first.source_title)}` : ''}</div>
              </div>
              <div class="sub-actions">
                <button data-action="open-series-modal" data-id="${group.id}">${readOnlyGroup ? 'View series' : 'Edit series'}</button>
                ${readOnlyGroup ? '' : `<button data-action="open-skipper-modal" data-id="${group.id}">Skipper</button><button class="danger" data-action="delete-series-group" data-id="${group.id}">Delete series</button>`}
              </div>
            </header>
          </article>`;
        }).join('') : '<div class="empty">No series yet.</div>'}
      </div>
    </details>`;
}

function renderTimelineSidebarOnly() {
  const shell = document.getElementById('timeline-sidebar-shell');
  if (!shell) {
    renderTimeline();
    return;
  }
  shell.innerHTML = timelineSidebarMarkup();
  window.TimeGridTimelineController?.bindTimelineEditorActions?.({
    state,
    renderTimeline,
    renderTimelineSidebarOnly,
    renderTimelineEditorOnly,
    setBanner,
    saveTimeline,
    logout,
    importTimelineFromFile,
    emptyEvent,
    selectedSeriesEvent,
    selectedEventReadOnly,
    sidebarOnly: true,
  });
}

function timelineOverlayMarkup() {
  return `
    ${notificationsModal()}
    ${eventDetailModal()}
    ${eventEditModal()}`;
}

function renderTimelineEditorOnly() {
  const shell = document.getElementById('timeline-sidebar-shell');
  const overlay = document.getElementById('timeline-overlay-shell');
  if (!shell || !overlay) {
    renderTimeline();
    return;
  }
  shell.innerHTML = timelineSidebarMarkup();
  overlay.innerHTML = timelineOverlayMarkup();
  window.TimeGridTimelineController?.bindTimelineEditorActions?.({
    state,
    renderTimeline,
    renderTimelineSidebarOnly,
    renderTimelineEditorOnly,
    setBanner,
    saveTimeline,
    logout,
    importTimelineFromFile,
    emptyEvent,
    selectedSeriesEvent,
    selectedEventReadOnly,
    sidebarOnly: true,
  });
  bindNoticeActions(renderTimelineEditorOnly);
  bindEventDetailActions();
}

function renderTimeline() {
  normalizeTimelineEvents();
  const acct = currentAcct();
  root.innerHTML = `
    <div class="page-shell timeline-page-shell">
      ${timelineToolbar()}
      <div class="editor-layout">
        <aside id="timeline-sidebar-shell" class="sidebar editor-sidebar">${timelineSidebarMarkup()}</aside>
        <main class="main-panel editor-main">
          ${calendarTabs()}
          <div class="editor-stage">
            <div class="section-header"><div><div class="eyebrow">Timeline calendar</div><h2>${escapeHtml(state.timeline.title || 'New timeline')}</h2><p class="section-copy">Double-click to create. Click an event to edit.</p></div><div class="toolbar"><button data-action="new-event">New event</button></div></div>
            ${state.timelineHint ? `<div id="timeline-calendar-hint" class="calendar-tip">${escapeHtml(state.timelineHint)}</div>` : '<div id="timeline-calendar-hint" class="calendar-tip hidden"></div>'}<div id="timeline-calendar"></div>
          </div>
        </main>
      </div>
      <div id="timeline-overlay-shell">${timelineOverlayMarkup()}</div>
      <input id="timeline-import-input" type="file" accept=".ics,.ical,.csv,text/calendar,text/csv" class="hidden" />
    </div>`;

  window.TimeGridTimelineController?.bindTimelineEditorActions?.({
    state,
    renderTimeline,
    renderTimelineSidebarOnly,
    renderTimelineEditorOnly,
    setBanner,
    saveTimeline,
    logout,
    importTimelineFromFile,
    emptyEvent,
    selectedSeriesEvent,
    selectedEventReadOnly,
  });
  bindCalendarTabActions();
  bindNoticeActions(renderTimeline);
  bindEventDetailActions();
  initCalendar();
}

function initCalendar() {
  const el = document.getElementById('timeline-calendar');
  if (!el) return;
  el.classList.add('is-refreshing');
  window.TimeGridScheduleXCalendar?.destroy?.(el);
  const events = expandedCalendarEvents().map((item) => ({
    ...item,
    timeline_color: eventColor(item),
  }));
  const focus = calendarFocus(events, {
    selectedDate: state.timelineDate,
    initialView: state.timelineView || savedCalendarView(`timeline:${state.timeline?.id || 'new'}`) || 'dayGridMonth',
  });
  state.timelineHint = focus.hint;
  setCalendarHint('timeline-calendar-hint', state.timelineHint);
  state.calendar = window.TimeGridScheduleXCalendar?.mountEditor?.(el, {
    initialView: focus.initialView,
    selectedDate: (focus.selectedDate || preferredCalendarDate()).slice(0, 10),
    events,
    weekOptions: { gridHeight: responsiveCalendarGridHeight() },
    onRangeUpdate(range) {
      state.timelineView = range.legacyView || state.timelineView;
      saveCalendarView(`timeline:${state.timeline?.id || 'new'}`, state.timelineView);
      state.timelineDate = range.startIso || state.timelineDate;
      state.timelineHint = calendarGapHint(events, range.startIso || '', range.endIso || '');
      setCalendarHint('timeline-calendar-hint', state.timelineHint);
    },
    onSelectedDateUpdate(date) {
      state.timelineDate = `${date.toString()}T00:00:00.000Z`;
    },
    onDoubleClickDate(payload) {
      state.selectedEventId = null;
      state.selectedOccurrence = null;
      state.draftEvent = emptyEvent(payload.startIso, payload.endIso);
      state.recurrenceConversion = null;
      renderTimelineSidebarOnly();
    },
    onDoubleClickDateTime(payload) {
      state.selectedEventId = null;
      state.selectedOccurrence = null;
      state.draftEvent = emptyEvent(payload.startIso, payload.endIso);
      state.recurrenceConversion = null;
      renderTimelineSidebarOnly();
    },
    onEditEvent(event) {
      const parsedEventId = window.TimeGridCalendarDomain?.parseOccurrenceEventId?.(event.id) || { seriesId: String(event.id || ''), occurrenceId: '' };
      const seriesId = parsedEventId.seriesId;
      const occurrenceId = parsedEventId.occurrenceId;
      state.selectedEventId = seriesId;
      state.selectedOccurrence = occurrenceId ? { recurrenceId: occurrenceId } : null;
      state.draftEvent = null;
      state.recurrenceConversion = null;
      const base = state.timeline?.events?.find((item) => item.id === seriesId);
      state.eventEditModal = base?.recurrence?.freq ? { kind: 'series', id: timelineEventGroupId(base) } : { kind: 'single', id: seriesId };
      renderTimelineEditorOnly();
    },
    onEventClick(event) {
      const parsedEventId = window.TimeGridCalendarDomain?.parseOccurrenceEventId?.(event.id) || { seriesId: String(event.id || ''), occurrenceId: '' };
      const seriesId = parsedEventId.seriesId;
      const occurrenceId = parsedEventId.occurrenceId;
      state.selectedEventId = seriesId;
      state.selectedOccurrence = occurrenceId ? { recurrenceId: occurrenceId } : null;
      state.draftEvent = null;
      state.recurrenceConversion = null;
      const base = state.timeline?.events?.find((item) => item.id === seriesId);
      state.eventEditModal = base?.recurrence?.freq ? { kind: 'series', id: timelineEventGroupId(base) } : { kind: 'single', id: seriesId };
      renderTimelineEditorOnly();
    },
    enableDragAndDrop: false,
    enableResize: false,
    enableEventModal: false,
    useNativeEventModal: false,
  }) || null;
  requestAnimationFrame(() => el.classList.remove('is-refreshing'));
}

async function saveTimeline(options = {}) {
  const { silent = false } = options;
  const title = document.getElementById('timeline-title').value.trim() || 'Untitled timeline';
  const description = document.getElementById('timeline-description').value.trim();
  const color = document.getElementById('timeline-color')?.value || state.timeline.color || state.timeline.overlay_color || '';
  const requestedCalendarId = new URLSearchParams(window.location.search).get('calendar_id') || '';
  const payload = { title, description, color, events: state.timeline.events || [], calendar_id: state.personal?.active_calendar_id || state.timeline.calendar_id || requestedCalendarId, workspace: currentTimelineOrigin() };
  try {
    if (state.timeline.id) {
      const data = await api(`/api/personal/${encodeURIComponent(currentAcct())}/timelines/${encodeURIComponent(state.timeline.id)}`, { method: 'PATCH', body: JSON.stringify(payload) });
      state.timeline = data.timeline;
      await hydrateWrapperTimeline();
      state.selectedOccurrence = null;
      const savedUrl = data.subscription?.url || data.timeline?.ics_url || '';
      if (!silent) setBanner(savedUrl ? `Timeline saved. Subscription feed updated: ${savedUrl}` : 'Timeline saved.');
      return data;
    } else {
      const data = await api(`/api/personal/${encodeURIComponent(currentAcct())}/timelines`, { method: 'POST', body: JSON.stringify(payload) });
      window.location.href = data.timeline.edit_url;
      return data;
    }
  } catch (error) {
    setBanner('', error.message);
    throw error;
  }
}


function splitIcsLines(text) {
  const raw = String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
  const out = [];
  for (const line of raw) {
    if (!line) continue;
    if ((line.startsWith(' ') || line.startsWith('\t')) && out.length) out[out.length - 1] += line.slice(1);
    else out.push(line);
  }
  return out;
}

function parseIcsDate(value) {
  const v = String(value || '').trim();
  if (!v) return '';
  if (/^\d{8}T\d{6}Z$/.test(v)) {
    return `${v.slice(0,4)}-${v.slice(4,6)}-${v.slice(6,8)}T${v.slice(9,11)}:${v.slice(11,13)}:${v.slice(13,15)}Z`;
  }
  if (/^\d{8}T\d{6}$/.test(v)) {
    return new Date(`${v.slice(0,4)}-${v.slice(4,6)}-${v.slice(6,8)}T${v.slice(9,11)}:${v.slice(11,13)}:${v.slice(13,15)}`).toISOString();
  }
  if (/^\d{8}$/.test(v)) {
    return new Date(`${v.slice(0,4)}-${v.slice(4,6)}-${v.slice(6,8)}T00:00:00`).toISOString();
  }
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? '' : d.toISOString();
}

function unescapeIcsText(value) {
  return String(value || '')
    .replace(/\\n/gi, '\n')
    .replace(/\\,/g, ',')
    .replace(/\\;/g, ';')
    .replace(/\\\\/g, '\\');
}

function parseRRule(value) {
  const rule = {};
  for (const part of String(value || '').split(';')) {
    const [key, raw] = part.split('=');
    if (!key || !raw) continue;
    const upper = key.toUpperCase();
    if (upper === 'FREQ') rule.freq = raw.toUpperCase();
    if (upper === 'INTERVAL') rule.interval = Number(raw) || 1;
    if (upper === 'UNTIL') rule.until = parseIcsDate(raw);
    if (upper === 'COUNT') rule.count = Number(raw) || null;
    if (upper === 'BYDAY') rule.byweekday = raw.split(',').map((item) => item.toUpperCase());
  }
  return rule.freq ? rule : null;
}

function parseIcsFile(text, fallbackTitle) {
  const lines = splitIcsLines(text);
  const calendarTitle = fallbackTitle || 'Imported calendar';
  const vevents = [];
  let current = null;
  let calTitle = calendarTitle;
  for (const line of lines) {
    if (line === 'BEGIN:VEVENT') { current = {}; continue; }
    if (line === 'END:VEVENT') { if (current) vevents.push(current); current = null; continue; }
    const idx = line.indexOf(':');
    if (idx < 0) continue;
    const left = line.slice(0, idx);
    const value = line.slice(idx + 1);
    const key = left.split(';', 1)[0].toUpperCase();
    if (key === 'X-WR-CALNAME') calTitle = unescapeIcsText(value) || calTitle;
    if (!current) continue;
    if (key === 'UID') current.uid = value;
    if (key === 'SUMMARY') current.summary = value;
    if (key === 'DTSTART') current.start = value;
    if (key === 'DTEND') current.end = value;
    if (key === 'DESCRIPTION') current.description = value;
    if (key === 'LOCATION') current.location = value;
    if (key === 'URL') current.url = value;
    if (key === 'RRULE') current.rrule = value;
    if (key === 'EXDATE') current.exdate = [...(current.exdate || []), ...value.split(',')];
    if (key === 'RECURRENCE-ID') current.recurrenceId = value;
  }
  const grouped = new Map();
  const standalone = [];
  for (const row of vevents) {
    const start = parseIcsDate(row.start);
    let end = parseIcsDate(row.end);
    if (!end && start) end = new Date(new Date(start).getTime() + 60 * 60 * 1000).toISOString();
    const normalized = {
      title: unescapeIcsText(row.summary || 'Imported event'),
      start,
      end,
      description: unescapeIcsText(row.description || ''),
      location: unescapeIcsText(row.location || ''),
      url: unescapeIcsText(row.url || ''),
      rrule: parseRRule(row.rrule),
      recurrenceId: parseIcsDate(row.recurrenceId),
      exdate: (row.exdate || []).map(parseIcsDate).filter(Boolean),
      uid: row.uid || `uid_${Math.random().toString(36).slice(2, 10)}`,
    };
    if (!normalized.start || !normalized.end) continue;
    if (!grouped.has(normalized.uid)) grouped.set(normalized.uid, { base: null, overrides: [] });
    const bucket = grouped.get(normalized.uid);
    if (normalized.recurrenceId) bucket.overrides.push(normalized);
    else if (normalized.rrule) bucket.base = normalized;
    else standalone.push(normalized);
  }
  const events = [];
  for (const item of standalone) {
    events.push({
      id: `evt_${Math.random().toString(36).slice(2, 10)}`,
      title: item.title,
      start: item.start,
      end: item.end,
      description: item.description,
      location: item.location,
      url: item.url,
      recurrence: null,
      exdates: [],
      overrides: [],
    });
  }
  for (const [uid, bucket] of grouped.entries()) {
    if (!bucket.base) continue;
    events.push({
      id: `evt_${Math.random().toString(36).slice(2, 10)}`,
      uid,
      title: bucket.base.title,
      start: bucket.base.start,
      end: bucket.base.end,
      description: bucket.base.description,
      location: bucket.base.location,
      url: bucket.base.url,
      recurrence: bucket.base.rrule,
      exdates: bucket.base.exdate,
      overrides: bucket.overrides.map((override) => ({
        recurrence_id: override.recurrenceId,
        title: override.title,
        start: override.start,
        end: override.end,
        description: override.description,
        location: override.location,
        url: override.url,
      })),
    });
  }
  return { title: calTitle, events };
}


function parseCsvLine(line) {
  const out = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') {
        cur += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (ch === ',' && !inQuotes) {
      out.push(cur);
      cur = '';
      continue;
    }
    cur += ch;
  }
  out.push(cur);
  return out.map((item) => item.trim());
}

function parseCsvFile(text, fallbackTitle) {
  const lines = String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n').filter(Boolean);
  if (!lines.length) return { title: fallbackTitle || 'Imported CSV', events: [] };
  const headers = parseCsvLine(lines[0]).map((item) => item.toLowerCase());
  const idx = (names) => names.map((name) => headers.indexOf(name)).find((value) => value >= 0) ?? -1;
  const titleIdx = idx(['title', 'summary', 'name', 'subject']);
  const startIdx = idx(['start', 'start_at', 'start date', 'start_date', 'begin']);
  const endIdx = idx(['end', 'end_at', 'end date', 'end_date', 'finish']);
  const descIdx = idx(['description', 'details', 'notes']);
  const locIdx = idx(['location', 'where']);
  const urlIdx = idx(['url', 'link']);
  const events = [];
  for (const line of lines.slice(1)) {
    const cols = parseCsvLine(line);
    const start = startIdx >= 0 ? new Date(cols[startIdx]).toISOString() : '';
    if (!start || start === 'Invalid Date') continue;
    let end = endIdx >= 0 ? new Date(cols[endIdx]).toISOString() : '';
    if (!end || end === 'Invalid Date') end = new Date(new Date(start).getTime() + 60 * 60 * 1000).toISOString();
    events.push({
      id: `evt_${Math.random().toString(36).slice(2, 10)}`,
      title: (titleIdx >= 0 && cols[titleIdx]) || 'Imported event',
      start,
      end,
      description: descIdx >= 0 ? (cols[descIdx] || '') : '',
      location: locIdx >= 0 ? (cols[locIdx] || '') : '',
      url: urlIdx >= 0 ? (cols[urlIdx] || '') : '',
    });
  }
  return { title: fallbackTitle || 'Imported CSV', events };
}

async function fetchCalendarEventsFromSource(source) {
  const target = source.fetch_url || source.url;
  if (!target) return [];
  const res = await fetch(target, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Could not load ${source.title || 'calendar source'}.`);
  const text = await res.text();
  const parsed = parseIcsFile(text, source.title || 'Calendar source');
  return (parsed.events || []).map((event) => ({
    ...event,
    source_title: source.title || parsed.title || 'Calendar source',
    source_author_name: source.author_name || source.owner_acct || '',
    contributor_text: source.contributor_text || '',
    source_site: source.site_url || window.location.origin || 'https://calendar.time-grid.org',
    timeline_color: source.color || '',
    editable: false,
  }));
}

async function makeHardCopyFromSubscription(item) {
  throw new Error('Hard copy is currently disabled.');
}

function readonlyEventDescription(item) {
  const existing = String(item?.description || '').trim();
  const timelineTitle = String(item?.source_title || item?.calendar_title || 'TimeGrid timeline').trim();
  const authorLabel = String(item?.author_name || item?.source_author_name || '').trim();
  const authorsLabel = String(item?.contributor_text || '').trim();
  const website = String(item?.source_site || window.location.origin || 'https://calendar.time-grid.org').trim();
  const parts = [];
  if (existing) parts.push(existing);
  parts.push('Shared via TimeGrid.');
  parts.push('TimeGrid is a calendar workspace for publishing timelines and subscribing to live calendar updates.');
  parts.push(`Website: ${website}`);
  if (timelineTitle) parts.push(`Timeline: ${timelineTitle}`);
  if (authorLabel) {
    parts.push(`Author: ${authorLabel}`);
  } else if (authorsLabel) {
    parts.push(`Authors: ${authorsLabel}`);
  }
  return parts.join('\n\n').trim();
}

function scriptSrcFor(path) {
  const script = Array.from(document.scripts || []).find((item) => {
    try {
      return new URL(item.src, window.location.origin).pathname === path;
    } catch (_error) {
      return false;
    }
  });
  return script?.src || path;
}

function ensureReadonlyRenderer() {
  if (window.TimeGridScheduleXCalendar?.mountReadonly) return Promise.resolve(true);
  if (state.readonlyRendererLoadPromise) return state.readonlyRendererLoadPromise;
  state.readonlyRendererLoadPromise = new Promise((resolve) => {
    const src = scriptSrcFor('/schedule-x-readonly.js');
    const existing = Array.from(document.scripts || []).find((item) => {
      try {
        return new URL(item.src, window.location.origin).pathname === '/schedule-x-readonly.js';
      } catch (_error) {
        return false;
      }
    });
    const finish = () => resolve(Boolean(window.TimeGridScheduleXCalendar?.mountReadonly));
    if (existing && !existing.dataset.timegridReloaded) {
      existing.addEventListener('load', finish, { once: true });
      existing.addEventListener('error', () => resolve(false), { once: true });
      window.setTimeout(finish, 250);
      return;
    }
    const script = document.createElement('script');
    script.src = src.includes('?') ? `${src}&retry=${Date.now()}` : `${src}?retry=${Date.now()}`;
    script.async = false;
    script.dataset.timegridReloaded = 'true';
    script.onload = finish;
    script.onerror = () => resolve(false);
    document.head.appendChild(script);
  });
  return state.readonlyRendererLoadPromise;
}

async function initReadonlyCalendar(elementId, sources, hintId = '') {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (state.readonlyCalendar) {
    state.readonlyCalendar.destroy?.();
    state.readonlyCalendar = null;
  }
  if (!sources || !sources.length) {
    el.innerHTML = '';
    setCalendarHint(hintId, '');
    return;
  }
  const token = ++state.readonlyLoadToken;
  el.innerHTML = '<div class="empty">Loading calendar…</div>';
  const chunks = await Promise.all(sources.map(async (source) => {
    try {
      return await fetchCalendarEventsFromSource(source);
    } catch (_error) {
      return [];
    }
  }));
  if (token !== state.readonlyLoadToken) return;
  const expanded = expandEventsList(chunks.flat());
  const viewScope = `${elementId}:${page}:${currentAcct() || currentPublishedSlug?.() || ''}`;
  const focus = calendarFocus(expanded, { initialView: savedCalendarView(viewScope) || 'dayGridMonth' });
  el.innerHTML = '';
  const rendererReady = await ensureReadonlyRenderer();
  if (token !== state.readonlyLoadToken) return;
  if (rendererReady && window.TimeGridScheduleXCalendar?.mountReadonly) {
    setCalendarHint(hintId, focus.hint);
    const mounted = window.TimeGridScheduleXCalendar.mountReadonly(el, {
      events: expanded.map((item) => ({
        ...item,
        description: readonlyEventDescription(item),
        timeline_color: item.timeline_color || item.source_color || '',
      })),
      initialView: focus.initialView,
      selectedDate: (focus.selectedDate || preferredCalendarDate()).slice(0, 10),
      weekOptions: { gridHeight: responsiveCalendarGridHeight() },
      onRangeUpdate(range) {
        saveCalendarView(viewScope, range?.legacyView || '');
        setCalendarHint(hintId, calendarGapHint(
          expanded,
          range?.startIso || '',
          range?.endIso || ''
        ));
      },
    });
    state.readonlyCalendar = {
      destroy() {
        window.TimeGridScheduleXCalendar?.destroy?.(el);
      },
      app: mounted,
    };
    return;
  }
  el.innerHTML = '<div class="empty">Calendar renderer unavailable.</div>';
}

async function parseImportFile(file) {
  const text = await file.text();
  const fallbackTitle = file.name.replace(/\.[^.]+$/, '') || 'Imported timeline';
  const lower = file.name.toLowerCase();
  if (lower.endsWith('.ics') || lower.endsWith('.ical') || file.type.includes('calendar')) return parseIcsFile(text, fallbackTitle);
  if (lower.endsWith('.csv') || file.type.includes('csv')) return parseCsvFile(text, fallbackTitle);
  throw new Error('Unsupported file type. Use .ics, .ical, or .csv.');
}

async function importTimelineFromFile(file, mode) {
  const parsed = await parseImportFile(file);
  if (!parsed.events.length) throw new Error('No valid events found in the imported file.');
  if (mode === 'personal' || mode === 'creator') {
    const data = await api(`/api/personal/${encodeURIComponent(currentAcct())}/timelines`, {
      method: 'POST',
      body: JSON.stringify({ title: parsed.title, description: `Imported from ${file.name}`, events: parsed.events, calendar_id: state.personal?.active_calendar_id || '', workspace: mode }),
    });
    if (mode === 'creator') {
      window.location.href = data.timeline?.edit_url || `/u/${encodeURIComponent(currentAcct())}/creator`;
      return data;
    }
    await loadWorkspace();
    setBanner(`Imported ${file.name} as a new editable timeline.`);
    return data;
  }
  state.timeline.title = parsed.title || state.timeline.title;
  state.timeline.description = state.timeline.description || `Imported from ${file.name}`;
  state.timeline.events = parsed.events;
  state.selectedEventId = parsed.events[0]?.id || null;
  state.draftEvent = null;
  renderTimeline();
  setBanner(`Imported ${file.name}. Review and save this timeline when ready.`);
  return null;
}

async function loadCurrentPageData() {
  if (page === 'published') {
    await Promise.all([loadPublished(), loadCommunity()]);
  } else if (page === 'published-detail') {
    await loadPublishedDetail();
  } else if (page === 'community') {
    await loadCommunity();
  } else if (page === 'community-profile') {
    await loadCommunityProfile();
  } else if (page === 'auth') {
    await loadAuthOptions();
  } else if (['personal', 'creator', 'archive', 'official'].includes(page)) {
    if (!state.me?.authenticated) {
      window.location.href = loginHref();
      return false;
    }
    await loadWorkspace();
  } else if (page === 'timeline') {
    if (!state.me?.authenticated) {
      window.location.href = loginHref();
      return false;
    }
    await loadTimeline();
  }
  return true;
}

function canSmoothNavigate(url) {
  if (url.origin !== window.location.origin) return false;
  const nextPage = pageFromPath(url.pathname);
  return ['personal', 'creator', 'archive', 'official', 'community', 'published'].includes(nextPage);
}

async function smoothNavigate(href, { replace = false } = {}) {
  const url = new URL(href, window.location.origin);
  if (!canSmoothNavigate(url) || state.routeSwitching) return false;
  state.routeSwitching = true;
  state.actionMenuOpen = false;
  const currentNavTop = document.querySelector('[data-section-nav]')?.getBoundingClientRect().top;
  const previousScrollHeight = Math.max(document.documentElement.scrollHeight, document.body.scrollHeight);
  const scrollFloor = Math.max(previousScrollHeight, window.scrollY + window.innerHeight + 24);
  const nextPage = pageFromPath(url.pathname);
  const nextUrl = `${url.pathname}${url.search}${url.hash}`;
  const setRouteScrollFloor = () => {
    root.style.minHeight = `${scrollFloor}px`;
  };
  const preserveNavPosition = () => {
    const nav = document.querySelector('[data-section-nav]');
    if (!nav || !Number.isFinite(currentNavTop)) return;
    const nextTop = nav.getBoundingClientRect().top;
    const delta = nextTop - currentNavTop;
    if (Math.abs(delta) > 1) window.scrollBy({ top: delta, left: 0, behavior: 'auto' });
  };
  const settleNavPosition = () => {
    preserveNavPosition();
    positionSectionIndicator();
  };
  const updateRoute = async () => {
    setRouteScrollFloor();
    page = nextPage;
    document.body.dataset.page = page;
    if (replace) history.replaceState({}, '', nextUrl);
    else history.pushState({}, '', nextUrl);
    await loadCurrentPageData();
    render();
    setRouteScrollFloor();
    preserveNavPosition();
  };
  try {
    if (document.startViewTransition) {
      await document.startViewTransition(updateRoute).finished;
    } else {
      root.classList.add('route-transitioning');
      await updateRoute();
      requestAnimationFrame(() => root.classList.remove('route-transitioning'));
    }
  } finally {
    state.routeSwitching = false;
    requestAnimationFrame(() => {
      settleNavPosition();
      requestAnimationFrame(() => {
        settleNavPosition();
        window.setTimeout(settleNavPosition, 90);
        window.setTimeout(settleNavPosition, 260);
      });
    });
  }
  return true;
}

document.addEventListener('click', (event) => {
  const link = event.target.closest('a.section-nav__link');
  if (!link || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const href = link.getAttribute('href');
  if (!href) return;
  const url = new URL(href, window.location.origin);
  if (!canSmoothNavigate(url)) return;
  event.preventDefault();
  smoothNavigate(href);
});

window.addEventListener('popstate', () => {
  smoothNavigate(window.location.href, { replace: true });
});

function renderLanding() {
  const recent = readRecentAccounts();
  root.innerHTML = `
    <div class="landing-page">
      <header class="site-header">
        <a class="site-logo" href="/" aria-label="TimeGrid home"><span>TimeGrid</span><small>Calendar</small></a>
        <nav class="site-nav" aria-label="Landing sections">
          <a href="#goals">Goals</a>
          <a href="#how-to-use">How to use</a>
          <a href="#faq">FAQ</a>
        </nav>
        <a class="button primary" href="${loginHref('/')}">Create account or sign in</a>
      </header>
      <main>
        <section class="landing-hero">
          <div class="landing-hero-copy">
            <h1>Private personal calendars, public share pages.</h1>
            <p>TimeGrid helps students, clubs, instructors, and small teams collect messy calendar feeds, clean them into timelines, and publish useful calendar pages without losing control of the original sources.</p>
            <div class="row landing-hero-actions">
              <a class="button primary" href="${loginHref('/')}">Create account or sign in</a>
              <a class="button" href="/published">Browse published calendars</a>
            </div>
            ${recent.length ? `<div class="banner">Recent accounts on this device: ${recent.map((item) => `@${escapeHtml(item.acct)}`).join(', ')}</div>` : ''}
          </div>
          <div class="landing-preview" aria-label="Timeline editor preview">
            <div class="preview-toolbar"><span></span><strong>Course timeline</strong><em>Live ICS</em></div>
            <div class="preview-grid">
              <div class="preview-side"><strong>Sources</strong><span>Personal</span><span>Creator</span><span>Published</span></div>
              <div class="preview-calendar">
                ${['Mon','Tue','Wed','Thu','Fri'].map((day) => `<small>${day}</small>`).join('')}
                <b style="grid-column:2 / span 2">APS360 Lecture</b>
                <i style="grid-column:3 / span 2">Office hours</i>
                <b style="grid-column:1 / span 2">Project review</b>
              </div>
            </div>
          </div>
        </section>

        <section class="landing-band" id="introduction">
          <div class="section-kicker">Introduction</div>
          <h2>One place to prepare calendars before people subscribe.</h2>
          <p>Most calendar tools are built for personal editing after events already exist. TimeGrid focuses on the earlier step: importing schedules, fixing series, splitting breaks, skipping exceptions, and publishing a clean feed that other people can follow.</p>
        </section>

        <section class="landing-split" id="goals">
          <div>
            <div class="section-kicker">Goals</div>
            <h2>Make shared time easier to trust.</h2>
            <p>TimeGrid is designed around clarity, ownership, and repairable schedules. A creator should be able to explain where a calendar came from, adjust it safely, and publish it without forcing everyone else to manually copy events.</p>
          </div>
          <div class="goal-list">
            <article><strong>Import without losing context</strong><span>Bring in ICS files and feeds, then keep source timelines understandable.</span></article>
            <article><strong>Edit series carefully</strong><span>Manage recurring blocks, breaks, skipped dates, descriptions, links, and locations in one editor.</span></article>
            <article><strong>Publish reusable pages</strong><span>Share public calendars people can preview, subscribe to, and attribute back to the creator.</span></article>
          </div>
        </section>

        <section class="landing-steps" id="how-to-use">
          <div class="section-kicker">How to use</div>
          <h2>From rough schedule to subscribe-ready feed.</h2>
          <div class="step-grid">
            <article><span>1</span><strong>Sign in</strong><p>Create a TimeGrid account and open your personal calendar workspace.</p></article>
            <article><span>2</span><strong>Import or create</strong><p>Add ICS files, feeds, or new events in the creator editor.</p></article>
            <article><span>3</span><strong>Clean the timeline</strong><p>Fix single events, manage series breaks, and skip dates that should not appear.</p></article>
            <article><span>4</span><strong>Publish and share</strong><p>Save the timeline, publish it, and share the page or subscription link.</p></article>
          </div>
        </section>

        <section class="landing-split project-description">
          <div>
            <div class="section-kicker">Project description</div>
            <h2>Built for academic and community schedules that change.</h2>
          </div>
          <p>Courses, labs, office hours, club events, and public programs often arrive as scattered files or unofficial links. TimeGrid treats calendars as maintained projects: creators can revise them, subscribers can follow the latest version, and published pages can explain what the calendar is for.</p>
        </section>

        <section class="landing-faq" id="faq">
          <div class="section-kicker">FAQ</div>
          <h2>Questions people usually ask first.</h2>
          <details open><summary>Can I keep a calendar private?</summary><p>Yes. Personal pages are private to the signed-in owner and authorized admins. Publishing is a separate action.</p></details>
          <details><summary>What happens when a source file has mistakes?</summary><p>Use the creator editor to correct single events, manage recurring series, add breaks, and skip individual occurrences before sharing the feed.</p></details>
          <details><summary>Can other people subscribe?</summary><p>Published timelines expose calendar links that people can add to their own calendar apps.</p></details>
          <details><summary>Why connect Mastodon?</summary><p>Mastodon provides identity and a path for public creator profiles, discussion, and attribution around published calendars.</p></details>
        </section>
      </main>
    </div>`;
}

function renderAuthHub() {
  const nextPath = authNextPath();
  const recent = readRecentAccounts();
  const providers = state.authOptions.length ? state.authOptions : [
    { id: 'mastodon', label: 'Mastodon', description: 'Sign in with your linked social.time-grid.org account.', status: 'active', login_href: mastodonLoginHref(nextPath) },
  ];
  const mastodonProvider = providers.find((item) => item.id === 'mastodon');
  const externalProviders = providers.filter((item) => !['mastodon', 'email', 'google', 'apple'].includes(item.id) && item.login_href);
  const providerSummary = 'Use your social.time-grid.org Mastodon account for TimeGrid access.';
  const isSignup = state.authMode !== 'login';
  root.innerHTML = `
    <div class="auth-shell">
      <section class="auth-centered-card">
        <div class="auth-mark">TimeGrid</div>
        <h1>${isSignup ? 'Create your TimeGrid account' : 'Sign in to TimeGrid'}</h1>
        <p class="auth-subcopy">Use one account for calendars, creator pages, publishing, invites, and dynamic exports.</p>
        ${recent.length ? `<div class="banner">Recent accounts on this device: ${recent.map((item) => `@${escapeHtml(item.acct)}`).join(', ')}</div>` : ''}
        ${state.authSignupError ? `<div class="banner error">${escapeHtml(state.authSignupError)}</div>` : ''}
        ${state.authSignupStatus ? `<div class="banner">${escapeHtml(state.authSignupStatus)}</div>` : ''}
        <div class="auth-mode-switch" role="tablist" aria-label="Auth mode">
          <button type="button" class="${isSignup ? 'active' : ''}" data-action="auth-mode" data-mode="signup">Sign up</button>
          <button type="button" class="${!isSignup ? 'active' : ''}" data-action="auth-mode" data-mode="login">Sign in</button>
        </div>
        <div class="auth-secondary-list">
          ${externalProviders.map((provider) => `<a class="button auth-provider-button ${provider.id === 'google' ? 'primary' : ''}" href="${escapeHtml(provider.login_href || '#')}">Continue with ${escapeHtml(provider.label)}</a>`).join('')}
          ${mastodonProvider ? `<a class="button auth-provider-button" href="${escapeHtml(mastodonProvider.login_href || mastodonLoginHref(nextPath))}">Continue with Mastodon</a>` : ''}
        </div>
        ${mastodonProvider ? `<div class="auth-help">Need a social identity? <a href="${escapeHtml(mastodonSignupHref())}" target="_blank" rel="noreferrer noopener">Create a Mastodon account</a>. If Mastodon is already signed in with the wrong account, use a private window or sign out on <code>social.time-grid.org</code>.</div>` : ''}
        <div class="muted" style="text-align:center">${escapeHtml(providerSummary)}</div>
        <div class="auth-link-row">
          <a href="/published">Browse published calendars</a>
          <span>·</span>
          <a href="/">Back to TimeGrid</a>
        </div>
      </section>
    </div>`;
  bindAuthActions();
}

function bindAuthActions() {
  document.querySelectorAll('[data-action="auth-mode"]').forEach((button) => button.addEventListener('click', () => {
    state.authMode = button.dataset.mode || 'signup';
    state.authSignupError = '';
    state.authSignupStatus = '';
    renderAuthHub();
  }));
}

function renderPublishedEmbed() {
  const item = state.publishedDetail;
  const subscribeUrl = document.body.dataset.subscribeUrl || item.subscribe_url || '';
  const signInSubscribeUrl = loginHref(`/subscribe/${encodeURIComponent(item.slug)}`);
  const subscribeControl = item.subscribed
    ? '<button class="embed-action" disabled>Added to my calendar</button>'
    : (state.me?.authenticated
      ? '<button class="embed-action" data-action="embed-subscribe">Add to my calendar</button>'
      : `<a class="embed-action" href="${escapeHtml(signInSubscribeUrl)}" target="_blank" rel="noreferrer noopener">Sign in to add</a>`);
  const contributorText = (item.contributors || []).map((entry) => `${entry.name}${entry.count > 1 ? ` (${entry.count})` : ''}`).join(', ');
  root.innerHTML = `
    <div class="embed-shell-app">
      <article class="embed-card-app">
        <div class="embed-copy-app">
          <div class="eyebrow">Published Calendar</div>
          <div class="embed-title-row">
            <h1>${escapeHtml(item.title)}</h1>
            ${subscribeControl}
          </div>
          <p>Published by @${escapeHtml(item.owner_acct)} with ${escapeHtml(item.subscription_count)} subscriptions</p>
          ${contributorText ? `<p>Authors: ${escapeHtml(contributorText)}</p>` : ''}
        </div>
        <div class="embed-calendar-wrap">
          <div id="published-embed-calendar-hint" class="calendar-tip hidden"></div><div id="published-embed-calendar" class="readonly-calendar-shell compact"></div>
        </div>
        <div class="embed-footer-app">
          <a class="embed-open" href="${escapeHtml(item.share_url)}" target="_blank" rel="noreferrer">Open interactive calendar</a>
        </div>
      </article>
    </div>`;
  document.querySelector('[data-action="embed-subscribe"]')?.addEventListener('click', async () => {
    try {
      await api(`/api/published/${encodeURIComponent(item.slug)}/subscribe`, { method: 'POST' });
      state.publishedDetail.subscribed = true;
      renderPublishedEmbed();
    } catch (error) {
      showToast(error.message || 'Subscribe failed');
    }
  });
  initReadonlyCalendar('published-embed-calendar', [{ title: item.title, fetch_url: item.feed_url, url: item.feed_url, contributor_text: contributorText, owner_acct: item.owner_acct, site_url: window.location.origin }], 'published-embed-calendar-hint');
}

function renderPublishedDetail() {
  const item = state.publishedDetail;
  const contributorText = (item.contributors || []).map((entry) => `${entry.name}${entry.count > 1 ? ` (${entry.count})` : ''}`).join(', ');
  root.innerHTML = `
    <div>
      <header class="published-hero">
        <div>
          <div class="eyebrow">Published Calendar</div>
          <h1>${escapeHtml(item.title)}</h1>
          <p>Published by <a href="/people/${encodeURIComponent(item.owner_acct)}">@${escapeHtml(item.owner_acct)}</a> with ${escapeHtml(item.subscription_count)} subscriptions</p>
          ${contributorText ? `<p>Authors: ${escapeHtml(contributorText)}</p>` : ''}
        </div>
        <div class="hero-actions">
          ${item.subscribed ? '<button class="button" disabled>Added to my calendar</button>' : `<a class="button" href="${escapeHtml(item.subscribe_url || '')}">Add to my calendar</a>`}
          <button class="button primary" data-action="share-bundle" data-url="${escapeHtml(item.share_url)}" data-title="${escapeHtml(item.title)}">Share</button>
          <a class="button" href="/published">Browse published</a>
          <a class="button" href="/people/${encodeURIComponent(item.owner_acct)}">Creator profile</a>
        </div>
      </header>
      <main class="published-layout">
        <section class="published-frame">
          <div id="published-calendar-hint" class="calendar-tip hidden"></div><div id="published-calendar" class="readonly-calendar-shell"></div>
        </section>
        <aside class="published-meta">
          <div class="meta-card"><span class="meta-label">Owner</span><strong>@${escapeHtml(item.owner_acct)}</strong></div>
          <div class="meta-card"><span class="meta-label">Subscriptions</span><strong>${escapeHtml(item.subscription_count)}</strong></div>
          ${contributorText ? `<div class="meta-card"><span class="meta-label">Authors</span><strong>${escapeHtml(contributorText)}</strong></div>` : ''}
          <div class="meta-card"><span class="meta-label">Subscribe</span><a href="${escapeHtml(item.feed_url)}" target="_blank" rel="noreferrer">Open merged ICS</a></div>
          <div class="meta-card"><span class="meta-label">Source</span><a href="${escapeHtml(item.embed_url)}" target="_blank" rel="noreferrer">Open calendar source</a></div>
        </aside>
      </main>
      ${eventDetailModal()}
    </div>`;
  document.querySelectorAll('[data-action="share-bundle"]').forEach((button) => button.addEventListener('click', () => openShareSheet(button.dataset.url, button.dataset.title || '')));
  bindEventDetailActions();
  initReadonlyCalendar('published-calendar', [{ title: item.title, fetch_url: item.feed_url, url: item.feed_url, contributor_text: contributorText, owner_acct: item.owner_acct, site_url: window.location.origin }], 'published-calendar-hint');
}

function renderPublished() {
  const labels = { public: 'Public', invited: 'Invited', private: 'Private' };
  const sortPublishedItems = (items) => {
    const list = [...items];
    if (state.publishedSort === 'oldest') {
      return list.sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')));
    }
    if (state.publishedSort === 'subscriptions') {
      return list.sort((a, b) => Number(b.subscription_count || 0) - Number(a.subscription_count || 0) || String(b.created_at || '').localeCompare(String(a.created_at || '')));
    }
    if (state.publishedSort === 'title') {
      return list.sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), undefined, { sensitivity: 'base' }));
    }
    return list.sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
  };
  const items = sortPublishedItems(state.published);
  root.innerHTML = `
    <div class="published-wrap">
      <header class="topbar">
        <div class="topbar-main">
          <div class="brand">
            <div class="eyebrow">TimeGrid Calendar</div>
            <h1>Published calendars</h1>
          </div>
          <div class="topbar-utility">
            ${state.me?.authenticated ? `<button data-action="logout">Sign out</button>${notificationsButton()}` : `<a class="button primary" href="${loginHref()}">Sign in with Mastodon</a>`}
          </div>
        </div>
        ${sectionNav()}
      </header>
      <section class="main-panel" style="margin-top:20px;">
        <div class="published-toolbar">
          <div class="category-tabs">
            ${['public','invited','private'].map((category) => `<button class="category-tab ${category === state.publishedCategory ? 'active' : ''}" data-action="published-tab" data-category="${category}">${labels[category]}</button>`).join('')}
          </div>
          <div class="published-toolbar-controls">
            <select class="published-sort-select" data-action="published-sort" aria-label="Sort published calendars">
              <option value="newest" ${state.publishedSort === 'newest' ? 'selected' : ''}>Newest</option>
              <option value="subscriptions" ${state.publishedSort === 'subscriptions' ? 'selected' : ''}>Most subscriptions</option>
              <option value="title" ${state.publishedSort === 'title' ? 'selected' : ''}>Title</option>
              <option value="oldest" ${state.publishedSort === 'oldest' ? 'selected' : ''}>Oldest</option>
            </select>
          </div>
        </div>
        <div class="published-search-row">
          <input type="search" class="published-search-input" placeholder="Search titles, owners, or hashtags" value="${escapeHtml(state.publishedQuery)}" data-action="published-search-input" />
          <button class="button" data-action="published-search-submit">Search</button>
        </div>
        <div class="public-list published-list-swap">
          ${items.length ? items.map(item => {
            const contributorText = (item.contributors || []).map((entry) => `${entry.name}${entry.count > 1 ? ` (${entry.count})` : ''}`).join(', ');
            const visibilityLabel = labels[item.visibility] || 'Public';
            const isOfficialOwner = String(item.owner_acct || '').toLowerCase() === 'official';
            return `
            <article class="public-card">
              <header>
                <div>
                  <div class="published-title-row">
                    <strong>${escapeHtml(item.title)}</strong>
                    ${isOfficialOwner ? '<span class="verified-badge" aria-label="Verified official">&#10003;</span>' : ''}
                    <span class="published-pill">${escapeHtml(visibilityLabel)}</span>
                    ${contributorText ? `<span class="published-pill">Authors: ${escapeHtml(contributorText)}</span>` : ''}
                  </div>
                  ${item.hashtag_text ? `<div class="published-hashtags">${escapeHtml(item.hashtag_text)}</div>` : ''}
                </div>
                <div class="sub-actions">
                  <a class="button primary" href="/p/${encodeURIComponent(item.slug)}">Open</a>
                  ${item.subscribed ? '<button class="button" disabled>Added to my calendar</button>' : `<a class="button" href="${escapeHtml(item.subscribe_url || '')}">Add to my calendar</a>`}
                  <button class="button" data-action="share-bundle" data-url="${escapeHtml(item.share_url)}" data-title="${escapeHtml(item.title)}">Share</button>
                </div>
              </header>
            </article>
          `; }).join('') : `<div class="empty">No ${escapeHtml(labels[state.publishedCategory].toLowerCase())} calendars available.</div>`}
        </div>
        <section style="margin-top:20px;">
          <div class="section-header">
            <h3>Creators</h3>
            <span class="muted">Browse published work from people on TimeGrid.</span>
          </div>
          <div class="public-list">
            ${state.community.length ? state.community.map((item) => `
              <article class="public-card">
                <header>
                  <div>
                    <strong><a href="/people/${encodeURIComponent(item.acct)}">${escapeHtml(item.display_name || item.acct)}</a></strong>
                    <div class="muted">@${escapeHtml(item.acct)}</div>
                    ${item.bio ? `<div class="muted">${escapeHtml(item.bio)}</div>` : ''}
                    <div class="muted">${escapeHtml(item.published_count)} published calendars</div>
                  </div>
                  <div class="sub-actions">
                    <a class="button primary" href="/people/${encodeURIComponent(item.acct)}">Open profile</a>
                  </div>
                </header>
              </article>
            `).join('') : '<div class="empty">No visible creator profiles match this search yet.</div>'}
          </div>
        </section>
      </section>
      ${notificationsModal()}
      ${eventDetailModal()}
    </div>`;
  document.querySelector('[data-action="logout"]')?.addEventListener('click', logout);
  bindNoticeActions(renderPublished);
  bindEventDetailActions();
  document.querySelectorAll('[data-action="share-bundle"]').forEach((button) => button.addEventListener('click', () => openShareSheet(button.dataset.url, button.dataset.title || '')));
  document.querySelectorAll('[data-action="published-tab"]').forEach((button) => button.addEventListener('click', async () => {
    state.publishedCategory = button.dataset.category || 'public';
    await loadPublished();
    renderPublished();
  }));
  const searchInput = document.querySelector('[data-action="published-search-input"]');
  const runSearch = async () => {
    state.publishedQuery = searchInput?.value || '';
    state.communityQuery = state.publishedQuery;
    await loadPublished();
    await loadCommunity();
    renderPublished();
  };
  searchInput?.addEventListener('keydown', async (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    await runSearch();
  });
  searchInput?.addEventListener('change', runSearch);
  document.querySelector('[data-action="published-search-submit"]')?.addEventListener('click', runSearch);
  document.querySelector('[data-action="published-sort"]')?.addEventListener('change', (event) => {
    state.publishedSort = event.target.value || 'newest';
    renderPublished();
  });
}

function bindEventDetailActions() {
  document.querySelectorAll('[data-action="close-event-detail"]').forEach((node) => node.addEventListener('click', closeEventDetail));
  document.querySelector('[data-action="event-detail-edit"]')?.addEventListener('click', () => {
    state.eventDetail = null;
    const eventId = String(state.selectedEventId || '');
    if (eventId) {
      document.getElementById('event-title')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      document.getElementById('event-title')?.focus();
    }
    render();
  });
}

function renderCommunity() {
  root.innerHTML = `
    <div class="published-wrap">
      <header class="topbar">
        <div class="topbar-main">
          <div class="brand">
            <div class="eyebrow">TimeGrid Community</div>
            <h1>People and their published calendars</h1>
          </div>
          <div class="topbar-utility">
            ${state.me?.authenticated ? `<button data-action="logout">Sign out</button>${notificationsButton()}` : `<a class="button primary" href="${loginHref()}">Sign in with Mastodon</a>`}
          </div>
        </div>
        ${sectionNav()}
      </header>
      <section class="main-panel" style="margin-top:20px;">
        <div class="published-search-row">
          <input type="search" class="published-search-input" placeholder="Search creators or published work" value="${escapeHtml(state.communityQuery)}" data-action="community-search-input" />
          <button class="button" data-action="community-search-submit">Search</button>
        </div>
        <div class="public-list">
          ${state.community.length ? state.community.map((item) => `
            <article class="public-card">
              <header>
                <div>
                  <strong><a href="/people/${encodeURIComponent(item.acct)}">${escapeHtml(item.display_name || item.acct)}</a></strong>
                  <div class="muted">@${escapeHtml(item.acct)}</div>
                  ${item.bio ? `<div class="muted">${escapeHtml(item.bio)}</div>` : ''}
                  <div class="muted">${escapeHtml(item.published_count)} published calendars</div>
                </div>
                <div class="sub-actions">
                  <a class="button primary" href="/people/${encodeURIComponent(item.acct)}">Open profile</a>
                </div>
              </header>
            </article>
          `).join('') : '<div class="empty">No creators match this search yet.</div>'}
        </div>
      </section>
      ${notificationsModal()}
    </div>`;
  document.querySelector('[data-action="logout"]')?.addEventListener('click', logout);
  bindNoticeActions(renderCommunity);
  const searchInput = document.querySelector('[data-action="community-search-input"]');
  const runSearch = async () => {
    state.communityQuery = searchInput?.value || '';
    await loadCommunity();
    renderCommunity();
  };
  searchInput?.addEventListener('keydown', async (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    await runSearch();
  });
  document.querySelector('[data-action="community-search-submit"]')?.addEventListener('click', runSearch);
}

function renderCommunityProfile() {
  const item = state.communityProfile;
  root.innerHTML = `
    <div class="published-wrap">
      <header class="topbar">
        <div class="topbar-main">
          <div class="brand">
            <div class="eyebrow">Creator profile</div>
            <h1>${escapeHtml(item.display_name || item.acct)}</h1>
            <p>@${escapeHtml(item.acct)}</p>
            ${item.bio ? `<p>${escapeHtml(item.bio)}</p>` : ''}
          </div>
          <div class="topbar-utility">
            ${state.me?.authenticated ? notificationsButton() : ''}
          </div>
        </div>
        ${sectionNav()}
      </header>
      <section class="main-panel" style="margin-top:20px;">
        <div class="section-header">
          <h3>Published work</h3>
          <span class="muted">${escapeHtml(item.published_count)} calendars</span>
        </div>
        <div class="public-list">
          ${item.published.length ? item.published.map((bundle) => publishCard(bundle, { manage: false })).join('') : '<div class="empty">This profile has no visible published calendars.</div>'}
        </div>
      </section>
      ${notificationsModal()}
    </div>`;
  document.querySelectorAll('[data-action="share-bundle"]').forEach((button) => button.addEventListener('click', () => openShareSheet(button.dataset.url, button.dataset.title || '')));
  bindNoticeActions(renderCommunityProfile);
}

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (state.mergeToolOpen) {
    state.mergeToolOpen = false;
    render();
    return;
  }
  if (state.publishOpen) {
    state.publishOpen = false;
    render();
    return;
  }
  if (state.exportOpen) {
    state.exportOpen = false;
    render();
    return;
  }
  if (state.publishedManageSlug) {
    state.publishedManageSlug = '';
    render();
    return;
  }
  if (state.signInChooserOpen) {
    state.signInChooserOpen = false;
    render();
    return;
  }
  if (state.colorMenuOpenId) {
    state.colorMenuOpenId = '';
    renderPersonal();
    return;
  }
  if (state.noticesOpen) {
    state.noticesOpen = false;
    render();
    return;
  }
});

function render() {
  let rendered;
  if (page === 'published') rendered = renderPublished();
  else if (page === 'published-detail') rendered = renderPublishedDetail();
  else if (page === 'auth') rendered = renderAuthHub();
  else if (page === 'personal' || page === 'creator' || page === 'archive' || page === 'official') rendered = renderPersonal();
  else if (page === 'timeline') rendered = renderTimeline();
  else if (page === 'community') rendered = renderCommunity();
  else if (page === 'community-profile') rendered = renderCommunityProfile();
  else rendered = renderLanding();
  requestAnimationFrame(() => {
    positionSectionIndicator();
    requestAnimationFrame(positionSectionIndicator);
  });
  window.setTimeout(positionSectionIndicator, 120);
  return rendered;
}

async function boot() {
  try {
    if (await handleSupabaseRedirect()) return;
    if (page === 'auth') renderAuthHub();
    await loadMe();
    await loadNotifications();
    await loadCurrentPageData();
    render();
  } catch (error) {
    state.noticeToast = { message: error.message || 'Could not load TimeGrid', error: true, exiting: false };
    scheduleNoticeToastDismiss();
    render();
  }
}

boot();
