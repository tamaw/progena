import { Fragment, memo, useMemo, useRef } from 'react';
import { useSelector } from 'react-redux';
import { useMermaidRender } from '../../../hooks/useMermaidRender';
import { collectMessageImages } from '../models/conversationModel';
import { formatDisplayToolCalls } from '../models/messageDisplayModel';
import { hasReasoningContent, isReasoningEvent, isRenderableStreamEvent } from '../models/streamEventsModel';
import TimelineItem from './TimelineItem';
import WorkGroup from './WorkGroup';
import { buildWorkGroups, partitionTurnTimeline, timelineItemKey } from '../models/workGroupModel';
import MessageImageStrip from './MessageImageStrip';
import AttachmentFileIcon from './AttachmentFileIcon';
import { describeAttachmentFileType, formatAttachmentSize } from '../models/attachmentFileTypes';
import NormalToolContent from './NormalToolContent';
import MessageActions from './MessageActions';
import { describeStreamEvent, badgeClassForEvent, toolDetailsForEvent, tokenMetaItemsForEvent, appendStreamingCursor, formatTimestamp } from '../models/messageRenderUtils';
import { renderSafeMarkdown, sanitizeHtmlFragment } from '../../../lib/safeMarkdown';
import { filterStreamEvents, filterTimelineItems } from '../models/streamFilters';
import { selectAgentName } from '../../settings/state/preferences';

function isTimelineStreamEvent(event) {
  return isRenderableStreamEvent(event);
}

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

