(function attachTimeGridTimelineController(global) {
  function bindTimelineEditorActions(config) {
    const {
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
      sidebarOnly = false,
    } = config;
    let autoSaveTimer = null;
    let autoSaveInFlight = false;

    const queueAutoSave = ({ immediate = false, allowCreate = false } = {}) => {
      window.clearTimeout(autoSaveTimer);
      if (!state.timeline?.id && !allowCreate) return;
      const runSave = async () => {
        if (autoSaveInFlight) return;
        autoSaveInFlight = true;
        try {
          await saveTimeline({ silent: true });
        } catch (_error) {
          // saveTimeline already surfaces the error
        } finally {
          autoSaveInFlight = false;
        }
      };
      if (immediate) {
        runSave();
        return;
      }
      autoSaveTimer = window.setTimeout(runSave, 600);
    };

    const rerenderEditor = () => {
      if (typeof renderTimelineSidebarOnly === 'function') {
        renderTimelineSidebarOnly();
        return;
      }
      renderTimeline();
    };

    const rerenderEditorAndOverlays = () => {
      if (typeof renderTimelineEditorOnly === 'function') {
        renderTimelineEditorOnly();
        return;
      }
      renderTimeline();
    };

    const stableStringify = (value) => {
      if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
      if (value && typeof value === 'object') {
        return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(',')}}`;
      }
      return JSON.stringify(value);
    };

    const snapshotEvents = () => stableStringify(state.timeline?.events || []);


    const datetimeLocalValue = (date) => {
      const offset = date.getTimezoneOffset();
      return new Date(date.getTime() - offset * 60000).toISOString().slice(0, 16);
    };

    const validInputDate = (input) => {
      if (!input?.value) return null;
      const date = new Date(input.value);
      return Number.isNaN(date.getTime()) ? null : date;
    };

    const pairDuration = (startInput, endInput) => {
      const start = validInputDate(startInput);
      const end = validInputDate(endInput);
      if (!start || !end || end <= start) return null;
      return end.getTime() - start.getTime();
    };

    const rememberedDuration = (startInput, endInput) => {
      const direct = pairDuration(startInput, endInput);
      if (direct) return direct;
      const stored = Number(startInput?.dataset.durationMs || endInput?.dataset.durationMs || 0);
      return Number.isFinite(stored) && stored > 0 ? stored : 60 * 60 * 1000;
    };

    const rememberDuration = (startInput, endInput) => {
      const duration = pairDuration(startInput, endInput);
      if (!duration || !startInput || !endInput) return;
      startInput.dataset.durationMs = String(duration);
      endInput.dataset.durationMs = String(duration);
    };

    const attachDatePairRepair = (startInput, endInput, label = 'event') => {
      if (!startInput || !endInput || startInput.dataset.datePairRepair === 'bound') return;
      startInput.dataset.datePairRepair = 'bound';
      endInput.dataset.datePairRepair = 'bound';
      rememberDuration(startInput, endInput);
      const note = (target) => {
        setBanner(`${target} adjusted to keep the previous ${label} duration.`);
      };
      const repairFromStart = () => {
        const start = validInputDate(startInput);
        if (!start) return;
        const end = validInputDate(endInput);
        if (end && end > start) {
          rememberDuration(startInput, endInput);
          return;
        }
        const duration = rememberedDuration(startInput, endInput);
        endInput.value = datetimeLocalValue(new Date(start.getTime() + duration));
        rememberDuration(startInput, endInput);
        note('End time');
      };
      const repairFromEnd = () => {
        const end = validInputDate(endInput);
        if (!end) return;
        const start = validInputDate(startInput);
        if (start && end > start) {
          rememberDuration(startInput, endInput);
          return;
        }
        const duration = rememberedDuration(startInput, endInput);
        startInput.value = datetimeLocalValue(new Date(end.getTime() - duration));
        rememberDuration(startInput, endInput);
        note('Start time');
      };
      ['focus', 'pointerdown', 'keydown'].forEach((eventName) => {
        startInput.addEventListener(eventName, () => rememberDuration(startInput, endInput));
        endInput.addEventListener(eventName, () => rememberDuration(startInput, endInput));
      });
      startInput.addEventListener('change', repairFromStart);
      endInput.addEventListener('change', repairFromEnd);
    };

    if (!sidebarOnly) {
      document.querySelector('[data-action="logout"]')?.addEventListener('click', logout);
      document.querySelector('[data-action="save-timeline"]')?.addEventListener('click', () => saveTimeline());
      document.querySelector('[data-action="return-workspace"]')?.addEventListener('click', async (event) => {
        await saveTimeline({ silent: true });
        window.location.href = event.currentTarget.dataset.href || `/u/${encodeURIComponent(state.timeline?.owner_acct || document.body.dataset.acct || location.pathname.split('/')[2] || '')}`;
      });
      document.querySelectorAll('[data-section-key]').forEach((link) => link.addEventListener('click', async (event) => {
        const key = link.dataset.sectionKey;
        if (!['personal', 'creator'].includes(key)) return;
        event.preventDefault();
        await saveTimeline({ silent: true });
        window.location.href = link.href;
      }));
      document.querySelector('[data-action="import-editor"]')?.addEventListener('click', () => document.getElementById('timeline-import-input')?.click());
      document.getElementById('timeline-import-input')?.addEventListener('change', async (event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (!file) return;
        try {
          await importTimelineFromFile(file, 'editor');
        } catch (error) {
          setBanner('', error.message);
        }
      });
    }

    document.getElementById('timeline-title')?.addEventListener('input', (event) => {
      state.timeline.title = event.target.value;
      queueAutoSave();
    });
    document.getElementById('timeline-description')?.addEventListener('input', (event) => {
      state.timeline.description = event.target.value;
      queueAutoSave();
    });
    document.getElementById('timeline-color')?.addEventListener('input', (event) => {
      state.timeline.color = event.target.value;
      queueAutoSave();
    });

    const repeatSelect = document.getElementById('event-repeat');
    const repeatUntilWrap = document.getElementById('event-repeat-until-wrap');
    const startInput = document.getElementById('event-start');
    const endInput = document.getElementById('event-end');

    const syncRepeatUntil = () => {
      if (!repeatSelect || !repeatUntilWrap) return;
      repeatUntilWrap.classList.toggle('field-hidden', repeatSelect.value === 'none');
    };

    const syncRecurrenceConversionState = () => {
      const selected = selectedSeriesEvent();
      if (!selected?.recurrence?.freq || state.selectedOccurrence) {
        state.recurrenceConversion = null;
        return;
      }
      if (repeatSelect?.value !== 'none') {
        state.recurrenceConversion = null;
        return;
      }
      const choices = global.TimeGridCalendarDomain?.listSeriesOccurrences?.(selected, { limit: 18, horizonDays: 1460 }) || [];
      const valid = new Set(choices.map((item) => item.start));
      let mode = state.recurrenceConversion?.mode === 'multiple' ? 'multiple' : 'single';
      let occurrenceIds = Array.isArray(state.recurrenceConversion?.occurrenceIds)
        ? state.recurrenceConversion.occurrenceIds.filter((value) => valid.has(value))
        : [];
      if (!occurrenceIds.length && choices.length) {
        const upcoming = choices.find((item) => new Date(item.start) >= new Date());
        occurrenceIds = [(upcoming || choices[choices.length - 1] || choices[0]).start];
      }
      if (mode === 'single' && occurrenceIds.length > 1) occurrenceIds = [occurrenceIds[0]];
      state.recurrenceConversion = { mode, occurrenceIds };
    };

    const syncEndAfterStart = () => {
      attachDatePairRepair(startInput, endInput, 'event');
      const startDate = validInputDate(startInput);
      if (!startDate) return;
      const endDate = validInputDate(endInput);
      if (!endDate || endDate <= startDate) {
        endInput.value = datetimeLocalValue(new Date(startDate.getTime() + rememberedDuration(startInput, endInput)));
        rememberDuration(startInput, endInput);
      } else {
        rememberDuration(startInput, endInput);
      }
      syncDraftEventFromForm();
    };

    const ensureDraftEvent = () => {
      if (state.draftEvent) return state.draftEvent;
      state.selectedEventId = null;
      state.selectedOccurrence = null;
      state.recurrenceConversion = null;
      state.draftEvent = emptyEvent();
      return state.draftEvent;
    };

    const syncDraftEventFromForm = () => {
      const draft = ensureDraftEvent();
      draft.title = valueOf('event-title');
      const start = inputIso('event-start');
      const end = inputIso('event-end');
      if (start) draft.start = start;
      if (end) draft.end = end;
      const repeat = document.getElementById('event-repeat')?.value || 'none';
      const repeatUntil = document.getElementById('event-repeat-until')?.value || '';
      draft.recurrence = repeat === 'none'
        ? null
        : {
            freq: repeat,
            interval: 1,
            until: repeatUntil ? new Date(`${repeatUntil}T23:59:59`).toISOString() : '',
          };
      draft.location = valueOf('event-location');
      draft.url = valueOf('event-url');
      draft.description = valueOf('event-description');
    };

    repeatSelect?.addEventListener('change', () => {
      syncDraftEventFromForm();
      const selected = selectedSeriesEvent();
      const shouldRerender = !!(selected?.recurrence?.freq && !state.selectedOccurrence && repeatSelect.value === 'none');
      syncRepeatUntil();
      syncRecurrenceConversionState();
      if (shouldRerender) {
        rerenderEditor();
      }
    });
    startInput?.addEventListener('input', syncEndAfterStart);
    endInput?.addEventListener('input', syncDraftEventFromForm);
    startInput?.addEventListener('change', syncEndAfterStart, true);
    endInput?.addEventListener('change', syncDraftEventFromForm, true);
    attachDatePairRepair(startInput, endInput, 'event');
    startInput?.addEventListener('change', syncEndAfterStart);
    endInput?.addEventListener('change', syncDraftEventFromForm);
    ['event-title', 'event-location', 'event-url', 'event-description', 'event-repeat-until'].forEach((id) => {
      document.getElementById(id)?.addEventListener('input', syncDraftEventFromForm);
      document.getElementById(id)?.addEventListener('change', syncDraftEventFromForm);
    });
    syncRepeatUntil();

    function valueOf(id) {
      return document.getElementById(id)?.value.trim() || '';
    }

    function inputIso(id) {
      const value = document.getElementById(id)?.value || '';
      return value ? new Date(value).toISOString() : '';
    }

    function rowValue(row, field) {
      return row.querySelector(`[data-field="${field}"]`)?.value.trim() || '';
    }

    function rowIso(row, field) {
      const value = row.querySelector(`[data-field="${field}"]`)?.value || '';
      return value ? new Date(value).toISOString() : '';
    }

    function deleteSeriesGroup(groupId) {
      if (!groupId) return;
      const group = (state.timeline.events || []).filter((entry) => (entry.series_group_id || entry.id) === groupId);
      if (group.some((entry) => entry.editable === false)) return setBanner('', 'This source is read-only.');
      state.timeline.events = (state.timeline.events || []).filter((entry) => (entry.series_group_id || entry.id) !== groupId);
      state.eventEditModal = null;
      state.selectedEventId = null;
      state.selectedOccurrence = null;
      state.draftEvent = null;
      state.recurrenceConversion = null;
      renderTimeline();
      queueAutoSave({ immediate: true, allowCreate: true });
    }

    document.querySelector('[data-action="new-event"]')?.addEventListener('click', () => {
      state.selectedEventId = null;
      state.selectedOccurrence = null;
      state.draftEvent = emptyEvent();
      state.recurrenceConversion = null;
      rerenderEditor();
    });

    document.querySelector('[data-action="switch-series"]')?.addEventListener('click', () => {
      state.selectedOccurrence = null;
      state.recurrenceConversion = null;
      rerenderEditor();
    });

    document.querySelectorAll('[data-action="pick-event"]').forEach((button) => button.addEventListener('click', () => {
      state.selectedEventId = button.dataset.id;
      state.selectedOccurrence = null;
      state.draftEvent = null;
      state.recurrenceConversion = null;
      rerenderEditor();
    }));

    document.querySelectorAll('[data-action="delete-event"]').forEach((button) => button.addEventListener('click', () => {
      const item = state.timeline.events.find((entry) => entry.id === button.dataset.id);
      if (item?.editable === false) return setBanner('', 'This source is read-only.');
      state.timeline.events = global.TimeGridCalendarEditor?.deleteSeries?.(state.timeline.events, button.dataset.id) || state.timeline.events.filter((entry) => entry.id !== button.dataset.id);
      if (state.selectedEventId === button.dataset.id) state.selectedEventId = null;
      state.selectedOccurrence = null;
      state.draftEvent = null;
      state.recurrenceConversion = null;
      rerenderEditor();
      queueAutoSave({ immediate: true });
    }));

    document.querySelector('[data-action="delete-selected-series"]')?.addEventListener('click', () => {
      if (!state.selectedEventId) return;
      const item = selectedSeriesEvent();
      if (item?.editable === false) return setBanner('', 'This source is read-only.');
      state.timeline.events = global.TimeGridCalendarEditor?.deleteSeries?.(state.timeline.events, state.selectedEventId) || state.timeline.events.filter((entry) => entry.id !== state.selectedEventId);
      state.selectedEventId = null;
      state.selectedOccurrence = null;
      state.draftEvent = null;
      state.recurrenceConversion = null;
      rerenderEditor();
      queueAutoSave({ immediate: true });
    });

    document.querySelector('[data-action="delete-occurrence"]')?.addEventListener('click', () => {
      const base = selectedSeriesEvent();
      if (!base || !state.selectedOccurrence) return;
      if (base.editable === false) return setBanner('', 'This source is read-only.');
      if (global.TimeGridCalendarEditor?.deleteOccurrence) {
        global.TimeGridCalendarEditor.deleteOccurrence(base, state.selectedOccurrence.recurrenceId);
      }
      state.selectedOccurrence = null;
      state.recurrenceConversion = null;
      rerenderEditor();
      queueAutoSave({ immediate: true });
    });

    document.querySelector('[data-action="clear-event"]')?.addEventListener('click', () => {
      state.selectedEventId = null;
      state.selectedOccurrence = null;
      state.draftEvent = null;
      state.recurrenceConversion = null;
      rerenderEditor();
    });

    document.querySelectorAll('[data-action="open-single-modal"]').forEach((button) => button.addEventListener('click', () => {
      state.eventEditModal = { kind: 'single', id: button.dataset.id };
      rerenderEditorAndOverlays();
    }));

    document.querySelectorAll('[data-action="open-series-modal"]').forEach((button) => button.addEventListener('click', () => {
      state.eventEditModal = { kind: 'series', id: button.dataset.id || state.eventEditModal?.id };
      rerenderEditorAndOverlays();
    }));

    document.querySelectorAll('[data-action="open-skipper-modal"]').forEach((button) => button.addEventListener('click', () => {
      state.eventEditModal = { kind: 'skipper', id: button.dataset.id || state.eventEditModal?.id };
      rerenderEditorAndOverlays();
    }));

    document.querySelectorAll('[data-action="close-event-editor"]').forEach((button) => button.addEventListener('click', () => {
      state.eventEditModal = null;
      state.selectedEventId = null;
      state.selectedOccurrence = null;
      rerenderEditorAndOverlays();
    }));


    attachDatePairRepair(document.getElementById('modal-event-start'), document.getElementById('modal-event-end'), 'single event');
    document.querySelectorAll('[data-series-row-id]').forEach((row) => {
      attachDatePairRepair(row.querySelector('[data-field="start"]'), row.querySelector('[data-field="end"]'), 'series row');
    });

    document.querySelectorAll('[data-action="remove-series-segment"]').forEach((button) => button.addEventListener('click', () => {
      const item = state.timeline.events.find((entry) => entry.id === button.dataset.id);
      if (item?.editable === false) return setBanner('', 'This source is read-only.');
      state.timeline.events = (state.timeline.events || []).filter((entry) => entry.id !== button.dataset.id);
      renderTimeline();
      queueAutoSave({ immediate: true, allowCreate: true });
    }));

    document.querySelector('[data-action="add-series-break"]')?.addEventListener('click', () => {
      const groupId = document.querySelector('[data-series-group-id]')?.dataset.seriesGroupId || state.eventEditModal?.id || '';
      const groupItems = (state.timeline.events || []).filter((entry) => (entry.series_group_id || entry.id) === groupId && entry.recurrence?.freq);
      const base = groupItems.find((entry) => entry.editable !== false) || groupItems[0];
      if (!base || base.editable === false) return setBanner('', 'This source is read-only.');
      const sharedTitle = document.getElementById('series-shared-title')?.value.trim() || base.title || 'Untitled series';
      const duration = new Date(base.end).getTime() - new Date(base.start).getTime();
      const start = new Date(new Date(base.start).getTime() + 7 * 86400000);
      const resolvedGroupId = groupId || base.id;
      const clone = {
        ...base,
        id: `evt_${Math.random().toString(36).slice(2, 10)}`,
        series_group_id: resolvedGroupId,
        title: sharedTitle,
        start: start.toISOString(),
        end: new Date(start.getTime() + (Number.isFinite(duration) ? duration : 60 * 60 * 1000)).toISOString(),
        exdates: [],
        overrides: [],
      };
      state.timeline.events = [...(state.timeline.events || []).map((entry) => entry.id === base.id ? { ...entry, series_group_id: resolvedGroupId } : entry), clone];
      state.timeline.events = state.timeline.events.map((entry) => (entry.series_group_id || entry.id) === resolvedGroupId ? { ...entry, title: sharedTitle, series_group_id: resolvedGroupId } : entry);
      state.eventEditModal = { kind: 'series', id: resolvedGroupId };
      renderTimeline();
      queueAutoSave({ immediate: true, allowCreate: true });
    });

    document.querySelector('[data-action="save-single-event-modal"]')?.addEventListener('click', () => {
      const id = document.querySelector('[data-single-event-id]')?.dataset.singleEventId || state.eventEditModal?.id;
      const item = state.timeline.events.find((entry) => entry.id === id);
      if (!item) return;
      if (item.editable === false) return setBanner('', 'This source is read-only.');
      const start = inputIso('modal-event-start');
      const end = inputIso('modal-event-end');
      if (!start || !end) return setBanner('', 'Start and end are required.');
      const nextValues = {
        title: valueOf('modal-event-title') || 'Untitled event',
        start,
        end,
        location: valueOf('modal-event-location'),
        url: valueOf('modal-event-url'),
        description: valueOf('modal-event-description'),
        recurrence: null,
        series_group_id: '',
      };
      const before = stableStringify({
        title: item.title || '',
        start: item.start || '',
        end: item.end || '',
        location: item.location || '',
        url: item.url || '',
        description: item.description || '',
        recurrence: item.recurrence || null,
        series_group_id: item.series_group_id || '',
      });
      const after = stableStringify(nextValues);
      state.eventEditModal = null;
      state.selectedEventId = null;
      state.selectedOccurrence = null;
      if (before === after) {
        rerenderEditorAndOverlays();
        return;
      }
      Object.assign(item, nextValues);
      renderTimeline();
      queueAutoSave({ immediate: true, allowCreate: true });
    });

    document.querySelector('[data-action="delete-single-event-modal"]')?.addEventListener('click', () => {
      const id = document.querySelector('[data-single-event-id]')?.dataset.singleEventId || state.eventEditModal?.id;
      const item = state.timeline.events.find((entry) => entry.id === id);
      if (item?.editable === false) return setBanner('', 'This source is read-only.');
      state.timeline.events = (state.timeline.events || []).filter((entry) => entry.id !== id);
      state.eventEditModal = null;
      state.selectedEventId = null;
      state.selectedOccurrence = null;
      renderTimeline();
      queueAutoSave({ immediate: true, allowCreate: true });
    });

    document.querySelector('[data-action="save-series-modal"]')?.addEventListener('click', () => {
      const groupId = document.querySelector('[data-series-group-id]')?.dataset.seriesGroupId || state.eventEditModal?.id || '';
      const rows = Array.from(document.querySelectorAll('[data-series-row-id]'));
      if (!rows.length) return;
      const before = snapshotEvents();
      const nextEvents = [...(state.timeline.events || [])];
      const sharedTitle = document.getElementById('series-shared-title')?.value.trim() || 'Untitled series';
      for (const row of rows) {
        const id = row.dataset.seriesRowId;
        const item = nextEvents.find((entry) => entry.id === id);
        if (!item || item.editable === false) continue;
        const start = rowIso(row, 'start');
        const end = rowIso(row, 'end');
        if (!start || !end) return setBanner('', 'Every series row needs start and end.');
        Object.assign(item, {
          series_group_id: groupId || item.series_group_id || item.id,
          title: sharedTitle,
          start,
          end,
          location: rowValue(row, 'location'),
          url: rowValue(row, 'url'),
          description: rowValue(row, 'description'),
          recurrence: {
            freq: rowValue(row, 'repeat') || 'WEEKLY',
            interval: 1,
            until: rowValue(row, 'until') ? new Date(`${rowValue(row, 'until')}T23:59:59`).toISOString() : '',
          },
        });
      }
      state.timeline.events = nextEvents;
      state.eventEditModal = null;
      state.selectedEventId = null;
      state.selectedOccurrence = null;
      if (before === snapshotEvents()) {
        rerenderEditorAndOverlays();
        return;
      }
      renderTimeline();
      queueAutoSave({ immediate: true, allowCreate: true });
    });

    document.querySelectorAll('[data-action="delete-series-group"]').forEach((button) => button.addEventListener('click', (event) => deleteSeriesGroup(event.currentTarget.dataset.id)));
    document.querySelector('[data-action="delete-series-group-modal"]')?.addEventListener('click', () => deleteSeriesGroup(state.eventEditModal?.id));

    document.querySelector('[data-action="save-skipper-modal"]')?.addEventListener('click', () => {
      const before = snapshotEvents();
      const rows = Array.from(document.querySelectorAll('.skipper-row input[type="checkbox"]'));
      rows.forEach((box) => {
        const item = state.timeline.events.find((entry) => entry.id === box.dataset.seriesId);
        if (!item || item.editable === false) return;
        const id = box.dataset.occurrenceId;
        const exdates = new Set(item.exdates || []);
        if (box.checked) exdates.delete(id);
        else exdates.add(id);
        item.exdates = Array.from(exdates);
      });
      if (before === snapshotEvents()) {
        state.eventEditModal = null;
        rerenderEditorAndOverlays();
        return;
      }
      renderTimeline();
      queueAutoSave({ immediate: true, allowCreate: true });
    });

    document.querySelectorAll('[data-action="conversion-mode"]').forEach((button) => button.addEventListener('click', () => {
      const selected = selectedSeriesEvent();
      if (!selected?.recurrence?.freq) return;
      const mode = button.dataset.mode === 'multiple' ? 'multiple' : 'single';
      const choices = global.TimeGridCalendarDomain?.listSeriesOccurrences?.(selected, { limit: 18, horizonDays: 1460 }) || [];
      const defaultId = choices[0]?.start || '';
      let occurrenceIds = Array.isArray(state.recurrenceConversion?.occurrenceIds) ? [...state.recurrenceConversion.occurrenceIds] : [];
      if (mode === 'single') occurrenceIds = occurrenceIds.length ? [occurrenceIds[0]] : (defaultId ? [defaultId] : []);
      else if (!occurrenceIds.length && defaultId) occurrenceIds = [defaultId];
      state.recurrenceConversion = { mode, occurrenceIds };
      rerenderEditor();
    }));

    document.querySelectorAll('[data-action="toggle-conversion-occurrence"]').forEach((button) => button.addEventListener('click', () => {
      const id = button.dataset.id;
      if (!id) return;
      const mode = state.recurrenceConversion?.mode === 'multiple' ? 'multiple' : 'single';
      let occurrenceIds = Array.isArray(state.recurrenceConversion?.occurrenceIds) ? [...state.recurrenceConversion.occurrenceIds] : [];
      if (mode === 'single') {
        occurrenceIds = [id];
      } else if (occurrenceIds.includes(id)) {
        occurrenceIds = occurrenceIds.filter((value) => value !== id);
      } else {
        occurrenceIds.push(id);
      }
      state.recurrenceConversion = { mode, occurrenceIds };
      rerenderEditor();
    }));

    document.querySelector('[data-action="apply-event"]')?.addEventListener('click', async () => {
      const form = global.TimeGridCalendarEditor?.readEventForm?.(document);
      if (!form?.start || !form?.end) return setBanner('', 'Start and end are required.');

      const selected = null;
      if (selected?.recurrence?.freq && form.repeat === 'none') {
        const converted = global.TimeGridCalendarEditor?.convertRecurringSeries?.(selected, {
          mode: state.recurrenceConversion?.mode || 'single',
          occurrenceIds: state.recurrenceConversion?.occurrenceIds || [],
          now: new Date(),
        }) || [];
        if (!converted.length) return setBanner('', 'Choose at least one occurrence to keep.');
        state.timeline.events = global.TimeGridCalendarEditor?.deleteSeries?.(state.timeline.events, selected.id) || state.timeline.events.filter((entry) => entry.id !== selected.id);
        converted.forEach((payload) => {
          state.timeline.events = global.TimeGridCalendarEditor?.upsertSeriesEvent?.(state.timeline.events, payload) || state.timeline.events;
        });
        state.timelineDate = converted[0].start;
        state.selectedEventId = converted.length === 1 ? converted[0].id : null;
        state.selectedOccurrence = null;
        state.draftEvent = null;
        state.recurrenceConversion = null;
        renderTimeline();
        queueAutoSave({ immediate: true, allowCreate: true });
        return;
      }

      const payloadWithRecurrence = global.TimeGridCalendarEditor?.buildSeriesPayload?.({
        form,
        selectedEventId: null,
        draftEvent: state.draftEvent,
        selectedSeriesEvent: null,
        timeline: state.timeline,
      }) || null;
      if (!payloadWithRecurrence) return setBanner('', 'Could not build event payload.');
      state.timeline.events = global.TimeGridCalendarEditor?.upsertSeriesEvent?.(state.timeline.events, payloadWithRecurrence) || state.timeline.events;
      state.timelineDate = payloadWithRecurrence.start;
      state.selectedEventId = null;
      state.selectedOccurrence = null;
      state.draftEvent = null;
      state.recurrenceConversion = null;
      renderTimeline();
      queueAutoSave({ immediate: true, allowCreate: true });
    });
  }

  global.TimeGridTimelineController = Object.freeze({
    bindTimelineEditorActions,
  });
})(window);
