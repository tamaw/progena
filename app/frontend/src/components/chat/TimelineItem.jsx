/**
 * Renders a single item from a message's `timelineItems` array.
 *
 * Each item is either:
 *   - `{ kind: 'assistant_message', ... }` — the agent's text response row
 *   - `{ kind: 'stream_event', event: ... }` — a tool/reasoning/node event
 *     row. call_agent's own tool_start/tool_result/tool_error render here
 *     too, completely unmodified (Progenitor's act of dispatching/receiving
 *     an A2A call is an ordinary tool call and reads as one, same as
 *     search_web or read_file). Only the intermediate a2a_call_progress
 *     updates are consumed into the richer card below instead of rendering
 *     their own row (see applyAssistantStreamEvent in
 *     liveAssistantTurnModel.js) -- a plain tool call has no equivalent of
 *     an in-flight progress stream.
 *   - `{ kind: 'subagent', ... }` — a nested `task` tool delegation card
 *   - `{ kind: 'a2a_call', ... }` — the remote agent's own submitted/
 *     working/completed self-reporting for a call_agent invocation,
 *     alongside (not instead of) its plain tool_start/tool_result rows above
 */

import { Fragment, memo } from 'react';
import { formatDisplayToolCalls } from './messageDisplayModel';
import {
  formatMessage,
  sanitizeHtmlFragment,
  appendStreamingCursor,
  describeStreamEvent,
  badgeForEvent,
  toolDetailsForEvent,
  todosDetailsForEvent,
  tokenMetaItemsForEvent,
  tagClassForEventType,
  formatTimestamp,
} from './messageRenderUtils';
import TodoListPanel from './TodoListPanel';
import SubagentCard from './SubagentCard';
import A2ACallCard from './A2ACallCard';
import NormalToolContent from './NormalToolContent';
import MessageActions from './MessageActions';



const TimelineItem = memo(function TimelineItem({ item, itemIndex, onRegenerate, regenerateDisabled = false }) {
  if (item.kind === 'subagent') {
    return <SubagentCard item={item} />;
  }

  if (item.kind === 'a2a_call') {
    return <A2ACallCard item={item} />;
  }

  if (item.kind === 'assistant_message') {
    const sanitizedContent = sanitizeHtmlFragment(formatMessage(item.content || ''));
    const formattedContent = item.isStreaming && item.content
      ? appendStreamingCursor(sanitizedContent)
      : sanitizedContent;
    const formattedTime = formatTimestamp(item.timestamp);
    const tokenMeta = [
      Number.isFinite(item.tokenCount) && item.tokenCount > 0
        ? `${new Intl.NumberFormat().format(item.tokenCount)} token${item.tokenCount === 1 ? '' : 's'}`
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
                <MessageActions
                  content={item.content || ''}
                  onRegenerate={onRegenerate}
                  regenerateDisabled={regenerateDisabled}
                />
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
    const todos = todosDetailsForEvent(event);
    const eventTokenMetaItems = tokenMetaItemsForEvent(event);
    const eventFormattedTime = formatTimestamp(event.timestamp);

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
                {tool?.isNormal
                  ? (tool.duration && <span className="muted">{tool.duration}</span>)
                  : (description.metaDetail && <span className="muted">{description.metaDetail}</span>)}
                {eventTokenMetaItems.map((meta, mi) => (
                  <Fragment key={`${event.id}-${meta}-${mi}`}>
                    {mi > 0 && <span className="message-meta-separator dot-sep" aria-hidden="true" />}
                    <span className="msg__stat message-token-count">{meta}</span>
                  </Fragment>
                ))}
                {eventFormattedTime && <span className="msg__time entry__time">{eventFormattedTime}</span>}
              </span>
            </div>
            {description.bodyHtml && (description.reasoningDetailHtml ? (
              <details className="tool-event-disclosure reasoning-event-disclosure">
                <summary>
                  <div
                    className="message-content reasoning-event-disclosure__summary"
                    dangerouslySetInnerHTML={{ __html: sanitizeHtmlFragment(description.bodyHtml) }}
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
            ) : (
              <div
                className="message-content reasoning-event-content entry__content"
                dangerouslySetInnerHTML={{ __html: sanitizeHtmlFragment(description.bodyHtml) }}
              />
            ))}
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
            {todos && todos.length > 0 && (
              <div className="entry__content">
                <TodoListPanel todos={todos} />
              </div>
            )}
          </div>
        </article>
      </div>
    );
  }

  return null;
});

export default TimelineItem;