function buildTimelineStreamEvents(
  streamEvents,
  reasoningText,
  reasoningTokenCount,
  reasoningTimestamp,
  messageId,
  isStreaming = false,
) {
  const timelineEvents = streamEvents.filter((event) => isTimelineStreamEvent(event));
  const hasReasoningEntry = timelineEvents.some((event) =>
    isReasoningEvent(event) && hasReasoningContent(event)
  );
  const fallbackReasoningText = typeof reasoningText === 'string' ? reasoningText.trim() : '';

  if (isStreaming || hasReasoningEntry || !fallbackReasoningText) {
    return timelineEvents;
  }

  return [buildSyntheticReasoningEvent(reasoningText, reasoningTokenCount, reasoningTimestamp, messageId), ...timelineEvents];
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
    tokenCount = 0,
    reasoningTokenCount = 0,
    reasoningText = '',
    reasoningTimestamp = null,
    reasoningSegments = [],
    model = '',
    displayAgentName = '',
    displayModelName = '',
    displayToolCalls = [],
    timelineItems,
    workActive = false,
    attachments = [],
  } = message;

  const hasTimelineItems = role === 'assistant' && Array.isArray(timelineItems) && timelineItems.length > 0;
  const visibleStreamEvents = useMemo(() => {
    if (hasTimelineItems) return [];

    const timelineEvents = buildTimelineStreamEvents(
      streamEvents,
      reasoningText,
      reasoningTokenCount,
      reasoningTimestamp,
      messageId,
      isStreaming,
    );
    return filterStreamEvents(timelineEvents, streamEventFilter);
  }, [hasTimelineItems, isStreaming, messageId, reasoningText, reasoningTimestamp, reasoningTokenCount, streamEvents, streamEventFilter]);

  const formattedContent = useMemo(() => {
    if (!content || hasTimelineItems) return '';
    const sanitized = renderSafeMarkdown(content);
    return isStreaming ? appendStreamingCursor(sanitized) : sanitized;
  }, [content, hasTimelineItems, isStreaming]);
  const messageContentRef = useRef(null);
  useMermaidRender(messageContentRef, formattedContent);

  const formattedTime = useMemo(() => {
    if (!timestamp) return '';
    return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  }, [timestamp]);

  const formattedTokenCount = useMemo(() => {
    if (role !== 'assistant' || isStreaming || !Number.isFinite(tokenCount) || tokenCount <= 0) {
      return '';
    }
    const count = new Intl.NumberFormat().format(tokenCount);
    return `${count} ${tokenCount === 1 ? 'token' : 'tokens'}`;
  }, [isStreaming, role, tokenCount]);
  const tokenMetaItems = [formattedTokenCount].filter(Boolean);
  const agentName = useSelector(selectAgentName);
  const displayName = role === 'assistant' && displayAgentName ? displayAgentName : agentName;
  const displayModel = role === 'assistant' && displayModelName ? displayModelName : model;
  const displayTools = useMemo(
    () => (role === 'assistant' ? formatDisplayToolCalls(displayToolCalls) : ''),
    [displayToolCalls, role],
  );
  const hasConnectedEvents = role === 'assistant' && visibleStreamEvents.length > 0;
  const entryStateClass = hasConnectedEvents ? 'entry--chain-end' : 'entry--standalone';

  // Images belong to the message, even when their producing activity is hidden.
  const messageImages = useMemo(
    () => collectMessageImages({ role, content, streamEvents }),
    [content, role, streamEvents],
  );

  const messageEntry = (
    <article className={`message-wrapper ${role} msg msg--${role} entry entry--${role === 'assistant' ? 'assistant' : 'user'} ${entryStateClass}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge entry__badge--${role === 'assistant' ? 'assistant' : 'user'}${role === 'assistant' && isStreaming && !error ? ' activity-dot' : ''}`} />
      </div>
      <div className="entry__main msg__entry-main">
        <div className="msg__head entry__head">
          <span className="msg__role">{role === 'assistant' ? displayName : 'Message'}</span>
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
            {tokenMetaItems.map((item, index) => (
              <Fragment key={`${item}-${index}`}>
                {index > 0 && <span className="message-meta-separator dot-sep" aria-hidden="true" />}
                <span className="msg__stat message-token-count">{item}</span>
              </Fragment>
            ))}
            {displayModel && <span className="msg__model">{displayModel}</span>}
            {displayTools && <span className="msg__model">{displayTools}</span>}
            <MessageActions
              content={content}
              onRegenerate={role === 'assistant' ? onRegenerate : undefined}
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

  // Timeline turns render as: one collapsible work group (tools, reasoning,
  // plan, steps), then delegated-agent cards, then the answer.
  if (hasTimelineItems) {
    const fullTimelineItems = withReasoningFallback(timelineItems, {
      reasoningText,
      reasoningSegments,
      reasoningTokenCount,
      reasoningTimestamp,
      messageId,
      isStreaming,
    });
    // Visibility is a presentation choice, not a different end to the work.
    // Skip rows emptied by tool commentary, as the visible answer path does.
    const answerStartedAt = fullTimelineItems.find((item) =>
      item.kind === 'assistant_message' && (item.error || (item.content || '').trim())
    )?.timestamp ?? null;
    const visibleTimelineItems = filterTimelineItems(fullTimelineItems, streamEventFilter);
    const { workItems, cardItems, answerItems: allAnswerItems } = partitionTurnTimeline(visibleTimelineItems);
    // An answer row whose text all turned out to be tool commentary (see
    // claimToolCommentary in liveAssistantTurnModel.js) has nothing to show.
    const answerItems = allAnswerItems.filter((item) => item.error || (item.content || '').trim());
    const workGroups = buildWorkGroups(workItems);
    return (
      <div className="chat-entry-chain chat-entry-chain--connected">
        {workGroups.map((group, index) => {
          const isLatest = index === workGroups.length - 1;
          return (
            <WorkGroup
              key={group.id}
              heading={group.heading}
              items={group.items}
              active={workActive && isLatest}
              endedAt={isLatest ? answerStartedAt : workGroups[index + 1].startedAt}
              onRegenerate={onRegenerate}
            />
          );
        })}
        {[...cardItems, ...answerItems].map((item, idx) => (
          <TimelineItem
            key={timelineItemKey(item, idx)}
            item={item}
            itemIndex={idx}
            onRegenerate={onRegenerate}
          />
        ))}
        {messageImages.length > 0 && (
          <div className="chat-entry-chain__images">
            <MessageImageStrip images={messageImages} />
          </div>
        )}
      </div>
    );
  }

  // Fallback: no timelineItems — used by SSE-injected scheduled messages that
  // are pushed directly to the messages array without going through a normalizer.
  if (role === 'assistant') {
    return (
      <div className={`chat-entry-chain ${hasConnectedEvents ? 'chat-entry-chain--connected' : 'chat-entry-chain--single'}`}>
        {hasConnectedEvents && <StreamEvents events={visibleStreamEvents} />}
        {messageEntry}
      </div>
    );
  }

  return (
    <>
      {messageEntry}
    </>
  );
}

