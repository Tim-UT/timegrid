(function attachTimeGridCalendarDomain(global) {
  function weekdayCode(date) {
    return ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA'][date.getUTCDay()];
  }

  function encodeOccurrenceId(occurrenceId) {
    return Array.from(String(occurrenceId || ''))
      .map((char) => char.charCodeAt(0).toString(16).padStart(2, '0'))
      .join('');
  }

  function decodeOccurrenceId(encoded) {
    const raw = String(encoded || '');
    let out = '';
    for (let index = 0; index < raw.length; index += 2) {
      const chunk = raw.slice(index, index + 2);
      if (chunk.length < 2) break;
      out += String.fromCharCode(parseInt(chunk, 16));
    }
    return out;
  }

  function occurrenceEventId(seriesId, occurrenceId) {
    return `${seriesId}__occ__${encodeOccurrenceId(occurrenceId)}`;
  }

  function parseOccurrenceEventId(eventId) {
    const raw = String(eventId || '');
    const marker = '__occ__';
    const index = raw.indexOf(marker);
    if (index < 0) return { seriesId: raw, occurrenceId: '' };
    return {
      seriesId: raw.slice(0, index),
      occurrenceId: decodeOccurrenceId(raw.slice(index + marker.length)),
    };
  }

  function normalizeEvent(item) {
    return {
      ...item,
      recurrence: item?.recurrence || null,
      exdates: Array.isArray(item?.exdates) ? item.exdates : [],
      overrides: Array.isArray(item?.overrides) ? item.overrides : [],
    };
  }

  function normalizeEvents(events) {
    return (events || []).map(normalizeEvent);
  }

  function eventDurationMs(event) {
    return new Date(event.end).getTime() - new Date(event.start).getTime();
  }

  function applyOverride(base, occurrenceId) {
    const override = (base.overrides || []).find((item) => item.recurrence_id === occurrenceId && !item.deleted);
    if (!override) return null;
    return {
      ...base,
      ...override,
      recurrence: null,
      recurrence_id: occurrenceId,
    };
  }

  function isExcluded(base, occurrenceId) {
    return (base.exdates || []).includes(occurrenceId) || (base.overrides || []).some((item) => item.recurrence_id === occurrenceId && item.deleted);
  }

  function expandRecurringEvent(baseInput, horizonDays = 220) {
    const base = normalizeEvent(baseInput);
    if (!base.recurrence?.freq) {
      return [{ ...base, _seriesId: base.id, _occurrenceId: base.start, _isOccurrence: false }];
    }

    const startDate = new Date(base.start);
    const duration = eventDurationMs(base);
    const until = base.recurrence.until ? new Date(base.recurrence.until) : new Date(Date.now() + horizonDays * 86400000);
    const freq = String(base.recurrence.freq || '').toUpperCase();
    const interval = Math.max(1, Number(base.recurrence.interval) || 1);
    const byweekday = (base.recurrence.byweekday && base.recurrence.byweekday.length ? base.recurrence.byweekday : [weekdayCode(startDate)]).map((item) => String(item).toUpperCase());
    const weekdayMap = { SU: 0, MO: 1, TU: 2, WE: 3, TH: 4, FR: 5, SA: 6 };
    const out = [];

    if (freq === 'WEEKLY') {
      const weekStart = new Date(startDate);
      weekStart.setUTCDate(weekStart.getUTCDate() - weekStart.getUTCDay());
      for (let week = 0; week < 200; week += interval) {
        for (const code of byweekday) {
          const day = weekdayMap[code];
          const occurrenceStart = new Date(weekStart);
          occurrenceStart.setUTCDate(weekStart.getUTCDate() + week * 7 + day);
          occurrenceStart.setUTCHours(startDate.getUTCHours(), startDate.getUTCMinutes(), startDate.getUTCSeconds(), 0);
          if (occurrenceStart < startDate) continue;
          if (occurrenceStart > until) return out;
          const occurrenceId = occurrenceStart.toISOString();
          if (isExcluded(base, occurrenceId)) continue;
          const override = applyOverride(base, occurrenceId);
          if (override) {
            out.push({ ...override, id: occurrenceEventId(base.id, occurrenceId), _seriesId: base.id, _occurrenceId: occurrenceId, _isOccurrence: true, _isOverride: true });
          } else {
            out.push({ ...base, id: occurrenceEventId(base.id, occurrenceId), start: occurrenceId, end: new Date(occurrenceStart.getTime() + duration).toISOString(), _seriesId: base.id, _occurrenceId: occurrenceId, _isOccurrence: true });
          }
        }
      }
      return out;
    }

    const stepMs = freq === 'DAILY' ? interval * 86400000 : interval * 7 * 86400000;
    for (let cursor = new Date(startDate); cursor <= until; cursor = new Date(cursor.getTime() + stepMs)) {
      const occurrenceId = cursor.toISOString();
      if (isExcluded(base, occurrenceId)) continue;
      const override = applyOverride(base, occurrenceId);
      if (override) {
        out.push({ ...override, id: occurrenceEventId(base.id, occurrenceId), _seriesId: base.id, _occurrenceId: occurrenceId, _isOccurrence: true, _isOverride: true });
      } else {
        out.push({ ...base, id: occurrenceEventId(base.id, occurrenceId), start: occurrenceId, end: new Date(cursor.getTime() + duration).toISOString(), _seriesId: base.id, _occurrenceId: occurrenceId, _isOccurrence: true });
      }
    }
    return out;
  }

  function expandEventsList(events) {
    return normalizeEvents(events).flatMap((item) => expandRecurringEvent(item));
  }

  function listSeriesOccurrences(baseInput, options = {}) {
    const limit = Math.max(1, Number(options.limit) || 24);
    const horizonDays = Math.max(30, Number(options.horizonDays) || 720);
    return expandRecurringEvent(baseInput, horizonDays)
      .sort((a, b) => new Date(a.start) - new Date(b.start))
      .slice(0, limit);
  }

  function validDate(value) {
    const date = value instanceof Date ? value : new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function startOfTodayIso() {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return today.toISOString();
  }

  function findCalendarFocus(events, options = {}) {
    const now = validDate(options.now) || new Date();
    const normalized = (events || [])
      .map((item) => ({ ...item, _startDate: validDate(item?.start) }))
      .filter((item) => item._startDate)
      .sort((a, b) => a._startDate - b._startDate);

    if (!normalized.length) {
      return {
        anchorEvent: null,
        selectedDate: startOfTodayIso(),
        legacyView: 'dayGridMonth',
        hint: 'No events yet.',
      };
    }

    const upcoming = normalized.find((item) => item._startDate >= now) || null;
    const previous = upcoming ? null : normalized[normalized.length - 1];
    const anchorEvent = upcoming || previous || normalized[0];
    const diffDays = Math.abs(anchorEvent._startDate.getTime() - now.getTime()) / 86400000;
    const legacyView = diffDays <= 21 ? 'timeGridWeek' : 'dayGridMonth';

    return {
      anchorEvent,
      selectedDate: anchorEvent.start,
      legacyView,
      hint: '',
    };
  }

  global.TimeGridCalendarDomain = Object.freeze({
    normalizeEvent,
    normalizeEvents,
    eventDurationMs,
    applyOverride,
    isExcluded,
    expandRecurringEvent,
    expandEventsList,
    listSeriesOccurrences,
    findCalendarFocus,
  });
})(window);
