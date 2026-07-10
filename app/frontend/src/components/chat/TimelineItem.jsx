/**
 * Renders a single item from a message's `timelineItems` array.
 *
 * Each item is either:
 *   - `{ kind: 'assistant_message', ... }` — the agent's text response row
 *   - `{ kind: 'stream_event', event: ... }` — a tool/reasoning/node event row
 */

import { Fragment } from 'react';
import { formatDisplayToolCalls } from './messageDisplayModel';
import {
  formatMessage,
  sanitizeHtmlFragment,
  appendStreamingCursor,
  describeStreamEvent,
  badgeForEvent,
  toolDetailsForEvent,
  tokenMetaItemsForEvent,
  tagClassForEventType,
} from './messageRenderUtils';

export default function TimelineItem({ item, itemIndex }) {
  if (item.kind === 'assistant_message') {
    const sanitizedContent = sanitizeHtmlFragment(formatMessage(item.content || ''));
    const formattedContent = item.isStreaming && item.content
      ? appendStreamingCursor(sanitizedContent)
      : sanitizedContent;
    const formattedTime = item.timestamp
      ? new Date(item.timestamp).toLocaleTimeString([], {
          hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
        })
      : '';
    const tokenMeta = [
      Number.isFinite(item.tokenCount) && item.tokenCount > 0
        ? `${new Intl.NumberFormat().format(item.tokenCount)} token${item.tokenCount === 1 ? '' : 's'}`
        : '',
      Number.isFinite(item.reasoningTokenCount) && item.reasoningTokenCount > 0
        ? `${new Intl.NumberFormat().format(item.reasoningTokenCount)} reasoning token${item.reasoningTokenCount === 1 ? '' : 's'}`
        : '',
    ].filter(Boolean);

    return (
      <div key={item.id || `tl-${itemIndex}`} className="chat-entry-chain--item">
        <article className={`message-wrapper assistant msg msg--assistant entry entry--assistant ${item.error ? 'entry--standalone' : 'entry--chain-end'}`}>
          <div className="entry__gutter" aria-hidden="true">
            <span className="entry__badge entry__badge--assistant">P</span>
          </div>
          <div className="entry__main msg__entry-main">
            <div className="msg__head entry__head">
              <span className="entry__tag entry__tag--assistant">agent</span>
              <span className="msg__role">{item.displayAgentName || 'Progenitor'}</span>
              <span className="msg__head-trailing">
                {tokenMeta.map((meta, mi) => (
                  <Fragment key={`${meta}-${mi}`}>
                    {mi > 0 && <span className="message-meta-separator dot-sep" aria-hidden="true" />}
                    <span className="msg__stat message-token-count">{meta}</span>
                  </Fragment>
                ))}
                {item.displayModelName && <span className="msg__model">{item.displayModelName}</span>}
                {Array.isArray(item.displayToolCalls) && item.displayToolCalls.length > 0 && (
                  <span className="msg__model">{formatDisplayToolCalls(item.displayToolCalls)}</span>
                )}
                {formattedTime && <span className="msg__time entry__time">{formattedTime}</span>}
              </span>
            </div>
            <div className={`message assistant ${item.isStreaming ? 'streaming' : ''} ${item.error ? 'error' : ''}`}>
              <div className="message-content" dangerouslySetInnerHTML={{ __html: formattedContent }} />
            </div>
          </div>
        </article>
      </div>
    );
  }

  if (item.kind === 'stream_event') {
    const event = item.event;
    const description = describeStreamEvent(event);
    const badge = badgeForEvent(event.eventType || 'stream_event');
    const tool = toolDetailsForEvent(event, description);
    const eventTokenMetaItems = tokenMetaItemsForEvent(event);

    return (
      <div key={event.id || `tl-${itemIndex}`} className="chat-entry-chain--item">
        <article
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
                {description.metaDetail && !tool && <span className="muted">{description.metaDetail}</span>}
                {eventTokenMetaItems.map((meta, mi) => (
                  <Fragment key={`${event.id}-${meta}-${mi}`}>
                    {mi > 0 && <span className="message-meta-separator dot-sep" aria-hidden="true" />}
                    <span className="msg__stat message-token-count">{meta}</span>
                  </Fragment>
                ))}
              </span>
            </div>
            {description.bodyHtml && (
              <div
                className="message-stream-event-body entry__content"
                dangerouslySetInnerHTML={{ __html: sanitizeHtmlFragment(description.bodyHtml) }}
              />
            )}
            {description.bodyDetail && !tool && !description.bodyHtml && (
              <div className="message-stream-event-detail entry__content">{description.bodyDetail}</div>
            )}
            {tool && (
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
      </div>
    );
  }

  return null;
}
