import { retainAuthoritativeUsage, stripEphemeralStreamEvents } from './conversationPageModel';

const MAX_PERSISTED_MESSAGES = 100;

function summaryMarkerAnchorId(marker) {
  const anchor = marker?.anchor_message_id ?? marker?.anchorMessageId;
  return typeof anchor === 'string' && anchor.trim() ? anchor.trim() : null;
}

/**
 * Keeps the live promotion payload faithful for the current navigation while
 * making the reload fallback safe for browser storage.
 */
export function createQuickChatPromotion(history, threadId, summaryMarkers = []) {
  const messages = Array.isArray(history)
    ? history.map((message) => retainAuthoritativeUsage(message))
    : [];
  const markers = Array.isArray(summaryMarkers) && summaryMarkers.length > 0
    ? summaryMarkers.map((marker) => ({ ...marker }))
    : [];
  const canonicalThreadId = typeof threadId === 'string' ? threadId : null;
  const markerPayload = markers.length > 0 ? { summaryMarkers: markers } : {};
  const persistedMessages = messages
    .slice(-MAX_PERSISTED_MESSAGES)
    .map((message) => {
      const persisted = { ...stripEphemeralStreamEvents(message) };
      // The main page rebuilds this denormalized trace from streamEvents.
      // Keeping it would duplicate the images just stripped above.
      delete persisted.timelineItems;
      return persisted;
    });
  let persistedMarkers = markers;
  if (messages.length > MAX_PERSISTED_MESSAGES && markers.length > 0) {
    const retainedMessageIds = new Set();
    for (const message of persistedMessages) {
      retainedMessageIds.add(message.id);
    }
    // A head marker represents the beginning of the full transcript, which
    // is absent from a truncated fallback. Nested markers still anchor their
    // parent root message, so scope never decides this retention boundary.
    persistedMarkers = markers.filter((marker) => {
      const anchorMessageId = summaryMarkerAnchorId(marker);
      return Boolean(anchorMessageId) && retainedMessageIds.has(anchorMessageId);
    });
  }
  const persistedMarkerPayload = persistedMarkers.length > 0
    ? { summaryMarkers: persistedMarkers }
    : {};

  return {
    handoff: {
      messages,
      threadId: canonicalThreadId,
      ...markerPayload,
    },
    persistedHandoff: {
      messages: persistedMessages,
      threadId: canonicalThreadId,
      ...persistedMarkerPayload,
    },
  };
}

export function persistQuickChatHandoff(storageKey, handoff, storage) {
  try {
    (storage ?? localStorage).setItem(storageKey, JSON.stringify(handoff));
    return true;
  } catch {
    return false;
  }
}
