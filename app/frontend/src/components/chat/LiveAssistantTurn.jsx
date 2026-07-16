import { memo, Profiler, useEffect, useLayoutEffect, useRef, useSyncExternalStore } from 'react';
import { Message } from './Message';
import { toCommittedAssistantMessage } from './liveAssistantTurnModel';
import PendingAssistantCursor from './PendingAssistantCursor';
import {
  chatPerformanceNow,
  isChatPerformanceTracingEnabled,
  traceChatPerformance,
} from './chatPerformanceTrace';


let liveCommitSequence = 0;

function traceLiveTurnCommit(componentId, streamId, phase, actualDuration, baseDuration, startTime, commitTime) {
  if (!isChatPerformanceTracingEnabled()) return;

  const commitSequence = ++liveCommitSequence;
  traceChatPerformance('react.live_turn_commit', {
    streamId,
    phase,
    actualDurationMs: actualDuration,
    baseDurationMs: baseDuration,
    renderStartMs: startTime,
    reactCommitMs: commitTime,
    commitSequence,
    componentId,
  });
  const scheduledAt = chatPerformanceNow();
  requestAnimationFrame(() => {
    traceChatPerformance('react.live_turn_paint_proxy', {
      streamId,
      commitSequence,
      commitToFrameMs: chatPerformanceNow() - scheduledAt,
    });
  });
}
const LiveAssistantTurn = memo(function LiveAssistantTurn({
  store,
  messagesContainerRef,
  isPinnedToBottomRef,
  streamEventFilter,
  hideContent,
  showImages,
}) {
  const liveAssistantTurn = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getSnapshot,
  );
  const scrollFrameRef = useRef(null);
  const wasStreamingRef = useRef(Boolean(liveAssistantTurn?.isStreaming));

  // The terminal handoff replaces literal streaming text with its final
  // rendered form. Tail it after that DOM commit so the final answer cannot
  // land below the viewport while preserving a user's scroll-back position.
  useLayoutEffect(() => {
    const wasStreaming = wasStreamingRef.current;
    wasStreamingRef.current = Boolean(liveAssistantTurn?.isStreaming);
    if (!liveAssistantTurn || liveAssistantTurn.isStreaming || !wasStreaming || !isPinnedToBottomRef.current) {
      return;
    }

    const container = messagesContainerRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  }, [liveAssistantTurn, messagesContainerRef, isPinnedToBottomRef]);

  useEffect(() => {
    if (
      !liveAssistantTurn
      || !isPinnedToBottomRef.current
      || scrollFrameRef.current !== null
    ) {
      return;
    }

    const container = messagesContainerRef.current;
    if (!container) return;

    scrollFrameRef.current = requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      const currentContainer = messagesContainerRef.current;
      if (currentContainer && isPinnedToBottomRef.current) {
        currentContainer.scrollTop = currentContainer.scrollHeight;
      }
    });
  }, [liveAssistantTurn, messagesContainerRef, isPinnedToBottomRef]);

  useEffect(() => () => {
    if (scrollFrameRef.current !== null) {
      cancelAnimationFrame(scrollFrameRef.current);
      scrollFrameRef.current = null;
    }
  }, []);

  if (!liveAssistantTurn) {
    return null;
  }

  if (liveAssistantTurn.assistantMessageId === null && liveAssistantTurn.timelineItems.length === 0) {
    return <PendingAssistantCursor />;
  }

  const message = (
    <Message
      key={liveAssistantTurn.streamId}
      message={toCommittedAssistantMessage(liveAssistantTurn)}
      streamEventFilter={streamEventFilter}
      hideContent={hideContent}
      showImages={showImages}
    />
  );

  if (!isChatPerformanceTracingEnabled()) {
    return message;
  }

  return (
    <Profiler
      id="chat-live-turn"
      onRender={(id, phase, actualDuration, baseDuration, startTime, commitTime) => {
        traceLiveTurnCommit(id, liveAssistantTurn.streamId, phase, actualDuration, baseDuration, startTime, commitTime);
      }}
    >
      {message}
    </Profiler>
  );
});

export default LiveAssistantTurn;
