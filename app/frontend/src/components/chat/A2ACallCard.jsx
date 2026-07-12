/**
 * Renders a `{ kind: 'a2a_call', ... }` timeline item -- one card per
 * blocking `call_agent` invocation of a deployed A2A agent, live-updated as
 * intermediate status updates stream in. Structurally mirrors SubagentCard
 * (same entry/gutter/main layout, same fixed-height auto-scrolling body,
 * same nested .tool block styling per update), except each entry is keyed by
 * the A2A task's self-reported status (submitted/working/completed/...)
 * rather than a tool name -- A2A only exposes the remote agent's status
 * text, never its own internal tool calls.
 */

import {
  formatDuration,
  formatTimestamp,
  extractCallAgentSummary,
  statusPillProps,
  a2aStatePillProps,
} from './messageRenderUtils';
import useStickyScroll from './useStickyScroll';
import StatusPill from './StatusPill';

// Distinguishes the A2A card's badge from SubagentCard's at a glance -- a
// link between two endpoints, standing in for "agent-to-agent connection".
function A2AGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 7h3a5 5 0 0 1 5 5 5 5 0 0 1-5 5h-3" />
      <path d="M9 17H6a5 5 0 0 1-5-5 5 5 0 0 1 5-5h3" />
      <line x1="8" y1="12" x2="16" y2="12" />
    </svg>
  );
}

// Reuses the generic .tool/.tool__* block SubagentChild renders each nested
// tool call with (chrome-frame.css), rather than A2A-specific styling, so a
// call's progress log reads as the same visual language as a subagent's
// tool-call list -- kind/status up top, content below -- instead of two
// different card families looking unrelated for no real reason. `state`
// (submitted/working/completed/failed) stands in for the tool name slot;
// there's no args-equivalent for a status update, so .tool__desc is just a
// spacer (it's what pushes the status badge to the right in the original).
function A2ACallUpdate({ message }) {
  return (
    <div className="tool">
      <div className="tool__head">
        <span className="tool__kind">{message.state || 'update'}</span>
        <span className="tool__desc" />
        <StatusPill {...a2aStatePillProps(message.state)} />
      </div>
      {message.text && <pre className="tool__body">{message.text}</pre>}
    </div>
  );
}

export default function A2ACallCard({ item }) {
  const durationLabel = Number.isFinite(item.durationMs) ? formatDuration(item.durationMs) : '';
  const formattedTime = formatTimestamp(item.finishedAt || item.startedAt);
  const messageCount = item.messages.length;
  // resultPreview is the whole call_agent result dict, stringified -- only
  // shown when there's no message log to tell the story instead (e.g.
  // blocking=false, or an agent that doesn't stream), and even then prefer
  // just the human-readable summary over the raw JSON when it's extractable.
  const displayedResult = extractCallAgentSummary(item.resultPreview) || item.resultPreview;
  const { ref: scrollRef, onScroll } = useStickyScroll(`${messageCount}:${item.status}`);

  return (
    <div className={`entry entry--chain-item a2a-call-card a2a-call-card--${item.status}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge a2a-call-card__badge a2a-call-card__badge--${item.status}`}>
          <A2AGlyph />
        </span>
      </div>
      <div className="entry__main">
        <div className="entry__head">
          <span className="entry__tag a2a-call-card__tag">A2A</span>
          <span className="entry__role">{item.agentName || 'agent'}</span>
          <StatusPill {...statusPillProps(item.status)} />
          <span className="msg__head-trailing">
            {messageCount > 0 && (
              <span className="muted">{messageCount} update{messageCount === 1 ? '' : 's'}</span>
            )}
            {durationLabel && <span className="muted">{durationLabel}</span>}
            {formattedTime && <span className="msg__time entry__time">{formattedTime}</span>}
          </span>
        </div>
        <div className="a2a-call-card__body entry__content" ref={scrollRef} onScroll={onScroll}>
          {messageCount > 0 && (
            <div className="a2a-call-card__messages">
              {item.messages.map((message) => (
                <A2ACallUpdate key={message.id} message={message} />
              ))}
            </div>
          )}
          {item.status === 'error' && item.error && (
            <div className="a2a-call-card__error">{item.error}</div>
          )}
          {/* resultPreview is a generic, unstructured stringified dump of
              the whole call_agent result (task id, context id, raw
              history, ...) -- shown only as a fallback when there are no
              per-step messages to tell the story instead (e.g. a
              non-streaming agent, or blocking=false), matching
              SubagentCard's content-or-resultPreview fallback. With
              messages present, showing both would just repeat the same
              narrative twice, once readable and once as raw JSON. */}
          {item.status === 'success' && messageCount === 0 && displayedResult && (
            <div className="a2a-call-card__result">{displayedResult}</div>
          )}
        </div>
      </div>
    </div>
  );
}
