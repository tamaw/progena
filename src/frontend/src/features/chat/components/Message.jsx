import { memo, useMemo, useRef } from 'react';
import { useMermaidRender } from '../../../hooks/useMermaidRender';
import { collectMessageImages } from '../models/conversationModel';
import { hasReasoningContent, isReasoningEvent } from '../models/streamEventsModel';
import TimelineItem from './TimelineItem';
import WorkGroup from './WorkGroup';
import { buildWorkGroups, timelineItemKey } from '../models/workGroupModel';
import { buildDefaultAssistantTimeline } from '../models/liveAssistantTurnModel';
import MessageImageStrip from './MessageImageStrip';
import AttachmentFileIcon from './AttachmentFileIcon';
import { describeAttachmentFileType, formatAttachmentSize } from '../models/attachmentFileTypes';
import MessageActions from './MessageActions';
import { appendStreamingCursor } from '../models/messageRenderUtils';
import { renderSafeMarkdown } from '../../../lib/safeMarkdown';
import { filterTimelineItems } from '../models/streamFilters';
import InterruptedMarker from './InterruptedMarker';

function buildSyntheticReasoningEvent(
  reasoningText,
  reasoningTokenCount,
  reasoningTimestamp,
  messageId,
  idSuffix = '',
) {
  const trimmed = reasoningText.trim();
  return {
    id: `${messageId}-reasoning${idSuffix}`,
    eventType: 'reasoning_summary',
    timestamp: reasoningTimestamp || undefined,
    content: reasoningText,
    metadata: {
      source: 'message_reasoning',
      text_length: trimmed.length,
      ...(Number.isFinite(reasoningTokenCount) && reasoningTokenCount > 0
        ? { reasoning_tokens: reasoningTokenCount }
        : {}),
    },
  };
}


// Backend-loaded (non-live) assistant messages carry reasoning as plain
// fields rather than reasoning stream_events in their timelineItems -- see
// chatSessionsModel.js's buildDefaultAssistantTimeline, which never
// synthesizes one. Inject one synthetic reasoning row per model call
// (reasoningSegments; older payloads only have the joined reasoningText) at
// its chronological position, so each lands ahead of the tool calls it led
// to, matching the rows live streaming builds in liveAssistantTurnModel.js.
function withReasoningFallback(
  timelineItems,
  { reasoningText, reasoningSegments, reasoningTokenCount, reasoningTimestamp, messageId, isStreaming },
) {
  const hasReasoningEntry = timelineItems.some((item) =>
    item.kind === 'stream_event' && isReasoningEvent(item.event) && hasReasoningContent(item.event)
  );
  if (isStreaming || hasReasoningEntry) return timelineItems;

  const segments = Array.isArray(reasoningSegments) && reasoningSegments.length > 0
    ? reasoningSegments
    : [{ text: typeof reasoningText === 'string' ? reasoningText : '', timestamp: reasoningTimestamp }];
  const usableSegments = segments.filter((segment) => segment.text.trim());
  if (usableSegments.length === 0) return timelineItems;

  const items = timelineItems.slice();
  let searchFrom = 0;
  usableSegments.forEach((segment, segmentIndex) => {
    const event = buildSyntheticReasoningEvent(
      segment.text,
      // The turn's token total belongs to a single row; split rows estimate.
      usableSegments.length === 1 ? reasoningTokenCount : 0,
      segment.timestamp,
      messageId,
      usableSegments.length === 1 ? '' : `-${segmentIndex}`,
    );
    const reasoningItem = { kind: 'stream_event', id: event.id, event, timestamp: segment.timestamp };
    const reasoningTime = Date.parse(segment.timestamp || '');
    let insertAt = -1;
    if (Number.isFinite(reasoningTime)) {
      insertAt = items.findIndex((item, index) => {
        if (index < searchFrom) return false;
        const timestamp = item.timestamp ?? item.event?.timestamp;
        const itemTime = typeof timestamp === 'number' ? timestamp : Date.parse(timestamp || '');
        return Number.isFinite(itemTime) && itemTime >= reasoningTime;
      });
    }
    if (insertAt < 0) {
      const assistantIndex = items.findIndex((item) => item.kind === 'assistant_message');
      insertAt = assistantIndex >= 0 ? Math.max(assistantIndex, searchFrom) : items.length;
    }
    items.splice(insertAt, 0, reasoningItem);
    searchFrom = insertAt + 1;
  });
  return items;
}

