/**
 * Renders a `{ kind: 'subagent', ... }` timeline item -- one card per `task`
 * tool delegation, live-updated as the subagent's own reply streams and its
 * own tool calls start/finish. Multiple subagents can be running
 * concurrently within one turn; each card is independent (keyed by the
 * dispatching tool_call_id in liveAssistantTurnModel.js) and needs no
 * special handling here beyond rendering whatever `item` it's given.
 */

import { useState } from 'react';
import {
  formatMessage,
  sanitizeHtmlFragment,
  appendStreamingCursor,
  formatArgsPreview,
  formatDuration,
  formatTimestamp,
} from './messageRenderUtils';

const STATUS_GLYPH = {
  running: '◐',
  success: '✓',
  error: '✗',
};

const AUTO_COLLAPSE_CHILD_THRESHOLD = 5;

function SubagentChild({ child }) {
  const args = formatArgsPreview(child.argsPreview);
  const statusLabel = child.status === 'running' ? 'running' : child.status === 'error' ? 'error' : 'done';
  const statusClass = child.status === 'error' ? 'tool__status--err' : child.status === 'success' ? 'tool__status--ok' : '';
  const body = [
    Number.isFinite(child.durationMs) ? formatDuration(child.durationMs) : '',
    child.error || child.resultPreview,
  ].filter(Boolean).join('\n');

  return (
    <div className="tool">
      <div className="tool__head">
        <span className="tool__kind">{child.toolName || 'tool'}</span>
        <span className="tool__desc">{args}</span>
        <span className={`tool__status ${statusClass}`}>{statusLabel}</span>
      </div>
      {body && <pre className="tool__body">{body}</pre>}
    </div>
  );
}

export default function SubagentCard({ item }) {
  const [expanded, setExpanded] = useState(true);
  const [prevStatus, setPrevStatus] = useState(item.status);
  const [userToggled, setUserToggled] = useState(false);

  // Default expanded while running so live progress is visible; once a
  // subagent finishes with a lot of tool calls, collapse it automatically to
  // keep the transcript scannable -- unless the user already toggled it
  // manually during the run, in which case their choice wins. Adjusted
  // directly during render (comparing against the previous render's status,
  // React's documented pattern for this -- see "storing information from
  // previous renders" in the useState docs) rather than in a useEffect,
  // which would cause an extra cascading render.
  if (item.status !== prevStatus) {
    const wasRunning = prevStatus === 'running';
    setPrevStatus(item.status);
    if (
      wasRunning
      && item.status !== 'running'
      && !userToggled
      && item.children.length > AUTO_COLLAPSE_CHILD_THRESHOLD
    ) {
      setExpanded(false);
    }
  }

  const handleToggle = () => {
    setUserToggled(true);
    setExpanded((prev) => !prev);
  };

  // `content` (accumulated subagent_message_delta chunks) is empty for cards
  // reconstructed from persisted history on reload -- deltas are never
  // durably stored, only the final subagent_start/subagent_result pair
  // (see buildSubagentDispatchStreamEvents in chatSessionsModel.js) -- so
  // fall back to the result's own preview text rather than rendering blank.
  const displayText = item.content || item.resultPreview || '';
  const bodyHtml = displayText ? sanitizeHtmlFragment(formatMessage(displayText)) : '';
  const formattedBody = item.status === 'running' && item.content ? appendStreamingCursor(bodyHtml) : bodyHtml;
  const durationLabel = Number.isFinite(item.durationMs) ? formatDuration(item.durationMs) : '';
  const formattedTime = formatTimestamp(item.finishedAt || item.startedAt);
  const childCount = item.children.length;

  return (
    <div className={`entry entry--chain-item subagent-card subagent-card--${item.status}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge subagent-card__badge subagent-card__badge--${item.status}`}>
          {STATUS_GLYPH[item.status] || STATUS_GLYPH.running}
        </span>
      </div>
      <div className="entry__main">
        <div className="entry__head">
          <span className="entry__tag subagent-card__tag">Subagent</span>
          <span className="entry__role">{item.subagentName || 'subagent'}</span>
          <span className="msg__head-trailing">
            {childCount > 0 && (
              <span className="muted">{childCount} tool call{childCount === 1 ? '' : 's'}</span>
            )}
            {durationLabel && <span className="muted">{durationLabel}</span>}
            <button
              type="button"
              className="msg-action-btn subagent-card__toggle"
              onClick={handleToggle}
              aria-expanded={expanded}
            >
              {expanded ? 'Collapse' : 'Expand'}
            </button>
            {formattedTime && <span className="msg__time entry__time">{formattedTime}</span>}
          </span>
        </div>
        {expanded && (
          <div className="subagent-card__body entry__content">
            {formattedBody && (
              <div
                className="message-stream-event-body"
                dangerouslySetInnerHTML={{ __html: formattedBody }}
              />
            )}
            {item.status === 'error' && item.error && (
              <div className="subagent-card__error">{item.error}</div>
            )}
            {childCount > 0 && (
              <div className="subagent-card__children">
                {item.children.map((child) => (
                  <SubagentChild key={child.id} child={child} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
