(function attachTimeGridCalendarEditor(global) {
  function inputToIso(value) {
    return value ? new Date(value).toISOString() : '';
  }

  function randomEventId() {
    return `evt_${Math.random().toString(36).slice(2, 10)}`;
  }

  function emptyEvent({ start = '', end = '', timeline = null } = {}) {
    return {
      id: randomEventId(),
      title: '',
      start,
      end,
      description: '',
      location: '',
      url: '',
      recurrence: null,
      exdates: [],
      overrides: [],
      editable: true,
      source_timeline_id: timeline?.overlay_timeline_id || timeline?.id || '',
      source_subscription_id: timeline?.overlay_subscription_id || '',
      source_title: timeline?.title || 'New timeline',
      source_color: timeline?.overlay_color || timeline?.color || '',
    };
  }

  function readEventForm(documentRef) {
    return {
      title: documentRef.getElementById('event-title')?.value.trim() || 'Untitled event',
      start: inputToIso(documentRef.getElementById('event-start')?.value || ''),
      end: inputToIso(documentRef.getElementById('event-end')?.value || ''),
      location: documentRef.getElementById('event-location')?.value.trim() || '',
      url: documentRef.getElementById('event-url')?.value.trim() || '',
      description: documentRef.getElementById('event-description')?.value.trim() || '',
      repeat: documentRef.getElementById('event-repeat')?.value || 'none',
      repeatUntil: documentRef.getElementById('event-repeat-until')?.value || '',
    };
  }

  function buildSeriesPayload({
    form,
    selectedEventId = null,
    draftEvent = null,
    selectedSeriesEvent = null,
    timeline = null,
  }) {
    const current = selectedSeriesEvent || draftEvent || {};
    return {
      id: selectedEventId || draftEvent?.id || randomEventId(),
      title: form.title,
      start: form.start,
      end: form.end,
      location: form.location,
      url: form.url,
      description: form.description,
      recurrence: form.repeat === 'none'
        ? null
        : {
            freq: form.repeat,
            interval: 1,
            until: form.repeatUntil ? new Date(`${form.repeatUntil}T23:59:59`).toISOString() : '',
          },
      exdates: selectedSeriesEvent?.exdates || draftEvent?.exdates || [],
      overrides: selectedSeriesEvent?.overrides || draftEvent?.overrides || [],
      editable: true,
      source_timeline_id: current.source_timeline_id || timeline?.overlay_timeline_id || timeline?.id || '',
      source_subscription_id: current.source_subscription_id || timeline?.overlay_subscription_id || '',
      source_title: current.source_title || timeline?.title || 'My timeline',
      source_color: current.source_color || timeline?.overlay_color || timeline?.color || '',
    };
  }

  function applyOccurrenceEdit(base, occurrenceId, form) {
    const nextBase = base;
    nextBase.exdates = (nextBase.exdates || []).filter((value) => value !== occurrenceId);
    const override = {
      recurrence_id: occurrenceId,
      title: form.title,
      start: form.start,
      end: form.end,
      location: form.location,
      url: form.url,
      description: form.description,
    };
    const idx = (nextBase.overrides || []).findIndex((item) => item.recurrence_id === occurrenceId);
    if (idx >= 0) nextBase.overrides.splice(idx, 1, override);
    else (nextBase.overrides ||= []).push(override);
    return nextBase;
  }

  function deleteOccurrence(base, occurrenceId) {
    const nextBase = base;
    nextBase.exdates = Array.from(new Set([...(nextBase.exdates || []), occurrenceId]));
    nextBase.overrides = (nextBase.overrides || []).filter((item) => item.recurrence_id !== occurrenceId);
    return nextBase;
  }

  function upsertSeriesEvent(events, payload) {
    const list = [...(events || [])];
    const existing = list.findIndex((item) => item.id === payload.id);
    if (existing >= 0) list.splice(existing, 1, payload);
    else list.push(payload);
    return list;
  }

  function deleteSeries(events, eventId) {
    return (events || []).filter((entry) => entry.id !== eventId);
  }

  function standaloneEventFromOccurrence(base, occurrence, suffix = '') {
    return {
      ...base,
      id: randomEventId(),
      title: occurrence.title || base.title,
      start: occurrence.start,
      end: occurrence.end,
      location: occurrence.location || base.location || '',
      url: occurrence.url || base.url || '',
      description: occurrence.description || base.description || '',
      recurrence: null,
      exdates: [],
      overrides: [],
      editable: base.editable !== false,
      source_timeline_id: base.source_timeline_id || '',
      source_subscription_id: base.source_subscription_id || '',
      source_title: base.source_title || '',
      source_color: base.source_color || '',
      _conversion_suffix: suffix,
    };
  }

  function nearestOccurrenceId(occurrences, now = new Date()) {
    const upcoming = occurrences.find((item) => new Date(item.start) >= now);
    return (upcoming || occurrences[occurrences.length - 1] || occurrences[0] || null)?.start || '';
  }

  function convertRecurringSeries(base, options = {}) {
    const mode = options.mode === 'multiple' ? 'multiple' : 'single';
    const listSeriesOccurrences = global.TimeGridCalendarDomain?.listSeriesOccurrences;
    const occurrences = typeof listSeriesOccurrences === 'function'
      ? listSeriesOccurrences(base, { limit: 180, horizonDays: 1460 })
      : [];
    if (!occurrences.length) {
      return [standaloneEventFromOccurrence(base, base, 'fallback')];
    }

    const validIds = new Set(occurrences.map((item) => item.start));
    let selectedIds = Array.isArray(options.occurrenceIds)
      ? options.occurrenceIds.filter((value) => validIds.has(value))
      : [];
    if (!selectedIds.length) {
      const nearest = nearestOccurrenceId(occurrences, options.now instanceof Date ? options.now : new Date());
      if (nearest) selectedIds = [nearest];
    }
    if (mode === 'single' && selectedIds.length > 1) {
      selectedIds = [selectedIds[0]];
    }
    const selectedSet = new Set(selectedIds);
    return occurrences
      .filter((item) => selectedSet.has(item.start))
      .map((item, index) => standaloneEventFromOccurrence(base, item, String(index)));
  }

  function applyCalendarDateChange(base, occurrenceId, start, end) {
    if (!base) return { ok: false, reason: 'missing_base' };
    if (base.editable === false) return { ok: false, reason: 'readonly' };

    if (occurrenceId && base.recurrence?.freq) {
      base.exdates = (base.exdates || []).filter((value) => value !== occurrenceId);
      const override = {
        recurrence_id: occurrenceId,
        title: base.title,
        description: base.description || '',
        location: base.location || '',
        url: base.url || '',
        start,
        end,
      };
      const idx = (base.overrides || []).findIndex((item) => item.recurrence_id === occurrenceId);
      if (idx >= 0) base.overrides.splice(idx, 1, override);
      else (base.overrides ||= []).push(override);
      return {
        ok: true,
        selectedEventId: base.id,
        selectedOccurrence: { recurrenceId: occurrenceId },
      };
    }

    base.start = start;
    base.end = end;
    return {
      ok: true,
      selectedEventId: base.id,
      selectedOccurrence: null,
    };
  }

  global.TimeGridCalendarEditor = Object.freeze({
    emptyEvent,
    readEventForm,
    buildSeriesPayload,
    applyOccurrenceEdit,
    applyCalendarDateChange,
    deleteOccurrence,
    upsertSeriesEvent,
    deleteSeries,
    convertRecurringSeries,
  });
})(window);
