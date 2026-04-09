import { defineMessages } from 'react-intl';

import { createAction } from '@reduxjs/toolkit';
import type { List as ImmutableList, Map as ImmutableMap } from 'immutable';

import { apiUpdateMedia } from 'mastodon/api/compose';
import { apiGetSearch } from 'mastodon/api/search';
import type { ApiMediaAttachmentJSON } from 'mastodon/api_types/media_attachments';
import type { MediaAttachment } from 'mastodon/models/media_attachment';
import {
  createDataLoadingThunk,
  createAppThunk,
} from 'mastodon/store/typed_functions';

import type { ApiQuotePolicy } from '../api_types/quotes';
import type { Status, StatusVisibility } from '../models/status';
import type { RootState } from '../store';

import { showAlert } from './alerts';
import { changeCompose, focusCompose } from './compose';
import { importFetchedStatuses } from './importer';
import { openModal } from './modal';

const messages = defineMessages({
  quoteErrorEdit: {
    id: 'quote_error.edit',
    defaultMessage: 'Quotes cannot be added when editing a post.',
  },
  quoteErrorUpload: {
    id: 'quote_error.upload',
    defaultMessage: 'Quoting is not allowed with media attachments.',
  },
  quoteErrorPoll: {
    id: 'quote_error.poll',
    defaultMessage: 'Quoting is not allowed with polls.',
  },
  quoteErrorQuote: {
    id: 'quote_error.quote',
    defaultMessage: 'Only one quote at a time is allowed.',
  },
  quoteErrorUnauthorized: {
    id: 'quote_error.unauthorized',
    defaultMessage: 'You are not authorized to quote this post.',
  },
  quoteErrorPrivateMention: {
    id: 'quote_error.private_mentions',
    defaultMessage: 'Quoting is not allowed with direct mentions.',
  },
});

const TIMEGRID_HOSTS = new Set(['calendar.time-grid.org']);
const timegridHashtagCache = new Map<string, string[]>();
const timegridHashtagRequestCache = new Map<string, Promise<string[]>>();

const timegridPublishedSlugFromUrl = (value: string) => {
  let parsedUrl: URL;

  try {
    parsedUrl = new URL(value);
  } catch {
    return null;
  }

  if (!TIMEGRID_HOSTS.has(parsedUrl.hostname)) {
    return null;
  }

  const parts = parsedUrl.pathname.split('/').filter(Boolean);

  if (parts[0] === 'p' && parts[1]) {
    return { origin: parsedUrl.origin, slug: parts[1] };
  }

  if (parts[0] === 'bundle' && parts[1]?.endsWith('.ics')) {
    return { origin: parsedUrl.origin, slug: parts[1].slice(0, -4) };
  }

  return null;
};

const extractTimeGridPublishedUrls = (text: string) => {
  const matches = text.match(/https?:\/\/[^\s]+/g) ?? [];
  const unique = new Set<string>();

  for (const candidate of matches) {
    const target = timegridPublishedSlugFromUrl(candidate);
    if (target) {
      unique.add(`${target.origin}/p/${target.slug}`);
    }
  }

  return Array.from(unique);
};

