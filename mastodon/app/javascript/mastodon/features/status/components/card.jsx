import punycode from 'punycode';

import PropTypes from 'prop-types';
import { PureComponent } from 'react';

import { FormattedMessage } from 'react-intl';

import classNames from 'classnames';


import { is } from 'immutable';
import ImmutablePropTypes from 'react-immutable-proptypes';

import DescriptionIcon from '@/material-icons/400-24px/description-fill.svg?react';
import OpenInNewIcon from '@/material-icons/400-24px/open_in_new.svg?react';
import PlayArrowIcon from '@/material-icons/400-24px/play_arrow-fill.svg?react';
import { Blurhash } from 'mastodon/components/blurhash';
import { Icon }  from 'mastodon/components/icon';
import { MoreFromAuthor } from 'mastodon/components/more_from_author';
import { RelativeTimestamp } from 'mastodon/components/relative_timestamp';
import { useBlurhash } from 'mastodon/initial_state';

const IDNA_PREFIX = 'xn--';

const decodeIDNA = domain => {
  return domain
    .split('.')
    .map(part => part.indexOf(IDNA_PREFIX) === 0 ? punycode.decode(part.slice(IDNA_PREFIX.length)) : part)
    .join('.');
};

const getHostname = url => {
  const parser = document.createElement('a');
  parser.href = url;
  return parser.hostname;
};

const domParser = new DOMParser();
const TRUSTED_CALENDAR_HOSTS = new Set(['calendar.time-grid.org']);

const handleIframeUrl = (html, url, providerName) => {
  const document = domParser.parseFromString(html, 'text/html').documentElement;
  const iframe = document.querySelector('iframe');
  const startTime = new URL(url).searchParams.get('t')

  if (iframe) {
    const iframeUrl = new URL(iframe.src)

    iframeUrl.searchParams.set('autoplay', 1)
    iframeUrl.searchParams.set('auto_play', 1)

    if (providerName === 'YouTube') {
      iframeUrl.searchParams.set('start', startTime || '');
      iframe.referrerPolicy = 'strict-origin-when-cross-origin';
    }

    iframe.src = iframeUrl.href

    // DOM parser creates html/body elements around original HTML fragment,
    // so we need to get innerHTML out of the body and not the entire document
    return document.querySelector('body').innerHTML;
  }

  return html;
};

const isTrustedCalendarUrl = (url) => {
  try {
    return TRUSTED_CALENDAR_HOSTS.has(new URL(url).hostname);
  } catch {
    return false;
  }
};

const getTrustedCalendarEmbedUrl = (url) => {
  try {
    const parsed = new URL(url);
    return `${parsed.origin}/embed${parsed.pathname}${parsed.search}`;
  } catch {
    return url;
  }
};

const getTrustedCalendarSubscribeUrl = (url) => {
  try {
    const parsed = new URL(url);
    const match = parsed.pathname.match(/^\/p\/([^/]+)$/);
    if (!match) return url;
    return `${parsed.origin}/subscribe/${match[1]}`;
  } catch {
    return url;
  }
};

export default class Card extends PureComponent {

  static propTypes = {
    card: ImmutablePropTypes.map,
    onOpenMedia: PropTypes.func.isRequired,
    sensitive: PropTypes.bool,
  };

  state = {
    previewLoaded: false,
    embedded: false,
    revealed: !this.props.sensitive,
  };

  UNSAFE_componentWillReceiveProps (nextProps) {
    if (!is(this.props.card, nextProps.card)) {
      this.setState({ embedded: false, previewLoaded: false });
    }

    if (this.props.sensitive !== nextProps.sensitive) {
      this.setState({ revealed: !nextProps.sensitive });
    }
  }

  componentDidMount () {
    window.addEventListener('resize', this.handleResize, { passive: true });
  }

  componentWillUnmount () {
    window.removeEventListener('resize', this.handleResize);
  }

  handleEmbedClick = () => {
    this.setState({ embedded: true });
  };

  handleExternalLinkClick = (e) => {
    e.stopPropagation();
  };

  setRef = c => {
    this.node = c;
  };

  handleImageLoad = () => {
    this.setState({ previewLoaded: true });
  };

  handleReveal = e => {
    e.preventDefault();
    e.stopPropagation();
    this.setState({ revealed: true });
  };

  renderTrustedCalendar () {
    const { card } = this.props;
    const embedUrl = getTrustedCalendarEmbedUrl(card.get('url'));
    const subscribeUrl = getTrustedCalendarSubscribeUrl(card.get('url'));

    return (
      <div className='status-card status-card--embedded-calendar expanded'>
        <div className='status-card__image status-card-video' style={{ aspectRatio: '16 / 9', minHeight: '360px' }}>
          <iframe
            id='open-web-calendar'
            title={card.get('title') || 'Open Web Calendar'}
            src={embedUrl}
            sandbox='allow-scripts allow-same-origin allow-top-navigation allow-downloads allow-popups allow-popups-to-escape-sandbox'
            scrolling='no'
            frameBorder='0'
            width='100%'
            height='100%'
            style={{ width: '100%', height: '100%', background: "url('https://raw.githubusercontent.com/niccokunzmann/open-web-calendar/master/static/img/loaders/circular-loader.gif') center center no-repeat" }}
          />
        </div>

        <div className='status-card__content' dir='auto'>
          <span className='status-card__host'>calendar.time-grid.org</span>
          <strong className='status-card__title'>{card.get('title') || 'Published calendar'}</strong>
          <span className='status-card__description'>{card.get('description') || 'Open the full calendar page for interactive navigation and future dates.'}</span>
          <span className='status-card__author'>
            <a href={subscribeUrl} target='_blank' rel='noopener'>Subscribe</a>
            {' · '}
            <a href={card.get('url')} target='_blank' rel='noopener'>Open interactive calendar</a>
          </span>
        </div>
      </div>
    );
  }

