(function attachTimeGridScheduleXFrame(global) {
  const EditorModes = Object.freeze({
    CREATE_SINGLE: 'create_single',
    CREATE_SERIES: 'create_series',
    EDIT_SINGLE: 'edit_single',
    EDIT_SERIES: 'edit_series',
    EDIT_OCCURRENCE: 'edit_occurrence',
    READONLY_SERIES: 'readonly_series',
    READONLY_OCCURRENCE: 'readonly_occurrence',
  });

  const TimelineKinds = Object.freeze({
    PERSONAL: 'personal',
    WRAPPER: 'wrapper',
    PUBLISHED: 'published',
  });

  const SourceKinds = Object.freeze({
    OWNED: 'owned',
    WRAPPER_OWNED: 'wrapper_owned',
    SUBSCRIPTION: 'subscription',
    PUBLISHED: 'published',
    UNKNOWN: 'unknown',
  });

  const PublishVisibility = Object.freeze({
    PUBLIC: 'public',
    INVITED: 'invited',
    PRIVATE: 'private',
    ARCHIVED: 'archived',
    REMOVED: 'removed',
  });

  function getSeriesEvent(legacyState) {
    return legacyState?.timeline?.events?.find((item) => item.id === legacyState.selectedEventId) || null;
  }

  function getDraftEvent(legacyState) {
    return legacyState?.draftEvent || null;
  }

  function sourceKindForEvent(legacyState, seriesEvent) {
    if (!seriesEvent) return SourceKinds.OWNED;
    if (seriesEvent.editable === false && seriesEvent.source_subscription_id) return SourceKinds.SUBSCRIPTION;
    if (legacyState?.timeline?.kind === TimelineKinds.WRAPPER && seriesEvent.source_timeline_id && seriesEvent.source_timeline_id !== legacyState.timeline.id) return SourceKinds.WRAPPER_OWNED;
    if (seriesEvent.editable === false) return SourceKinds.UNKNOWN;
    return SourceKinds.OWNED;
  }

  function modeForLegacySelection(legacyState) {
    const seriesEvent = getSeriesEvent(legacyState);
    const draftEvent = getDraftEvent(legacyState);
    const occurrence = legacyState?.selectedOccurrence || null;
    const readOnly = !!(seriesEvent && seriesEvent.editable === false);

    if (readOnly && occurrence) return EditorModes.READONLY_OCCURRENCE;
    if (readOnly) return EditorModes.READONLY_SERIES;
    if (occurrence) return EditorModes.EDIT_OCCURRENCE;
    if (seriesEvent?.recurrence?.freq) return EditorModes.EDIT_SERIES;
    if (seriesEvent) return EditorModes.EDIT_SINGLE;
    if (draftEvent?.recurrence?.freq) return EditorModes.CREATE_SERIES;
    return EditorModes.CREATE_SINGLE;
  }

  function buildLegacyEditorContext(legacyState) {
    const seriesEvent = getSeriesEvent(legacyState);
    const draftEvent = getDraftEvent(legacyState);
    const mode = modeForLegacySelection(legacyState);
    const occurrence = legacyState?.selectedOccurrence || null;
    const sourceKind = sourceKindForEvent(legacyState, seriesEvent);
    const event = seriesEvent || draftEvent;

    return {
      mode,
      event,
      seriesEvent,
      draftEvent,
      occurrence,
      isReadOnly: mode === EditorModes.READONLY_SERIES || mode === EditorModes.READONLY_OCCURRENCE,
      sourceKind,
      timelineKind: legacyState?.timeline?.kind || TimelineKinds.PERSONAL,
      canEditSeries: !!seriesEvent && seriesEvent.editable !== false,
      canEditOccurrence: !!occurrence && !!seriesEvent && seriesEvent.editable !== false,
      canCreate: !seriesEvent || seriesEvent.editable !== false,
      canDeleteSeries: !!seriesEvent && seriesEvent.editable !== false,
      canDeleteOccurrence: !!occurrence && !!seriesEvent && seriesEvent.editable !== false,
      labels: {
        panelTitle: mode === EditorModes.EDIT_OCCURRENCE || mode === EditorModes.READONLY_OCCURRENCE
          ? 'Edit occurrence'
          : mode === EditorModes.EDIT_SERIES || mode === EditorModes.READONLY_SERIES
            ? 'Edit series'
            : mode === EditorModes.EDIT_SINGLE
              ? 'Edit event'
              : mode === EditorModes.CREATE_SERIES
                ? 'Create recurring event'
                : 'Create event',
        saveAction: mode === EditorModes.CREATE_SINGLE || mode === EditorModes.CREATE_SERIES ? 'Add event' : 'Save changes',
      },
      premium: {
        dragAndDrop: false,
        resize: false,
        eventModal: false,
        comments: false,
        resources: false,
      },
    };
  }

  global.TimeGridScheduleXFrame = Object.freeze({
    EditorModes,
    TimelineKinds,
    SourceKinds,
    PublishVisibility,
    buildLegacyEditorContext,
    rendererContract: Object.freeze({
      requiredInputs: [
        'events',
        'selectedDate',
        'selectedView',
        'readOnly',
        'permissionScope',
        'publishedVisibility',
      ],
      requiredCallbacks: [
        'onSelectRange',
        'onSelectEvent',
        'onChangeOccurrence',
        'onChangeSeries',
        'onDeleteOccurrence',
        'onDeleteSeries',
      ],
      premiumPlaceholders: [
        'dragAndDrop',
        'resize',
        'eventModal',
        'comments',
        'resources',
      ],
    }),
  });
})(window);
