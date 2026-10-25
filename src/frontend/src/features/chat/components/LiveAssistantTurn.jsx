import { memo, useEffect, useLayoutEffect, useRef, useSyncExternalStore } from 'react';
import { Message } from './Message';
import { toCommittedAssistantMessage } from '../models/liveAssistantTurnModel';
import PendingAssistantCursor from './PendingAssistantCursor';
import { followTranscript } from '../models/transcriptScroll';
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
  // rendered form. Re-follow after that DOM commit so the final answer cannot
  // land below the viewport (or, while reading a long answer, drift away).
  useLayoutEffect(() => {
    const wasStreaming = wasStreamingRef.current;
    wasStreamingRef.current = Boolean(liveAssistantTurn?.isStreaming);
    if (!liveAssistantTurn || liveAssistantTurn.isStreaming || !wasStreaming) {
      return;
    }

    followTranscript(messagesContainerRef.current, isPinnedToBottomRef);
  }, [liveAssistantTurn, messagesContainerRef, isPinnedToBottomRef]);

  // followTranscript decides per frame: hold the answer being read, follow
  // the tail while pinned, or leave a scrolled-back reader alone.
  useEffect(() => {
    if (!liveAssistantTurn || scrollFrameRef.current !== null) return;

    scrollFrameRef.current = requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      followTranscript(messagesContainerRef.current, isPinnedToBottomRef);
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

  return (
    <Message
      key={liveAssistantTurn.streamId}
      message={toCommittedAssistantMessage(liveAssistantTurn)}
      streamEventFilter={streamEventFilter}
      hideContent={hideContent}
      showImages={showImages}
    />
  );
});

export default LiveAssistantTurn;