function StreamEvents({ events }) {
  return (
    <>
      {events.map((event) => {
        const description = describeStreamEvent(event);
        const isReasoning = isReasoningEvent(event);
        const badgeClassName = badgeClassForEvent(event.eventType || 'stream_event');
        const tool = toolDetailsForEvent(event, description);
        const tokenMetaItems = tokenMetaItemsForEvent(event);
        const eventFormattedTime = formatTimestamp(event.timestamp);
        return (
          <article
            key={event.id}
            className={`message-stream-event chat-event-entry entry entry--chain-item ${event.eventType || 'stream_event'}`}
            role="listitem"
          >
            <div className="entry__gutter" aria-hidden="true">
              <span className={badgeClassName} />
            </div>
            <div className="entry__main">
              <div className="message-stream-event-header entry__head">
                <span className="entry__role">{description.title}</span>
                <span className="msg__head-trailing">
                  {tool?.isNormal
                    ? (tool.duration && <span className="muted">{tool.duration}</span>)
                    : (description.metaDetail && <span className="muted">{description.metaDetail}</span>)}
                  {tokenMetaItems.map((item, index) => (
                    <Fragment key={`${event.id}-${item}-${index}`}>
                      {(index > 0 || tool?.duration) && <span className="message-meta-separator dot-sep" aria-hidden="true" />}
                      <span className="msg__stat message-token-count">{item}</span>
                    </Fragment>
                  ))}
                  {eventFormattedTime && <span className="msg__time entry__time">{eventFormattedTime}</span>}
                </span>
              </div>
              {isReasoning && description.reasoningDetailHtml ? (
                <details className="tool-event-disclosure reasoning-event-disclosure">
                  <summary>
                    <div
                      className="message-content reasoning-event-disclosure__summary"
                      dangerouslySetInnerHTML={{ __html: sanitizeHtmlFragment(description.bodyHtml || '') }}
                    />
                    <span className="tool-event-disclosure__chevron" aria-hidden="true" />
                  </summary>
                  <div className="reasoning-event-disclosure__detail">
                    <div
                      className="message-content"
                      dangerouslySetInnerHTML={{ __html: sanitizeHtmlFragment(description.reasoningDetailHtml) }}
                    />
                  </div>
                </details>
              ) : description.bodyHtml && (
                <div
                  className="message-stream-event-body entry__content"
                  dangerouslySetInnerHTML={{ __html: sanitizeHtmlFragment(description.bodyHtml) }}
                />
              )}
              {description.bodyDetail && !tool && !description.bodyHtml && (
                <div className="message-stream-event-detail entry__content">{description.bodyDetail}</div>
              )}
              {tool?.isNormal ? <NormalToolContent tool={tool} /> : tool && (
                <div className="tool chat-event-tool">
                  <div className="tool__head">
                    <span className="tool__kind">{tool.kind}</span>
                    <span className="tool__desc">{tool.desc}</span>
                  </div>
                  {tool.command && <div className="tool__cmd"><span className="prompt">$</span>{tool.command}</div>}
                  {tool.body && <pre className="tool__body tool__body--out">{tool.body}</pre>}
                </div>
              )}
            </div>
          </article>
        );
      })}
    </>
  );
}

// Memoized so a streamed token only re-renders the streaming message, not the
// whole transcript. streamEventFilter is a stable useCallback in the parent,
// so the default shallow compare is safe.
const Message = memo(MessageView);

export { Message };

export default Message;
