import { memo, useCallback, useMemo, useRef } from 'react';

import LiveAssistantTurn from './LiveAssistantTurn';
import ConversationSummaryMarker from './ConversationSummaryMarker';
import { Message } from './Message';
import { buildConversationSummaryEntries, normalizeConversationSummaryMarkers } from '../models/conversationSummaryModel';
import { followTranscript } from '../models/transcriptScroll';

const HistoricalMessage = memo(function HistoricalMessage({
  message,
  summaryMarkers,
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
      summaryMarkers={summaryMarkers}
      streamEventFilter={streamEventFilter}
      onRegenerate={message.role === 'assistant' && onRegenerate ? handleMessageRegenerate : undefined}
    />
  );
});

function ChatMessages({
  messages,
  summaryMarkers,
  liveOwnedMessageId,
  streamEventFilter,
  onRegenerate,
  liveTurnStore,
  messagesContainerRef,
  isPinnedToBottomRef,
  interruption,
  isGenerating = false,
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
  const normalizedSummaryMarkers = useMemo(
    () => normalizeConversationSummaryMarkers(summaryMarkers),
    [summaryMarkers],
  );
  const transcriptEntries = useMemo(
    () => buildConversationSummaryEntries(messages, normalizedSummaryMarkers),
    [messages, normalizedSummaryMarkers],
  );
  const nestedSummaryMarkers = useMemo(
    () => normalizedSummaryMarkers.filter((marker) => marker.scope.kind === 'subagent'),
    [normalizedSummaryMarkers],
  );
  const liveOwnedMarkers = useMemo(() => {
    if (!liveOwnedMessageId) return [];
    const visibleMarkerIds = new Set(
      transcriptEntries
        .filter((entry) => entry.kind === 'conversation_summary')
        .map((entry) => entry.id),
    );
    return normalizedSummaryMarkers.filter((marker) => (
      marker.anchorMessageId === liveOwnedMessageId && !visibleMarkerIds.has(marker.id)
    ));
  }, [liveOwnedMessageId, normalizedSummaryMarkers, transcriptEntries]);
  const liveOwnedRootMarkers = liveOwnedMarkers.filter((marker) => marker.scope.kind === 'conversation');
  return (
    <div className={`chat-messages__inner ${className}`.trim()} ref={observeTranscript}>
      {children}
      {transcriptEntries.map((entry, index) => {
        if (entry.kind === 'conversation_summary') {
          return <ConversationSummaryMarker key={entry.id} marker={entry.marker} />;
        }
        const message = entry.message;
        return (
          <HistoricalMessage
            key={message.id ?? `${message.role}-${index}`}
            message={message}
            summaryMarkers={message.role === 'assistant' ? nestedSummaryMarkers : undefined}
            streamEventFilter={streamEventFilter}
            onRegenerate={onRegenerate}
          />
        );
      })}
      {liveTurnStore ? (
        <LiveAssistantTurn
          store={liveTurnStore}
          summaryMarkers={nestedSummaryMarkers}
          messagesContainerRef={messagesContainerRef}
          isPinnedToBottomRef={isPinnedToBottomRef}
          streamEventFilter={streamEventFilter}
        />
      ) : null}
      {liveOwnedRootMarkers.map((marker) => (
        <ConversationSummaryMarker key={marker.id} marker={marker} />
      ))}
      {interruption && !isGenerating ? (
        <p className="chat-messages__interruption" role="status">
          <strong>Interrupted</strong> — the runtime restarted during this turn. The last step may have partly run. Type continue to carry on.
        </p>
      ) : null}
    </div>
  );
}

export default ChatMessages;
