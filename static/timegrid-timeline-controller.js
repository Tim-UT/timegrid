(function attachTimeGridTimelineController(global) {
  function bindTimelineEditorActions(config) {
    const {
      state,
      renderTimeline,
      renderTimelineSidebarOnly,
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

    if (!sidebarOnly) {
      document.querySelector('[data-action="logout"]')?.addEventListener('click', logout);
      document.querySelector('[data-action="save-timeline"]')?.addEventListener('click', () => saveTimeline());
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
      if (!startInput || !endInput || !startInput.value) return;
      const startDate = new Date(startInput.value);
      if (Number.isNaN(startDate.getTime())) return;
      const endDate = endInput.value ? new Date(endInput.value) : null;
      if (!endInput.value || Number.isNaN(endDate?.getTime?.()) || endDate <= startDate || endInput.dataset.autofill !== 'manual') {
        const nextEnd = new Date(startDate.getTime() + 60 * 60 * 1000);
        const offset = nextEnd.getTimezoneOffset();
        const local = new Date(nextEnd.getTime() - offset * 60000);
        endInput.value = local.toISOString().slice(0, 16);
        endInput.dataset.autofill = 'auto';
      }
    };

    repeatSelect?.addEventListener('change', () => {
      const selected = selectedSeriesEvent();
      const shouldRerender = !!(selected?.recurrence?.freq && !state.selectedOccurrence && repeatSelect.value === 'none');
      syncRepeatUntil();
      syncRecurrenceConversionState();
      if (shouldRerender) {
        rerenderEditor();
      }
    });
    startInput?.addEventListener('change', syncEndAfterStart);
    endInput?.addEventListener('change', () => {
      endInput.dataset.autofill = 'manual';
    });
    syncRepeatUntil();

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
      if (selectedEventReadOnly()) return setBanner('', 'This source is read-only. Add a new event instead.');
      const form = global.TimeGridCalendarEditor?.readEventForm?.(document);
      if (!form?.start || !form?.end) return setBanner('', 'Start and end are required.');

      if (state.selectedOccurrence) {
        const base = selectedSeriesEvent();
        if (!base) return;
        global.TimeGridCalendarEditor?.applyOccurrenceEdit?.(base, state.selectedOccurrence.recurrenceId, form);
        renderTimeline();
        queueAutoSave({ immediate: true, allowCreate: true });
        return;
      }

      const selected = selectedSeriesEvent();
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
        selectedEventId: state.selectedEventId,
        draftEvent: state.draftEvent,
        selectedSeriesEvent: selectedSeriesEvent(),
        timeline: state.timeline,
      }) || null;
      if (!payloadWithRecurrence) return setBanner('', 'Could not build event payload.');
      state.timeline.events = global.TimeGridCalendarEditor?.upsertSeriesEvent?.(state.timeline.events, payloadWithRecurrence) || state.timeline.events;
      state.timelineDate = payloadWithRecurrence.start;
      state.selectedEventId = payloadWithRecurrence.id;
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
