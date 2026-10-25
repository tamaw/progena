import { memo, useCallback, useRef } from 'react';

import LiveAssistantTurn from './LiveAssistantTurn';
import { Message } from './Message';
import { followTranscript } from '../models/transcriptScroll';

const HistoricalMessage = memo(function HistoricalMessage({
  message,
  streamEventFilter,
  onRegenerate,
}) {
  const handleMessageRegenerate = useCallback(
    () => onRegenerate?.(message.id),
    [message.id, onRegenerate],
  );

  return (
    <Message
      message={message}
      streamEventFilter={streamEventFilter}
      onRegenerate={message.role === 'assistant' && onRegenerate ? handleMessageRegenerate : undefined}
    />
  );
});

function ChatMessages({
  messages,
  streamEventFilter,
  onRegenerate,
  liveTurnStore,
  messagesContainerRef,
  isPinnedToBottomRef,
  className = '',
  children,
}) {
  // Row heights can change without a parent render (streaming, image load,
  // work disclosure). Observe rows, not the potentially height-capped wrapper.
  const observersRef = useRef(null);
  const observeTranscript = useCallback((node) => {
    observersRef.current?.forEach((observer) => observer.disconnect());
    observersRef.current = null;
    if (!node || !messagesContainerRef || !isPinnedToBottomRef
      || typeof ResizeObserver === 'undefined' || typeof MutationObserver === 'undefined') return;
    const follow = () => followTranscript(messagesContainerRef.current, isPinnedToBottomRef);
    const resizeObserver = new ResizeObserver(follow);
    [...node.children].forEach((child) => resizeObserver.observe(child));
    const mutationObserver = new MutationObserver((records) => {
      records.forEach((record) => {
        record.removedNodes.forEach((removed) => {
          if (removed.nodeType === 1) resizeObserver.unobserve(removed);
        });
        record.addedNodes.forEach((added) => {
          if (added.nodeType === 1) resizeObserver.observe(added);
        });
      });
    });
    mutationObserver.observe(node, { childList: true });
    observersRef.current = [resizeObserver, mutationObserver];
  }, [messagesContainerRef, isPinnedToBottomRef]);

  return (
    <div className={`chat-messages__inner ${className}`.trim()} ref={observeTranscript}>
      {children}
      {messages.map((message, index) => {
        return (
          <HistoricalMessage
            key={message.id ?? `${message.role}-${index}`}
            message={message}
            streamEventFilter={streamEventFilter}
            onRegenerate={onRegenerate}
          />
        );
      })}
      {liveTurnStore ? (
        <LiveAssistantTurn
          store={liveTurnStore}
          messagesContainerRef={messagesContainerRef}
          isPinnedToBottomRef={isPinnedToBottomRef}
          streamEventFilter={streamEventFilter}
        />
      ) : null}
    </div>
  );
}

export default ChatMessages;