function MessageView({
  message,
  streamEventFilter,
  onRegenerate,
  summaryMarkers,
  summaryActivities,
}) {
  const {
    role,
    content,
    timestamp,
    id: messageId = `message-${timestamp || 'unknown'}`,
    isStreaming,
    error,
    isScheduled,
    streamEvents = [],
    reasoningTokenCount = 0,
    reasoningText = '',
    reasoningTimestamp = null,
    reasoningSegments = [],
    timelineItems,
    workActive = false,
    responseStatus = 'unknown',
    attachments = [],
    interruptionReason,
  } = message;
  const workResponseStatus = ['running', 'completed', 'failed', 'cancelled', 'interrupted', 'unknown'].includes(responseStatus)
    ? responseStatus
    : 'unknown';
  // A turn that never finished (the backend reports `interrupted` once a later
  // message superseded it) keeps its Interrupted row in place in history.
  const effectiveInterruptionReason = interruptionReason
    ?? (responseStatus === 'cancelled' ? 'user_cancelled' : undefined)
    ?? (responseStatus === 'interrupted' ? 'stopped_early' : undefined);
  const regenerateMessage = useMemo(
    () => onRegenerate ? () => onRegenerate(messageId) : undefined,
    [messageId, onRegenerate],
  );

  const isAssistant = role === 'assistant';
  const assistantTimeline = useMemo(() => (
    isAssistant
      ? (Array.isArray(timelineItems) && timelineItems.length > 0
        ? timelineItems : buildDefaultAssistantTimeline(message))
      : []
  ), [isAssistant, message, timelineItems]);

  const formattedContent = useMemo(() => {
    if (!content || isAssistant) return '';
    const sanitized = renderSafeMarkdown(content);
    return isStreaming ? appendStreamingCursor(sanitized) : sanitized;
  }, [content, isAssistant, isStreaming]);
  const messageContentRef = useRef(null);
  useMermaidRender(messageContentRef, formattedContent);

  const formattedTime = useMemo(() => {
    if (!timestamp) return '';
    return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  }, [timestamp]);


  // Images belong to the message, even when their producing activity is hidden.
  const messageImages = useMemo(
    () => collectMessageImages({ role, content, streamEvents }),
    [content, role, streamEvents],
  );


  // Root-agent work phases, delegated-agent cards and answer rows are siblings
  // in one chain. Each WorkGroup owns only the tools following its reasoning.
  if (isAssistant) {
    const fullTimelineItems = withReasoningFallback(assistantTimeline, {
      reasoningText,
      reasoningSegments,
      reasoningTokenCount,
      reasoningTimestamp,
      messageId,
      isStreaming,
    });
    const turnStillRunning = workResponseStatus === 'running'
      || (workResponseStatus === 'unknown' && (isStreaming || workActive));
    const visibleTimelineItems = filterTimelineItems(fullTimelineItems, streamEventFilter);
    const rows = buildWorkGroups(visibleTimelineItems.filter((item) => (
      item.kind !== 'assistant_message' || item.error || (item.content || '').trim()
    )));
    if (turnStillRunning && rows.length === 0) {
      rows.push({ kind: 'work_group', id: `${messageId}-pending`, heading: null, items: [], startedAt: message.startedAt ?? timestamp });
    }
    const lastRowKind = rows.at(-1)?.kind;
    if (workResponseStatus === 'failed' && !['work_group', 'assistant_message'].includes(lastRowKind)) {
      rows.push({
        kind: 'assistant_message', id: `${messageId}-failed-status`,
        content: '', timestamp, isStreaming: false, responseStatus: 'failed',
        displayAgentName: message.displayAgentName || '',
        tokenCount: message.tokenCount, finalCallTokenCount: message.finalCallTokenCount,
        usageProvenance: message.usageProvenance,
      });
    }
    return (
      <div className="chat-entry-chain chat-entry-chain--connected">
        {rows.map((item, index) => {
          const isLast = index === rows.length - 1;
          if (item.kind === 'work_group') {
            return (
              <WorkGroup
                key={item.id}
                heading={item.heading}
                items={item.items}
                active={turnStillRunning && isLast}
                startedAt={index === 0 ? message.startedAt ?? item.startedAt : item.startedAt}
                endedAt={item.endedAt ?? rows[index + 1]?.timestamp ?? message.workEndedAt ?? message.responseTimestamp}
                terminal={isLast}
                responseStatus={isLast ? workResponseStatus : undefined}
                onRegenerate={regenerateMessage}
                summaryMarkers={summaryMarkers}
                summaryActivities={summaryActivities}
              />
            );
          }
          return (
            <TimelineItem
              key={timelineItemKey(item, index)}
              item={item}
              contextUsage={item.kind === 'assistant_message' ? message.contextUsage : undefined}
              itemIndex={index}
              onRegenerate={isLast ? regenerateMessage : undefined}
              summaryMarkers={summaryMarkers}
              summaryActivities={summaryActivities}
              showResponseStatus={isLast && !effectiveInterruptionReason}
              continuesAfter={!isLast || turnStillRunning || Boolean(effectiveInterruptionReason)}
            />
          );
        })}
        {messageImages.length > 0 && (
          <div className="chat-entry-chain__images">
            <MessageImageStrip images={messageImages} />
          </div>
        )}
        {effectiveInterruptionReason ? <InterruptedMarker reason={effectiveInterruptionReason} /> : null}
      </div>
    );
  }


  const messageEntry = (
    <article className={`message-wrapper ${role} msg msg--${role} entry entry--user entry--standalone`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className="entry__badge entry__badge--user" />
      </div>
      <div className="entry__main msg__entry-main">
        <div className="msg__head entry__head">
          <span className="msg__role">{isScheduled ? 'Scheduled task' : 'Message'}</span>
          {isScheduled && (
            <span className="scheduled-badge muted">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="12 6 12 12 16 14"></polyline>
              </svg>
              Scheduled
            </span>
          )}
          <span className="msg__head-trailing">
            <MessageActions
              content={content}
            />
            {formattedTime && <span className="msg__time entry__time">{formattedTime}</span>}
          </span>
        </div>
        <div className={`message ${role} ${isStreaming ? 'streaming' : ''} ${error ? 'error' : ''} ${isScheduled ? 'scheduled' : ''}`}>
          {formattedContent && (
            <div
              ref={messageContentRef}
              className="message-content"
              dangerouslySetInnerHTML={{ __html: formattedContent }}
            />
          )}
          {attachments.length > 0 && (
            <div className="message-attachments">
              {attachments.map((attachment, index) => {
                const sizeLabel = formatAttachmentSize(attachment.size_bytes);
                const { label: fileTypeLabel } = describeAttachmentFileType(attachment.mime_type, attachment.name);
                const tooltip = attachment.error
                  ? `${attachment.name} — ${attachment.error}`
                  : [attachment.name, sizeLabel].filter(Boolean).join(' — ');
                return (
                  <span
                    key={attachment.id || `${attachment.name}-${index}`}
                    className={`message-attachment-chip${attachment.error ? ' message-attachment-chip--error' : ''}`}
                    title={tooltip}
                  >
                    <AttachmentFileIcon mimeType={attachment.mime_type} filename={attachment.name} size={26} />
                    <span className="message-attachment-chip__body">
                      <span className="message-attachment-chip__name">{attachment.name}</span>
                      <span className="message-attachment-chip__meta">
                        {attachment.error ? attachment.error : [fileTypeLabel, sizeLabel].filter(Boolean).join(' · ')}
                      </span>
                    </span>
                  </span>
                );
              })}
            </div>
          )}
          {messageImages.length > 0 && (
            <MessageImageStrip images={messageImages} />
          )}
        </div>
      </div>
    </article>
  );
  return (
    <>
      {messageEntry}
    </>
  );
}


// Memoized so a streamed token only re-renders the streaming message, not the
// whole transcript. streamEventFilter is a stable useCallback in the parent,
// so the default shallow compare is safe.
const Message = memo(MessageView);

export { Message };

export default Message;