  renderVideo () {
    const { card } = this.props;
    const content = { __html: handleIframeUrl(card.get('html'), card.get('url'), card.get('provider_name')) };

    return (
      <div
        ref={this.setRef}
        className='status-card__image status-card-video'
        dangerouslySetInnerHTML={content}
        style={{ aspectRatio: '16 / 9' }}
      />
    );
  }

  render () {
    const { card } = this.props;
    const { embedded, revealed } = this.state;

    if (card === null) {
      return null;
    }

    if (isTrustedCalendarUrl(card.get('url'))) {
      return this.renderTrustedCalendar();
    }

    const provider    = card.get('provider_name').length === 0 ? decodeIDNA(getHostname(card.get('url'))) : card.get('provider_name');
    const interactive = card.get('type') === 'video';
    const language    = card.get('language') || '';
    const largeImage  = (card.get('image')?.length > 0 && card.get('width') > card.get('height')) || interactive;
    const showAuthor  = !!card.getIn(['authors', 0, 'accountId']);

    const description = (
      <div className='status-card__content' dir='auto'>
        <span className='status-card__host'>
          <span lang={language}>{provider}</span>
          {card.get('published_at') && <> · <RelativeTimestamp timestamp={card.get('published_at')} /></>}
        </span>

        <strong className='status-card__title' title={card.get('title')} lang={language}>{card.get('title')}</strong>

        {!showAuthor && (card.get('author_name').length > 0 ? <span className='status-card__author'><FormattedMessage id='link_preview.author' defaultMessage='By {name}' values={{ name: <strong>{card.get('author_name')}</strong> }} /></span> : <span className='status-card__description' lang={language}>{card.get('description')}</span>)}
      </div>
    );

    const thumbnailStyle = {
      visibility: revealed ? null : 'hidden',
    };

    if (largeImage && card.get('type') === 'video') {
      thumbnailStyle.aspectRatio = `16 / 9`;
    } else if (largeImage) {
      thumbnailStyle.aspectRatio = '1.91 / 1';
    } else {
      thumbnailStyle.aspectRatio = 1;
    }

    let embed;

    let canvas = (
      <Blurhash
        className={classNames('status-card__image-preview', {
          'status-card__image-preview--hidden': revealed && this.state.previewLoaded,
        })}
        hash={card.get('blurhash')}
        dummy={!useBlurhash}
      />
    );

    const thumbnailDescription = card.get('image_description');
    const thumbnail = <img src={card.get('image')} alt={thumbnailDescription} title={thumbnailDescription} lang={language} style={thumbnailStyle} onLoad={this.handleImageLoad} className='status-card__image-image' />;

    let spoilerButton = (
      <button type='button' onClick={this.handleReveal} className='spoiler-button__overlay'>
        <span className='spoiler-button__overlay__label'>
          <FormattedMessage id='status.sensitive_warning' defaultMessage='Sensitive content' />
          <span className='spoiler-button__overlay__action'><FormattedMessage id='status.media.show' defaultMessage='Click to show' /></span>
        </span>
      </button>
    );

    spoilerButton = (
      <div className={classNames('spoiler-button', { 'spoiler-button--minified': revealed })}>
        {spoilerButton}
      </div>
    );

    if (interactive) {
      if (embedded) {
        embed = this.renderVideo();
      } else {
        embed = (
          <div className='status-card__image'>
            {canvas}
            {thumbnail}

            {revealed ? (
              <div className='status-card__actions' onClick={this.handleEmbedClick} role='none'>
                <div>
                  <button type='button' onClick={this.handleEmbedClick}><Icon id='play' icon={PlayArrowIcon} /></button>
                  <a href={card.get('url')} onClick={this.handleExternalLinkClick} target='_blank' rel='noopener'><Icon id='external-link' icon={OpenInNewIcon} /></a>
                </div>
              </div>
            ) : spoilerButton}
          </div>
        );
      }

      return (
        <div className={classNames('status-card', { expanded: largeImage })} ref={this.setRef} onClick={revealed ? null : this.handleReveal} role={revealed ? 'button' : null}>
          {embed}
          <a href={card.get('url')} target='_blank' rel='noopener'>{description}</a>
        </div>
      );
    } else if (card.get('image')) {
      embed = (
        <div className='status-card__image'>
          {canvas}
          {thumbnail}
        </div>
      );
    } else {
      embed = (
        <div className='status-card__image'>
          <Icon id='file-text' icon={DescriptionIcon} />
        </div>
      );
    }

    return (
      <>
        <a href={card.get('url')} className={classNames('status-card', { expanded: largeImage, bottomless: showAuthor })} target='_blank' rel='noopener' ref={this.setRef}>
          {embed}
          {description}
        </a>

        {showAuthor && <MoreFromAuthor accountId={card.getIn(['authors', 0, 'accountId'])} />}
      </>
    );
  }

}
