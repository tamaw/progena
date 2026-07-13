/**
 * Renders a `{ kind: 'subagent', ... }` timeline item -- one card per `task`
 * tool delegation, live-updated as the subagent's own reply streams and its
 * own tool calls start/finish. Multiple subagents can be running
 * concurrently within one turn; each card is independent (keyed by the
 * dispatching tool_call_id in liveAssistantTurnModel.js) and needs no
 * special handling here beyond rendering whatever `item` it's given.
 *
 * The body is a fixed-height pane (`.subagent-card__body`, chat-panel.css)
 * that scrolls and auto-tails its latest content (useStickyScroll) instead
 * of a manual collapse/expand toggle -- always visible, no click required,
 * and every concurrent card keeps the same small footprint on screen. The
 * header stays bare chrome, styled like any other top-level entry (see
 * A2ACallCard.jsx's matching comment history) -- the tinted "card" look
 * lives on the body.
 *
 * Each nested tool call (`item.children`) renders as its own mini `.entry`
 * node chained together (SubagentChild below) -- the same "mini card/event"
 * treatment A2ACallCard gives its own per-update sub-entries, reusing the
 * existing .tool block for content and the outer card's own running/error
 * badge modifiers for status color, rather than inventing a parallel set.
 */

import {
  formatMessage,
  sanitizeHtmlFragment,
  appendStreamingCursor,
  formatArgsPreview,
  formatDuration,
  formatTimestamp,
  statusPillProps,
} from './messageRenderUtils';
import useStickyScroll from './useStickyScroll';
import StatusPill from './StatusPill';
import { WorkingGlyph, CompletedGlyph, FailedGlyph } from './eventGlyphs';

// Distinguishes the subagent card's badge from A2ACallCard's at a glance --
// a branch spinning off the main line, standing in for "delegated sub-work".
function SubagentGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="6" y1="3" x2="6" y2="15" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path d="M18 9a9 9 0 0 1-9 9" />
    </svg>
  );
}

const CHILD_STATUS_GLYPHS = {
  running: WorkingGlyph,
  success: CompletedGlyph,
  error: FailedGlyph,
};

// Called directly as a plain function, not rendered as `<X />` -- see the
// matching comment on A2ACallCard.jsx's glyphForA2AState for why (these are
// pure markup with no hooks, but react-hooks/static-components can't verify
// a dynamically-selected component's stability statically).
function glyphForChildStatus(status) {
  const Glyph = CHILD_STATUS_GLYPHS[status] || WorkingGlyph;
  return Glyph();
}

// One nested tool call = one mini entry in the chain, the same "mini card/
// event" treatment A2ACallCard's A2ASubEntry gives each status update:
// its own badge (glyph + color keyed off child.status, reusing the exact
// same subagent-card__badge--running/--error modifiers the outer card's own
// badge already uses -- one status vocabulary, not a second one invented for
// children), its own connecting line down to the next call (`isLast` drops
// the trailing line for the chain's last item, same entry--chain-end vs
// entry--chain-item convention as everywhere else). The content itself is
// the existing .tool block, unchanged -- reused wholesale rather than
// inventing a new args/result layout, it just moves from a bare flex column
// (.subagent-card__children) into this gutter/badge shell.
function SubagentChild({ child, isLast }) {
  const args = formatArgsPreview(child.argsPreview);
  const body = [
    Number.isFinite(child.durationMs) ? formatDuration(child.durationMs) : '',
    child.error || child.resultPreview,
  ].filter(Boolean).join('\n');

  return (
    <div className={`entry subagent-child ${isLast ? 'entry--chain-end' : 'entry--chain-item'}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge subagent-card__badge subagent-card__badge--${child.status}`}>
          {glyphForChildStatus(child.status)}
        </span>
      </div>
      <div className="entry__main">
        <div className="tool">
          <div className="tool__head">
            <span className="tool__kind">{child.toolName || 'tool'}</span>
            <span className="tool__desc">{args}</span>
          </div>
          {body && <pre className="tool__body">{body}</pre>}
        </div>
      </div>
    </div>
  );
}

export default function SubagentCard({ item }) {
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
  const { ref: scrollRef, onScroll } = useStickyScroll(`${childCount}:${displayText.length}:${item.status}`);

  return (
    <div className={`entry entry--chain-item subagent-card subagent-card--${item.status}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge subagent-card__badge subagent-card__badge--${item.status}`}>
          <SubagentGlyph />
        </span>
      </div>
      <div className="entry__main">
        <div className="entry__head">
          <span className="entry__tag subagent-card__tag">Subagent</span>
          <span className="entry__role">{item.subagentName || 'subagent'}</span>
          <StatusPill {...statusPillProps(item.status)} />
          <span className="msg__head-trailing">
            {childCount > 0 && (
              <span className="muted">{childCount} tool call{childCount === 1 ? '' : 's'}</span>
            )}
            {durationLabel && <span className="muted">{durationLabel}</span>}
            {formattedTime && <span className="msg__time entry__time">{formattedTime}</span>}
          </span>
        </div>
        <div className="subagent-card__body entry__content" ref={scrollRef} onScroll={onScroll}>
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
              {item.children.map((child, index) => (
                <SubagentChild key={child.id} child={child} isLast={index === childCount - 1} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
