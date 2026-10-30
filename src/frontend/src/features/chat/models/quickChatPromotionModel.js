import { stripEphemeralStreamEvents } from './conversationPageModel';

const MAX_PERSISTED_MESSAGES = 100;

/**
 * Keeps the live promotion payload faithful for the current navigation while
 * making the reload fallback safe for browser storage.
 */
export function createQuickChatPromotion(history, threadId, summaryMarkers = []) {
  const messages = Array.isArray(history)
    ? history.map((message) => ({ ...message }))
    : [];
  const markers = Array.isArray(summaryMarkers) && summaryMarkers.length > 0
    ? summaryMarkers.map((marker) => ({ ...marker }))
    : [];
  const canonicalThreadId = typeof threadId === 'string' ? threadId : null;
  const markerPayload = markers.length > 0 ? { summaryMarkers: markers } : {};

  return {
    handoff: {
      messages,
      threadId: canonicalThreadId,
      ...markerPayload,
    },
    persistedHandoff: {
      messages: messages
        .slice(-MAX_PERSISTED_MESSAGES)
        .map((message) => {
          const persisted = { ...stripEphemeralStreamEvents(message) };
          // The main page rebuilds this denormalized trace from streamEvents.
          // Keeping it would duplicate the images just stripped above.
          delete persisted.timelineItems;
          return persisted;
        }),
      threadId: canonicalThreadId,
      ...markerPayload,
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
