import { useEffect, useRef, useSyncExternalStore } from 'react';
import { Message } from './Message';
import { toCommittedAssistantMessage } from './liveAssistantTurnModel';
import PendingAssistantCursor from './PendingAssistantCursor';

export default function LiveAssistantTurn({
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

  return (
    <Message
      key={liveAssistantTurn.streamId}
      message={toCommittedAssistantMessage(liveAssistantTurn)}
      streamEventFilter={streamEventFilter}
      hideContent={hideContent}
      showImages={showImages}
    />
  );
}
