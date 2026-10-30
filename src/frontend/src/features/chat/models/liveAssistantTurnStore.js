import { countReasoningTimelineEntries } from './streamEventsModel';

function createHeaderSnapshot(turn) {
  if (!turn) {
    return null;
  }
  return {
    assistantMessageId: turn.assistantMessageId ?? null,
    reasoningTokenCount: turn.reasoningTokenCount ?? 0,
    cacheReadTokenCount: turn.cacheReadTokenCount ?? 0,
    reasoningText: turn.reasoningText ?? '',
    reasoningEventCount: countReasoningTimelineEntries(
      turn.timelineItems?.map((item) => item.event),
      turn.reasoningText,
    ),
  };
}

function createContextUsageSnapshot(turn) {
  return turn?.contextUsage ?? null;
}

function sameContextUsage(left, right) {
  return left?.inputTokens === right?.inputTokens
    && left?.outputTokens === right?.outputTokens
    && left?.totalTokens === right?.totalTokens
    && left?.profileName === right?.profileName
    && left?.deployment === right?.deployment
    && left?.providerId === right?.providerId
    // Savings may arrive after the same footprint; notify the tooltip too.
    && left?.compaction?.beforeInputTokens === right?.compaction?.beforeInputTokens
    && left?.compaction?.afterInputTokens === right?.compaction?.afterInputTokens
    && left?.compaction?.freedInputTokens === right?.compaction?.freedInputTokens;
}

function sameHeaderSnapshot(left, right) {
  return left?.assistantMessageId === right?.assistantMessageId
    && left?.reasoningTokenCount === right?.reasoningTokenCount
    && left?.cacheReadTokenCount === right?.cacheReadTokenCount
    && left?.reasoningText === right?.reasoningText
    && left?.reasoningEventCount === right?.reasoningEventCount;
}

export function createLiveAssistantTurnStore(initialValue = null) {
  let liveTurn = initialValue;
  let headerSnapshot = createHeaderSnapshot(initialValue);
  let contextUsageSnapshot = createContextUsageSnapshot(initialValue);
  const listeners = new Set();
  const headerListeners = new Set();
  const contextUsageListeners = new Set();
  return {
    getSnapshot: () => liveTurn,
    getHeaderSnapshot: () => headerSnapshot,
    getContextUsageSnapshot: () => contextUsageSnapshot,
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
    subscribeContextUsage(listener) {
      contextUsageListeners.add(listener);
      return () => {
        contextUsageListeners.delete(listener);
      };
    },
    set(nextTurn) {
      if (Object.is(liveTurn, nextTurn)) {
        return;
      }
      liveTurn = nextTurn;
      const nextHeaderSnapshot = createHeaderSnapshot(nextTurn);
      const nextContextUsageSnapshot = createContextUsageSnapshot(nextTurn);
      if (!sameHeaderSnapshot(headerSnapshot, nextHeaderSnapshot)) {
        headerSnapshot = nextHeaderSnapshot;
        for (const listener of [...headerListeners]) {
          listener();
        }
      }
      if (!sameContextUsage(contextUsageSnapshot, nextContextUsageSnapshot)) {
        contextUsageSnapshot = nextContextUsageSnapshot;
        for (const listener of [...contextUsageListeners]) {
          listener();
        }
      }
      for (const listener of [...listeners]) {
        listener();
      }
    },
  };
}
