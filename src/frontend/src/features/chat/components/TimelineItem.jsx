/**
 * Renders a single item from a message's `timelineItems` array.
 *
 * Each item is either:
 *   - `{ kind: 'assistant_message', ... }` — the agent's text response row
 *   - `{ kind: 'stream_event', event: ... }` — a tool/reasoning/node event
 *     row. An A2A tool's own tool_start/tool_result/tool_error render here
 *     too, completely unmodified. The intermediate a2a_call_progress updates
 *     are consumed into the richer card below instead of rendering their own
 *     row (see applyAssistantStreamEvent in liveAssistantTurnModel.js).
 *   - `{ kind: 'subagent', ... }` — a nested `task` tool delegation card
 *   - `{ kind: 'async_subagent', ... }` — a tracked background delegation
 *   - `{ kind: 'a2a_call', ... }` — the remote agent's own submitted/
 *     working/completed self-reporting for an A2A tool invocation, alongside
 *     (not instead of) its plain lifecycle rows above
 */

import { Fragment, memo } from 'react';
import { useSelector } from 'react-redux';
import { sanitizeHtmlFragment } from '../../../lib/safeMarkdown';
import { describeStreamEvent, badgeClassForEvent, toolDetailsForEvent, todosDetailsForEvent, tokenMetaItemsForEvent, formatTimestamp } from '../models/messageRenderUtils';
import TodoListPanel from './TodoListPanel';
import SubagentCard from './SubagentCard';
import A2ACallCard from './A2ACallCard';
import NormalToolContent from './NormalToolContent';
import AssistantMessageEntry from './AssistantMessageEntry';
import { selectAgentName } from '../../settings/state/preferences';
import ToolCallEntry from './ToolCallEntry';

const TimelineItem = memo(function TimelineItem({ item, itemIndex, onRegenerate, summaryMarkers, summaryActivities, showResponseStatus, continuesAfterInterruption = false }) {
  if (item.kind === 'tool_call') {
    return <ToolCallEntry item={item} />;
  }
  return <TimelineRow
    item={item}
    itemIndex={itemIndex}
    onRegenerate={onRegenerate}
    summaryMarkers={summaryMarkers}
    summaryActivities={summaryActivities}
    showResponseStatus={showResponseStatus}
    continuesAfterInterruption={continuesAfterInterruption}
  />;
});

// Separate component so the tool_call branch above stays hook-free; this
// one reads the agent name from the store.
function TimelineRow({ item, itemIndex, onRegenerate, summaryMarkers, summaryActivities, showResponseStatus, continuesAfterInterruption }) {
  const agentName = useSelector(selectAgentName);
  if (item.kind === 'subagent' || item.kind === 'async_subagent') {
    const matchingMarkers = (summaryMarkers || []).filter((marker) => (
      marker.scope?.kind === 'subagent' && marker.scope.id === item.toolCallId
    ));
    const matchingActivities = (summaryActivities || []).filter((activity) => (
      activity.scope?.kind === 'subagent' && activity.scope.id === item.toolCallId
    ));
    return <SubagentCard
      item={item}
      summaryMarkers={matchingMarkers}
      summaryActivities={matchingActivities}
    />;
  }

  if (item.kind === 'a2a_call') {
    return <A2ACallCard item={item} />;
  }

  if (item.kind === 'assistant_message') {
    return <AssistantMessageEntry item={item} agentName={agentName} onRegenerate={onRegenerate} showResponseStatus={showResponseStatus} continuesAfterInterruption={continuesAfterInterruption} />;
  }

  if (item.kind === 'stream_event') {
    const event = item.event;
    const description = describeStreamEvent(event);
    const badgeClassName = badgeClassForEvent(event.eventType || 'stream_event');
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
            <span className={badgeClassName} />
          </div>
          <div className="entry__main">
            <div className="message-stream-event-header entry__head">
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
}

export default TimelineItem;
