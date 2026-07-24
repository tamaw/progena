import { Fragment, memo, useMemo } from 'react';
import { collectMessageImages } from './conversationModel';
import { formatDisplayToolCalls } from './messageDisplayModel';
import { hasReasoningContent, isReasoningEvent, isRenderableStreamEvent } from './streamEventsModel';
import TimelineItem from './TimelineItem';
import MessageImageStrip from './MessageImageStrip';
import AttachmentFileIcon from './AttachmentFileIcon';
import { describeAttachmentFileType, formatAttachmentSize } from './attachmentFileTypes';
import NormalToolContent from './NormalToolContent';
import MessageActions from './MessageActions';
import {
  describeStreamEvent,
  badgeForEvent,
  toolDetailsForEvent,
  tokenMetaItemsForEvent,
  tagClassForEventType,
  renderSafeMarkdown,
  sanitizeHtmlFragment,
  appendStreamingCursor,
  formatTimestamp,
} from './messageRenderUtils';

function isTimelineStreamEvent(event) {
  return isRenderableStreamEvent(event);
}

function buildSyntheticReasoningEvent(
  reasoningText,
  reasoningTokenCount,
  reasoningTimestamp,
  messageId,
) {
  const trimmed = reasoningText.trim();
  return {
    id: `${messageId}-reasoning`,
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

// Backend-loaded (non-live) assistant messages carry reasoningText as a plain
// field rather than a reasoning stream_event in their timelineItems -- see
// chatSessionsModel.js's buildDefaultAssistantTimeline, which never synthesizes
// one. Inject a synthetic reasoning row here, ahead of the assistant_message
// row, so history reloads don't silently lose the reasoning trace that live
// streaming shows via liveAssistantTurnModel.js.
function withReasoningFallback(
  timelineItems,
  reasoningText,
  reasoningTokenCount,
  reasoningTimestamp,
  messageId,
  isStreaming = false,
) {
  const hasReasoningEntry = timelineItems.some((item) =>
    item.kind === 'stream_event' && isReasoningEvent(item.event) && hasReasoningContent(item.event)
  );
  const fallbackReasoningText = typeof reasoningText === 'string' ? reasoningText.trim() : '';
  if (isStreaming || hasReasoningEntry || !fallbackReasoningText) {
    return timelineItems;
  }
  const event = buildSyntheticReasoningEvent(
    reasoningText,
    reasoningTokenCount,
    reasoningTimestamp,
    messageId,
  );
  const reasoningItem = { kind: 'stream_event', id: event.id, event, timestamp: reasoningTimestamp };
  const assistantIndex = timelineItems.findIndex((item) => item.kind === 'assistant_message');
  if (assistantIndex < 0) return [reasoningItem, ...timelineItems];

  const reasoningTime = Date.parse(reasoningTimestamp || '');
  if (Number.isFinite(reasoningTime)) {
    const chronologicalIndex = timelineItems.findIndex((item) => {
      const timestamp = item.timestamp ?? item.event?.timestamp;
      const itemTime = typeof timestamp === 'number' ? timestamp : Date.parse(timestamp || '');
      return Number.isFinite(itemTime) && itemTime >= reasoningTime;
    });
    if (chronologicalIndex >= 0) {
      return [
        ...timelineItems.slice(0, chronologicalIndex),
        reasoningItem,
        ...timelineItems.slice(chronologicalIndex),
      ];
    }
  }

  return [
    ...timelineItems.slice(0, assistantIndex),
    reasoningItem,
    ...timelineItems.slice(assistantIndex),
  ];
}

function MessageView({
  message,
  streamEventFilter,
  hideContent = false,
  showImages = true,
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
    model = '',
    displayAgentName = '',
    displayModelName = '',
    displayToolCalls = [],
    timelineItems,
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
    if (typeof streamEventFilter !== 'function') return timelineEvents;
    return timelineEvents.filter(streamEventFilter);
  }, [hasTimelineItems, isStreaming, messageId, reasoningText, reasoningTimestamp, reasoningTokenCount, streamEvents, streamEventFilter]);

  const formattedContent = useMemo(() => {
    if (!content || hasTimelineItems) return '';
    const sanitized = renderSafeMarkdown(content);
    return isStreaming ? appendStreamingCursor(sanitized) : sanitized;
  }, [content, hasTimelineItems, isStreaming]);

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
  const displayName = role === 'assistant' && displayAgentName ? displayAgentName : 'Progenitor';
  const displayModel = role === 'assistant' && displayModelName ? displayModelName : model;
  const displayTools = useMemo(
    () => (role === 'assistant' ? formatDisplayToolCalls(displayToolCalls) : ''),
    [displayToolCalls, role],
  );
  const hasConnectedEvents = role === 'assistant' && visibleStreamEvents.length > 0;
  const entryStateClass = hasConnectedEvents ? 'entry--chain-end' : 'entry--standalone';

  // Tool-result (base64) images and markdown images from the message text are both
  // image responses in the same sense, so they're collected together via the shared
  // collectMessageImages() -- see its doc comment for why this must stay one function
  // rather than duplicated logic here and in the Images filter count.
  const messageImages = useMemo(
    () => (showImages && !hideContent ? collectMessageImages({ role, content, streamEvents }) : []),
    [content, hideContent, role, showImages, streamEvents],
  );

  const messageEntry = (
    <article className={`message-wrapper ${role} msg msg--${role} entry entry--${role === 'assistant' ? 'assistant' : 'user'} ${entryStateClass}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge entry__badge--${role === 'assistant' ? 'assistant' : 'user'}`}>
          {role === 'assistant' ? 'P' : 'U'}
        </span>
      </div>
      <div className="entry__main msg__entry-main">
        <div className="msg__head entry__head">
          {role === 'assistant' ? (
            <>
              <span className="entry__tag entry__tag--assistant">agent</span>
              <span className="msg__role">{displayName}</span>
            </>
          ) : (
            <>
              <span className="entry__tag entry__tag--in">user</span>
              <span className="msg__role">Message</span>
            </>
          )}
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
          {formattedContent && <div className="message-content" dangerouslySetInnerHTML={{ __html: formattedContent }} />}
          {!hideContent && attachments.length > 0 && (
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
                    <AttachmentFileIcon mimeType={attachment.mime_type} filename={attachment.name} />
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
          {showImages && !hideContent && messageImages.length > 0 && (
            <MessageImageStrip images={messageImages} />
          )}
        </div>
      </div>
    </article>
  );

  // If the message has explicit timelineItems, render them in array order.
  if (hasTimelineItems) {
    const fullTimelineItems = withReasoningFallback(
      timelineItems,
      reasoningText,
      reasoningTokenCount,
      reasoningTimestamp,
      messageId,
      isStreaming,
    );
    const visibleTimelineItems = fullTimelineItems.filter((item) => {
      if (item.kind === 'stream_event') {
        return typeof streamEventFilter !== 'function' || streamEventFilter(item.event);
      }
      if (item.kind === 'assistant_message') {
        return !hideContent;
      }
      if (item.kind === 'subagent') {
        // Subagent cards aren't a single event, so gate on the same
        // "subagents" category the underlying subagent_* events belong to
        // (see categoryForEventType in streamFilters.js) rather than a
        // per-event check.
        return typeof streamEventFilter !== 'function' || streamEventFilter({ eventType: 'subagent_start' });
      }
      if (item.kind === 'a2a_call') {
        // Same reasoning as 'subagent' above -- gate on the dedicated "a2a"
        // category (see categoryForEventType in streamFilters.js). call_agent
        // has no dedicated start event of its own (Progenitor's dispatch of
        // it is a plain tool_start, tagged via metadata.tool_name, same as
        // any other tool -- see applyA2ACallEvent in liveAssistantTurnModel.js),
        // so the synthetic sentinel event needs that metadata too or it would
        // fall into the generic 'tools' category instead of 'a2a'. The
        // card's own tool_start/tool_result rows also render their own plain
        // stream_event rows (see TimelineItem.jsx) gated independently by
        // the same filter via their own real metadata, so this only needs
        // to gate the card itself.
        return typeof streamEventFilter !== 'function'
          || streamEventFilter({ eventType: 'tool_start', metadata: { tool_name: 'call_agent' } });
      }
      return true;
    });
    return (
      <div className="chat-entry-chain chat-entry-chain--connected">
        {visibleTimelineItems.map((item, idx) => (
          <TimelineItem
            key={item.kind === 'stream_event' && isReasoningEvent(item.event)
              ? `${item.id}-${item.event.eventType}`
              : (item.id || `tl-${idx}`)}
            item={item}
            itemIndex={idx}
            onRegenerate={onRegenerate}
          />
        ))}
        {showImages && !hideContent && messageImages.length > 0 && (
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
        {!hideContent && messageEntry}
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
        const badge = badgeForEvent(event.eventType || 'stream_event');
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
              <span className={badge.className}>{badge.label}</span>
            </div>
            <div className="entry__main">
              <div className="message-stream-event-header entry__head">
                <span className={tagClassForEventType(event.eventType || 'stream_event')}>{description.badge}</span>
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
                    <span className="tool-event-disclosure__state tool-event-disclosure__state--expand tool-event-disclosure__label">Expand</span>
                    <span className="tool-event-disclosure__state tool-event-disclosure__state--collapse tool-event-disclosure__label">Collapse</span>
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
// whole transcript. streamEventFilter is a stable useCallback in the parent and
// hideContent/showImages are booleans, so the default shallow compare is safe.
const Message = memo(MessageView);

export { Message };

export default Message;
