import { useEffect, useRef, useSyncExternalStore } from 'react';
import { Message } from './Message';
import { toCommittedAssistantMessage } from './liveAssistantTurnModel';
import PendingAssistantCursor from './PendingAssistantCursor';

export default function LiveAssistantTurn({
  store,
  messagesEndRef,
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
    if (!liveAssistantTurn || scrollFrameRef.current !== null) {
      return;
    }

    scrollFrameRef.current = requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' });
    });
  }, [liveAssistantTurn, messagesEndRef]);

  useEffect(() => () => {
    if (scrollFrameRef.current !== null) {
      cancelAnimationFrame(scrollFrameRef.current);
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
