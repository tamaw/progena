function normalizeScope(scope) {
  if (!scope || typeof scope !== 'object' || Array.isArray(scope)) return null;
  const kind = typeof scope.kind === 'string' ? scope.kind.trim() : '';
  const id = typeof scope.id === 'string' ? scope.id.trim() : '';
  if (!id || (kind !== 'conversation' && kind !== 'subagent')) return null;
  const label = typeof scope.label === 'string' ? scope.label.trim() : '';
  const expectedKeys = label ? ['kind', 'id', 'label'] : ['kind', 'id'];
  const isCanonical = scope.kind === kind
    && scope.id === id
    && (!label || scope.label === label)
    && Object.keys(scope).length === expectedKeys.length
    && expectedKeys.every((key) => Object.hasOwn(scope, key));
  return isCanonical ? scope : { kind, id, ...(label ? { label } : {}) };
}

function normalizeMarker(marker) {
  const id = typeof marker?.id === 'string' ? marker.id.trim() : '';
  const summary = typeof marker?.summary === 'string' ? marker.summary : '';
  const anchor = marker?.anchor_message_id ?? marker?.anchorMessageId;
  const anchorMessageId = typeof anchor === 'string' && anchor.trim() ? anchor.trim() : null;
  const scope = normalizeScope(marker?.scope);
  if (!id || !summary.trim() || !scope) return null;
  const committedAt = typeof marker?.committed_at === 'string'
    ? marker.committed_at
    : typeof marker?.committedAt === 'string' ? marker.committedAt : '';
  const expectedKeys = committedAt
    ? ['id', 'summary', 'anchorMessageId', 'scope', 'committedAt']
    : ['id', 'summary', 'anchorMessageId', 'scope'];
  const isCanonical = marker
    && typeof marker === 'object'
    && !Array.isArray(marker)
    && marker.id === id
    && marker.summary === summary
    && marker.anchorMessageId === anchorMessageId
    && marker.scope === scope
    && (!committedAt || marker.committedAt === committedAt)
    && Object.keys(marker).length === expectedKeys.length
    && expectedKeys.every((key) => Object.hasOwn(marker, key));
  return isCanonical ? marker : {
    id,
    summary,
    anchorMessageId,
    scope,
    ...(committedAt ? { committedAt } : {}),
  };
}

function areSummaryMarkersEqual(left, right) {
  return left === right || (
    left.id === right.id
    && left.summary === right.summary
    && left.anchorMessageId === right.anchorMessageId
    && left.committedAt === right.committedAt
    && left.scope.kind === right.scope.kind
    && left.scope.id === right.scope.id
    && left.scope.label === right.scope.label
  );
}

/**
 * Normalizes browser-facing committed conversation-summary descriptors.
 * A descriptor is only renderable when it has a stable identity, markdown body,
 * and an explicit creation-boundary anchor. A non-null anchor renders the
 * marker AFTER that visible projected message; null renders at transcript head.
 */
export function normalizeConversationSummaryMarkers(markers) {
  const source = Array.isArray(markers) ? markers : [];
  const seen = new Set();
  const normalized = [];
  let unchanged = Array.isArray(markers);
  for (const marker of source) {
    const normalizedMarker = normalizeMarker(marker);
    if (!normalizedMarker || seen.has(normalizedMarker.id)) {
      unchanged = false;
      continue;
    }
    seen.add(normalizedMarker.id);
    normalized.push(normalizedMarker);
    if (normalizedMarker !== marker) unchanged = false;
  }
  return unchanged && normalized.length === source.length ? markers : normalized;
}

/**
 * Merges descriptor batches by their backend-owned stable identity, retaining
 * the first appearance's chronological position and replacing its content with
 * a newer copy. This makes overlapping older-message pages idempotent.
 */
export function mergeConversationSummaryMarkers(current, incoming) {
  const normalizedCurrent = normalizeConversationSummaryMarkers(current);
  const normalizedIncoming = normalizeConversationSummaryMarkers(incoming);
  if (normalizedIncoming.length === 0) {
    return normalizedCurrent;
  }

  const indexById = new Map(normalizedCurrent.map((marker, index) => [marker.id, index]));
  let merged = normalizedCurrent;
  for (const marker of normalizedIncoming) {
    const index = indexById.get(marker.id);
    if (index === undefined) {
      if (merged === normalizedCurrent) merged = normalizedCurrent.slice();
      indexById.set(marker.id, merged.length);
      merged.push(marker);
    } else if (!areSummaryMarkersEqual(merged[index], marker)) {
      if (merged === normalizedCurrent) merged = normalizedCurrent.slice();
      merged[index] = marker;
    }
  }
  return merged;
}

/**
 * Interleaves root-scope committed markers after their backend-resolved message
 * anchors. Nested markers belong in their matching subagent card instead.
 * Markers whose anchor is not in this loaded page deliberately stay hidden
 * until that page is present; rendering them at the tail would lie about their
 * creation boundary.
 */
export function buildConversationSummaryEntries(messages, markers) {
  const normalizedMarkers = normalizeConversationSummaryMarkers(markers)
    .filter((marker) => marker.scope.kind === 'conversation');
  const markerBatches = new Map();
  for (const marker of normalizedMarkers) {
    const key = marker.anchorMessageId ?? '';
    const batch = markerBatches.get(key);
    if (batch) batch.push(marker);
    else markerBatches.set(key, [marker]);
  }
  for (const batch of markerBatches.values()) {
    batch.sort((left, right) => {
      if (!left.committedAt || !right.committedAt) return 0;
      return left.committedAt.localeCompare(right.committedAt);
    });
  }

  const entries = [];
  for (const marker of markerBatches.get('') || []) {
    entries.push({ kind: 'conversation_summary', id: marker.id, marker });
  }
  for (const message of Array.isArray(messages) ? messages : []) {
    entries.push({ kind: 'message', id: message?.id, message });
    const anchorId = typeof message?.id === 'string' ? message.id : '';
    for (const marker of markerBatches.get(anchorId) || []) {
      entries.push({ kind: 'conversation_summary', id: marker.id, marker });
    }
  }
  return entries;
}

export function summaryScopeLabel(scope) {
  if (scope?.label) return scope.label;
  return scope?.kind === 'subagent' ? 'Subagent' : 'Conversation';
}

/** Live events only represent in-progress native callback activity. */
export function applyConversationSummaryActivity(activities, streamEvent) {
  const eventType = streamEvent?.event_type;
  if (eventType !== 'conversation_summary_started' && eventType !== 'conversation_summary_finished') {
    return activities;
  }
  const id = typeof streamEvent?.metadata?.summary_id === 'string'
    ? streamEvent.metadata.summary_id.trim()
    : '';
  const scope = normalizeScope(streamEvent?.metadata?.scope);
  if (!id || !scope) return activities;
  const current = Array.isArray(activities) ? activities : [];
  if (eventType === 'conversation_summary_finished') {
    const next = current.filter((activity) => activity.id !== id || activity.scope.kind !== scope.kind || activity.scope.id !== scope.id);
    return next.length === current.length ? current : next;
  }
  if (current.some((activity) => activity.id === id && activity.scope.kind === scope.kind && activity.scope.id === scope.id)) {
    return current;
  }
  return [...current, { id, scope }];
}
