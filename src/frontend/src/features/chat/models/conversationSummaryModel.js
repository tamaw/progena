function normalizeScope(scope) {
  if (!scope || typeof scope !== 'object' || Array.isArray(scope)) return null;
  const kind = typeof scope.kind === 'string' ? scope.kind.trim() : '';
  const id = typeof scope.id === 'string' ? scope.id.trim() : '';
  if (!id || (kind !== 'conversation' && kind !== 'subagent')) return null;
  const label = typeof scope.label === 'string' ? scope.label.trim() : '';
  return { kind, id, ...(label ? { label } : {}) };
}

/**
 * Normalizes browser-facing committed conversation-summary descriptors.
 * A descriptor is only renderable when it has a stable identity, markdown body,
 * and an explicit creation-boundary anchor. A non-null anchor renders the
 * marker AFTER that visible projected message; null renders at transcript head.
 */
export function normalizeConversationSummaryMarkers(markers) {
  const seen = new Set();
  const normalized = [];
  for (const marker of Array.isArray(markers) ? markers : []) {
    const id = typeof marker?.id === 'string' ? marker.id.trim() : '';
    const summary = typeof marker?.summary === 'string' ? marker.summary : '';
    const anchor = marker?.anchor_message_id ?? marker?.anchorMessageId;
    const anchorMessageId = typeof anchor === 'string' && anchor.trim() ? anchor.trim() : null;
    const scope = normalizeScope(marker?.scope);
    if (!id || !summary.trim() || !scope || seen.has(id)) continue;
    const committedAt = typeof marker?.committed_at === 'string'
      ? marker.committed_at
      : typeof marker?.committedAt === 'string' ? marker.committedAt : '';
    seen.add(id);
    normalized.push({ id, summary, anchorMessageId, scope, ...(committedAt ? { committedAt } : {}) });
  }
  return normalized;
}

/**
 * Merges descriptor batches by their backend-owned stable identity, retaining
 * the first appearance's chronological position and replacing its content with
 * a newer copy. This makes overlapping older-message pages idempotent.
 */
export function mergeConversationSummaryMarkers(current, incoming) {
  const merged = normalizeConversationSummaryMarkers(current);
  const indexById = new Map(merged.map((marker, index) => [marker.id, index]));
  for (const marker of normalizeConversationSummaryMarkers(incoming)) {
    const index = indexById.get(marker.id);
    if (index === undefined) {
      indexById.set(marker.id, merged.length);
      merged.push(marker);
    } else {
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
