/**
 * One tool call (see mergeToolCallItems in workGroupModel.js): its start and
 * result/error rows merged into a single entry. Collapsed, it shows the
 * agent's commentary for the call, else a summary of its input; expanded, an
 * input box and an output box.
 */

import { memo } from 'react';
import {
  describeStreamEvent,
  formatDuration,
  formatTimestamp,
  prettifyLabel,
  toolDetailsForEvent,
} from '../models/messageRenderUtils';
import { toolCallSummary } from '../models/workGroupModel';

function details(event) {
  return event ? toolDetailsForEvent(event, describeStreamEvent(event)) : null;
}

const ToolCallEntry = memo(function ToolCallEntry({ item }) {
  const start = item.start?.event || null;
  const end = item.end?.event || null;
  const metadata = (start || end)?.metadata || {};
  const toolName = typeof metadata.tool_name === 'string' && metadata.tool_name ? metadata.tool_name : 'tool';
  const status = !end ? 'running' : end.eventType === 'tool_error' ? 'error' : 'success';
  const input = details(start)?.command || '';
  const endDetails = details(end);
  const output = endDetails?.body || '';
  const outputLabel = endDetails?.summary || '';
  const summary = start ? toolCallSummary(toolName, start.metadata) : outputLabel;
  const durationMs = end?.metadata?.duration_ms;
  const duration = Number.isFinite(durationMs) ? formatDuration(durationMs) : '';
  const time = formatTimestamp(start?.timestamp ?? end?.timestamp);
  const badgeClass = status === 'error' ? 'entry__badge--err' : 'entry__badge--tool';

  return (
    <div className="chat-entry-chain--item">
      <article className={`message-stream-event chat-event-entry entry entry--chain-item tool-call-entry tool-call-entry--${status}`} role="listitem">
        <div className="entry__gutter" aria-hidden="true">
          <span className={`entry__badge ${badgeClass}${status === 'running' ? ' work-step__badge--running' : ''}`} />
        </div>
        <div className="entry__main">
          <div className="message-stream-event-header entry__head">
            <span className="entry__role">{prettifyLabel(toolName)}</span>
            <span className="msg__head-trailing">
              {duration && <span className="muted">{duration}</span>}
              {time && <span className="msg__time entry__time">{time}</span>}
            </span>
          </div>
          {input || output ? (
            <details className="tool-event-disclosure">
              <summary>
                <span className="tool__summary tool-event-disclosure__summary">{summary}</span>
                <span className="tool-event-disclosure__chevron" aria-hidden="true" />
              </summary>
              {input && (
                <div className="tool chat-event-tool tool-call-entry__input">
                  <div className="tool__cmd"><span className="prompt">$</span>{input}</div>
                </div>
              )}
              {(output || outputLabel) && (
                <div className={`tool chat-event-tool tool-call-entry__output${status === 'error' ? ' tool-call-entry__output--error' : ''}`}>
                  {outputLabel && outputLabel !== output && <div className="tool-call-entry__label">{outputLabel}</div>}
                  {output && <pre className="tool__body tool__body--out">{output}</pre>}
                </div>
              )}
            </details>
          ) : summary && <span className="tool__summary">{summary}</span>}
        </div>
      </article>
    </div>
  );
});

export default ToolCallEntry;
