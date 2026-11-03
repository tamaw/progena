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

function isBackgroundActivityEvent(event) {
  const eventType = event?.eventType;
  if (typeof eventType !== 'string') return false;
  if (eventType.startsWith('async_subagent_')) return true;
  if (eventType !== 'tool_result') return false;
  return [
    'run_workflow',
    'start_async_task',
    'update_async_task',
    'check_async_task',
    'list_async_tasks',
    'cancel_async_task',
  ].includes(event.metadata?.tool_name);
}

function createBackgroundActivitySnapshot(turn) {
  if (!turn) return null;
  const streamEvents = (Array.isArray(turn.streamEvents) ? turn.streamEvents : [])
    .filter(isBackgroundActivityEvent);
  return streamEvents.length ? { startedAt: turn.startedAt, streamEvents } : null;
}

function sameBackgroundActivitySnapshot(left, right) {
  if (left === right) return true;
  if (!left || !right) return false;
  return left.startedAt === right.startedAt
    && left.streamEvents.length === right.streamEvents.length
    && left.streamEvents.every((event, index) => event === right.streamEvents[index]);
}

export function createLiveAssistantTurnStore(initialValue = null) {
  let liveTurn = initialValue;
  let headerSnapshot = createHeaderSnapshot(initialValue);
  let contextUsageSnapshot = createContextUsageSnapshot(initialValue);
  let backgroundActivitySnapshot = createBackgroundActivitySnapshot(initialValue);
  const listeners = new Set();
  const headerListeners = new Set();
  const contextUsageListeners = new Set();
  const backgroundActivityListeners = new Set();
  return {
    getSnapshot: () => liveTurn,
    getHeaderSnapshot: () => headerSnapshot,
    getContextUsageSnapshot: () => contextUsageSnapshot,
    getBackgroundActivitySnapshot: () => backgroundActivitySnapshot,
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
    subscribeBackgroundActivity(listener) {
      backgroundActivityListeners.add(listener);
      return () => {
        backgroundActivityListeners.delete(listener);
      };
    },
    set(nextTurn) {
      if (Object.is(liveTurn, nextTurn)) {
        return;
      }
      liveTurn = nextTurn;
      const nextHeaderSnapshot = createHeaderSnapshot(nextTurn);
      const nextContextUsageSnapshot = createContextUsageSnapshot(nextTurn);
      const nextBackgroundActivitySnapshot = createBackgroundActivitySnapshot(nextTurn);
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
      if (!sameBackgroundActivitySnapshot(backgroundActivitySnapshot, nextBackgroundActivitySnapshot)) {
        backgroundActivitySnapshot = nextBackgroundActivitySnapshot;
        for (const listener of [...backgroundActivityListeners]) {
          listener();
        }
      }
      for (const listener of [...listeners]) {
        listener();
      }
    },
  };
}
