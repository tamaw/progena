import { countReasoningTimelineEntries } from './streamEventsModel';

function createHeaderSnapshot(turn) {
  if (!turn) {
    return null;
  }
  return {
    assistantMessageId: turn.assistantMessageId ?? null,
    reasoningTokenCount: turn.reasoningTokenCount ?? 0,
    reasoningText: turn.reasoningText ?? '',
    reasoningEventCount: countReasoningTimelineEntries(
      turn.timelineItems?.map((item) => item.event),
      turn.reasoningText,
    ),
  };
}

function sameHeaderSnapshot(left, right) {
  return left?.assistantMessageId === right?.assistantMessageId
    && left?.reasoningTokenCount === right?.reasoningTokenCount
    && left?.reasoningText === right?.reasoningText
    && left?.reasoningEventCount === right?.reasoningEventCount;
}

export function createLiveAssistantTurnStore(initialValue = null) {
  let liveTurn = initialValue;
  let headerSnapshot = createHeaderSnapshot(initialValue);
  const listeners = new Set();
  const headerListeners = new Set();

  return {
    getSnapshot: () => liveTurn,
    getHeaderSnapshot: () => headerSnapshot,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    subscribeHeader(listener) {
      headerListeners.add(listener);
      return () => {
        headerListeners.delete(listener);
      };
    },
    set(nextTurn) {
      if (Object.is(liveTurn, nextTurn)) {
        return;
      }
      liveTurn = nextTurn;
      const nextHeaderSnapshot = createHeaderSnapshot(nextTurn);
      if (!sameHeaderSnapshot(headerSnapshot, nextHeaderSnapshot)) {
        headerSnapshot = nextHeaderSnapshot;
        for (const listener of [...headerListeners]) {
          listener();
        }
      }
      for (const listener of [...listeners]) {
        listener();
      }
    },
  };
}