const escapeRegExp = (value: string) =>
  value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const normalizeTimeGridUrlBlock = (
  text: string,
  url: string,
  hashtags: string[],
) => {
  const normalizedTags = hashtags
    .map((tag) => tag.trim().replace(/^#/, '').toLowerCase())
    .filter((tag) => tag.length > 0);

  const uniqueTags = Array.from(new Set(normalizedTags));
  const tagPattern = uniqueTags
    .map((tag) => `#${escapeRegExp(tag)}`)
    .join('|');

  let nextText = text;

  if (tagPattern) {
    const standaloneTagMatcher = new RegExp(
      `(^|\\s)(?:${tagPattern})(?=\\s|$)`,
      'gi',
    );

    nextText = nextText.replace(standaloneTagMatcher, (match, leading) =>
      leading || '',
    );
    nextText = nextText.replace(/[ \t]+\n/g, '\n');
    nextText = nextText.replace(/\n{3,}/g, '\n\n');
  }

  const urlMatcher = new RegExp(escapeRegExp(url), 'i');
  const urlMatch = nextText.match(urlMatcher);

  if (!urlMatch || urlMatch.index === undefined) {
    return text;
  }

  const urlIndex = urlMatch.index;
  const beforeUrl = nextText.slice(0, urlIndex).replace(/[ \t]+\n?$/, '');
  const afterUrl = nextText
    .slice(urlIndex + urlMatch[0].length)
    .replace(/^\s*/, '')
    .replace(/\n{3,}/g, '\n\n');
  const hashtagLine = uniqueTags.map((tag) => `#${tag}`).join(' ');

  const parts = [beforeUrl, url];

  if (hashtagLine) {
    parts.push(hashtagLine);
  }

  if (afterUrl) {
    parts.push(afterUrl);
  }

  return parts.filter(Boolean).join('\n\n').trim();
};

const fetchTimeGridHashtags = async (origin: string, slug: string) => {
  const cacheKey = `${origin}/p/${slug}`;
  const cached = timegridHashtagCache.get(cacheKey);

  if (cached) {
    return cached;
  }

  const pending = timegridHashtagRequestCache.get(cacheKey);

  if (pending) {
    return pending;
  }

  const request = (async () => {
    const urls = [
      `${origin}/api/published/${encodeURIComponent(slug)}/share-meta`,
      `${origin}/api/published/${encodeURIComponent(slug)}`,
    ];

    for (const endpoint of urls) {
      const response = await fetch(endpoint, {
        credentials: 'include',
      });

      if (!response.ok) {
        continue;
      }

      const data = (await response.json()) as { hashtags?: string[] };
      const hashtags = Array.isArray(data.hashtags)
        ? data.hashtags.map((tag) => tag.replace(/^#/, ''))
        : [];

      timegridHashtagCache.set(cacheKey, hashtags);
      return hashtags;
    }

    return [];
  })();

  timegridHashtagRequestCache.set(cacheKey, request);

  try {
    return await request;
  } finally {
    timegridHashtagRequestCache.delete(cacheKey);
  }
};

const insertTextAtSelection = (
  text: string,
  insertedText: string,
  selectionStart?: number,
  selectionEnd?: number,
) => {
  if (
    typeof selectionStart !== 'number' ||
    typeof selectionEnd !== 'number'
  ) {
    return text.trim().length > 0
      ? `${text.replace(/\s*$/, '')}\n${insertedText}`
      : insertedText;
  }

  return `${text.slice(0, selectionStart)}${insertedText}${text.slice(selectionEnd)}`;
};

type SimulatedMediaAttachmentJSON = ApiMediaAttachmentJSON & {
  unattached?: boolean;
};

const simulateModifiedApiResponse = (
  media: MediaAttachment,
  params: { description?: string; focus?: string },
): SimulatedMediaAttachmentJSON => {
  const [x, y] = (params.focus ?? '').split(',');

  const data = {
    ...media.toJS(),
    ...params,
    meta: {
      focus: {
        x: parseFloat(x ?? '0'),
        y: parseFloat(y ?? '0'),
      },
    },
  } as unknown as SimulatedMediaAttachmentJSON;

  return data;
};

export const changeComposeVisibility = createAppThunk(
  'compose/visibility_change',
  (visibility: StatusVisibility, { dispatch, getState }) => {
    if (visibility !== 'direct') {
      return visibility;
    }

    const state = getState();
    const quotedStatusId = state.compose.get('quoted_status_id') as
      | string
      | null;
    if (!quotedStatusId) {
      return visibility;
    }

    // Remove the quoted status
    dispatch(quoteComposeCancel());
    const quotedStatus = state.statuses.get(quotedStatusId) as Status | null;
    if (!quotedStatus) {
      return visibility;
    }

    // Append the quoted status URL to the compose text
    const url = quotedStatus.get('url') as string;
    const text = state.compose.get('text') as string;
    if (!text.includes(url)) {
      const newText = text.trim() ? `${text}\n\n${url}` : url;
      dispatch(changeCompose(newText));
    }
    return visibility;
  },
);

export const changeUploadCompose = createDataLoadingThunk(
  'compose/changeUpload',
  async (
    {
      id,
      ...params
    }: {
      id: string;
      description: string;
      focus: string;
    },
    { getState },
  ) => {
    const media = (
      (getState().compose as ImmutableMap<string, unknown>).get(
        'media_attachments',
      ) as ImmutableList<MediaAttachment>
    ).find((item) => item.get('id') === id);

    // Editing already-attached media is deferred to editing the post itself.
    // For simplicity's sake, fake an API reply.
    if (media && !media.get('unattached')) {
      return new Promise<SimulatedMediaAttachmentJSON>((resolve) => {
        resolve(simulateModifiedApiResponse(media, params));
      });
    }

    return apiUpdateMedia(id, params);
  },
  (media: SimulatedMediaAttachmentJSON) => {
    return {
      media,
      attached: typeof media.unattached !== 'undefined' && !media.unattached,
    };
  },
  {
    useLoadingBar: false,
  },
);

export const quoteCompose = createAppThunk(
  'compose/quoteComposeStatus',
  (status: Status, { dispatch }) => {
    dispatch(focusCompose());
    return status;
  },
);

export const quoteComposeByStatus = createAppThunk(
  (status: Status, { dispatch, getState }) => {
    const state = getState();
    const composeState = state.compose;
    const mediaAttachments = composeState.get('media_attachments');
    // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
    const wasQuietPostHintModalDismissed: boolean =
      // eslint-disable-next-line @typescript-eslint/no-unsafe-call, @typescript-eslint/no-unsafe-member-access
      state.settings.getIn(
        ['dismissed_banners', 'quote/quiet_post_hint'],
        false,
      );

    if (composeState.get('id')) {
      dispatch(showAlert({ message: messages.quoteErrorEdit }));
    } else if (composeState.get('privacy') === 'direct') {
      dispatch(showAlert({ message: messages.quoteErrorPrivateMention }));
    } else if (composeState.get('poll')) {
      dispatch(showAlert({ message: messages.quoteErrorPoll }));
    } else if (
      composeState.get('is_uploading') ||
      (mediaAttachments &&
        typeof mediaAttachments !== 'string' &&
        typeof mediaAttachments !== 'number' &&
        typeof mediaAttachments !== 'boolean' &&
        mediaAttachments.size !== 0)
    ) {
      dispatch(showAlert({ message: messages.quoteErrorUpload }));
    } else if (composeState.get('quoted_status_id')) {
      dispatch(showAlert({ message: messages.quoteErrorQuote }));
    } else if (
      status.getIn(['quote_approval', 'current_user']) !== 'automatic' &&
      status.getIn(['quote_approval', 'current_user']) !== 'manual'
    ) {
      dispatch(showAlert({ message: messages.quoteErrorUnauthorized }));
    } else if (
      status.get('visibility') === 'unlisted' &&
      !wasQuietPostHintModalDismissed
    ) {
      dispatch(
        openModal({
          modalType: 'CONFIRM_QUIET_QUOTE',
          modalProps: { status },
        }),
      );
    } else {
      dispatch(quoteCompose(status));
    }
  },
);

export const quoteComposeById = createAppThunk(
  (statusId: string, { dispatch, getState }) => {
    const status = getState().statuses.get(statusId);
    if (status) {
      dispatch(quoteComposeByStatus(status));
    }
  },
);

const composeStateForbidsLink = (composeState: RootState['compose']) => {
  return (
    composeState.get('quoted_status_id') ||
    composeState.get('is_submitting') ||
    composeState.get('poll') ||
    composeState.get('is_uploading') ||
    composeState.get('id') ||
    composeState.get('privacy') === 'direct'
  );
};

export const pasteLinkCompose = createDataLoadingThunk(
  'compose/pasteLink',
  async ({ url }: { url: string }) => {
    return await apiGetSearch({
      q: url,
      type: 'statuses',
      resolve: true,
      limit: 2,
    });
  },
  (data, { dispatch, getState, requestId }) => {
    const composeState = getState().compose;

    if (
      composeStateForbidsLink(composeState) ||
      composeState.get('fetching_link') !== requestId // Request has been cancelled
    )
      return;

    dispatch(importFetchedStatuses(data.statuses));

    if (
      data.statuses.length === 1 &&
      data.statuses[0] &&
      ['automatic', 'manual'].includes(
        data.statuses[0].quote_approval?.current_user ?? 'denied',
      )
    ) {
      dispatch(quoteComposeById(data.statuses[0].id));
    }
  },
  {
    useLoadingBar: false,
    condition: (_, { getState }) =>
      !getState().compose.get('fetching_link') &&
      !composeStateForbidsLink(getState().compose),
  },
);

export const appendTimeGridHashtagsFromUrl = createAppThunk(
  async (
    { url, text }: { url: string; text?: string },
    { dispatch, getState },
  ) => {
    const target = timegridPublishedSlugFromUrl(url);

    if (!target) {
      return;
    }

    let hashtags: string[];

    try {
      hashtags = await fetchTimeGridHashtags(target.origin, target.slug);
    } catch {
      return;
    }

    if (hashtags.length === 0) {
      return;
    }

    const currentText = text ?? (getState().compose.get('text') as string);
    const canonicalUrl = `${target.origin}/p/${target.slug}`;
    const nextText = normalizeTimeGridUrlBlock(
      currentText,
      canonicalUrl,
      hashtags,
    );

    if (nextText !== currentText) {
      dispatch(changeCompose(nextText));
    }
  },
);

export const appendTimeGridHashtagsFromText = createAppThunk(
  async ({ text }: { text: string }, { dispatch }) => {
    for (const url of extractTimeGridPublishedUrls(text)) {
      await dispatch(appendTimeGridHashtagsFromUrl({ url }));
    }
  },
);

export const pasteTimeGridLinkCompose = createAppThunk(
  async (
    {
      text,
      url,
      selectionStart,
      selectionEnd,
    }: {
      text: string;
      url: string;
      selectionStart?: number;
      selectionEnd?: number;
    },
    { dispatch },
  ) => {
    const nextText = insertTextAtSelection(text, url, selectionStart, selectionEnd);
    dispatch(changeCompose(nextText));
    await dispatch(appendTimeGridHashtagsFromUrl({ url, text: nextText }));
  },
);

// Ideally this would cancel the action and the HTTP request, but this is good enough
export const cancelPasteLinkCompose = createAction(
  'compose/cancelPasteLinkCompose',
);

export const quoteComposeCancel = createAction('compose/quoteComposeCancel');

export const setComposeQuotePolicy = createAction<ApiQuotePolicy>(
  'compose/setQuotePolicy',
);
