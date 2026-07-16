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
 * `item.entries` is a single ordered chain mixing the subagent's own
 * streamed text (`{ kind: 'message', ... }`) and its nested tool calls
 * (`{ kind: 'tool', ... }`), in true arrival order -- the same "mini
 * card/event" treatment A2ACallCard gives its own per-update sub-entries,
 * applied here to *both* entry kinds instead of just tool calls, so text
 * either side of a nested tool call renders as two distinct message cards
 * rather than one accumulated blob with the tool calls appended
 * underneath. Tool entries reuse the existing .tool block for content and
 * the outer card's own running/error badge modifiers for status color,
 * rather than inventing a parallel set.
 */

import { memo } from 'react';
import {
  renderSafeMarkdown,
  formatArgsPreview,
  formatToolInputSummary,
  formatDuration,
  formatTimestamp,
  statusPillProps,
} from './messageRenderUtils';
import useStickyScroll from './useStickyScroll';
import StatusPill from './StatusPill';
import { SubmittedGlyph, WorkingGlyph, CompletedGlyph, FailedGlyph } from './eventGlyphs';

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

// Distinguishes a streamed-text entry from a nested-tool-call entry in the
// same chain -- a speech bubble, standing in for "the subagent said this".
function MessageGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
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

const SubagentTaskSummaryEntry = memo(function SubagentTaskSummaryEntry({ taskSummary, isLast }) {
  return (
    <div className={`entry subagent-child subagent-card__task-summary-entry ${isLast ? 'entry--chain-end' : 'entry--chain-item'}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className="entry__badge subagent-card__badge subagent-card__badge--submitted">
          {SubmittedGlyph()}
        </span>
      </div>
      <div className="entry__main">
        <div className="entry__head">
          <div className="message-stream-event-body subagent-card__task-summary muted">{taskSummary}</div>
        </div>
      </div>
    </div>
  );
});

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
//
// Memoized: `entries` array updates preserve reference equality for every
// entry except the one actually being mutated (see appendSubagentMessageDelta/
// upsertSubagentToolEntry in liveAssistantTurnModel.js), so without memo()
// every already-settled child would still re-render -- and re-run
// SubagentMessageEntry's markdown parse below -- on every single delta of
// an unrelated, later entry, turning a streaming turn with several message/
// tool entries into O(entries x deltas) work instead of O(deltas).

function childToolSummary(child) {
  const toolName = child.toolName || 'tool';
  const detail = formatToolInputSummary(toolName, child.argsPreview);
  if (child.status === 'error') {
    return `${toolName} · failed${detail ? ` · ${detail}` : ''}`;
  }
  return detail ? `${toolName} · ${detail}` : toolName;
}

// Every nested tool call is a closed-by-default disclosure, matching the
// top-level tool-event-disclosure control. The status/summary stays visible;
// args, duration, and result/error remain available behind Expand.
const SubagentChild = memo(function SubagentChild({ child, isLast }) {
  const args = formatArgsPreview(child.argsPreview);
  const body = [
    Number.isFinite(child.durationMs) ? formatDuration(child.durationMs) : '',
    child.error || child.resultPreview,
  ].filter(Boolean).join('\n');
  const summary = childToolSummary(child);

  return (
    <div className={`entry subagent-child ${isLast ? 'entry--chain-end' : 'entry--chain-item'}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge subagent-card__badge subagent-card__badge--${child.status}`}>
          {glyphForChildStatus(child.status)}
        </span>
      </div>
      <div className="entry__main">
        <details className="tool-event-disclosure">
          <summary>
            <span className="tool__summary tool-event-disclosure__summary">{summary}</span>
            <span className="tool-event-disclosure__state tool-event-disclosure__state--expand tool-event-disclosure__label">Expand</span>
            <span className="tool-event-disclosure__state tool-event-disclosure__state--collapse tool-event-disclosure__label">Collapse</span>
            <span className="tool-event-disclosure__chevron" aria-hidden="true" />
          </summary>
          <div className="tool chat-event-tool">
            <div className="tool__head">
              <span className="tool__kind">{child.toolName || 'tool'}</span>
              <span className="tool__desc">{args}</span>
            </div>
            {body && <pre className="tool__body">{body}</pre>}
          </div>
        </details>
      </div>
    </div>
  );
});

// One burst of the subagent's own streamed text = one mini entry in the
// chain, rendered in the same compact header row as an A2A progress update.
// `isStreaming` only ever applies to the chain's trailing entry. Render that
// entry as literal text until it settles: parsing the growing response here
// would re-highlight an already-closed fenced block for every later delta.
//
// Memoized for the same reason as SubagentChild above -- settled entries keep
// their identity while the trailing entry updates, so they never re-render.
const SubagentMessageEntry = memo(function SubagentMessageEntry({ entry, isLast, isStreaming }) {
  const bodyHtml = !isStreaming && entry.text
    ? renderSafeMarkdown(entry.text)
    : '';

  return (
    <div className={`entry subagent-child ${isLast ? 'entry--chain-end' : 'entry--chain-item'}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge subagent-card__badge${isStreaming ? ' subagent-card__badge--running' : ''}`}>
          <MessageGlyph />
        </span>
      </div>
      <div className="entry__main">
        {isStreaming ? (
          <div className="entry__head">
            <div className="message-stream-event-body message-content--streaming-text">
              {entry.text || ''}
              <span className="stream-cursor" aria-hidden="true">▋</span>
            </div>
          </div>
        ) : bodyHtml && (
          <div className="entry__head">
            <div
              className="message-stream-event-body"
              dangerouslySetInnerHTML={{ __html: bodyHtml }}
            />
          </div>
        )}
      </div>
    </div>
  );
});

const SubagentCard = memo(function SubagentCard({ item }) {
  // `item.entries` is empty for cards reconstructed from persisted history
  // on reload -- deltas are never durably stored, only the final
  // subagent_start/subagent_result pair (see buildSubagentDispatchStreamEvents
  // in chatSessionsModel.js) -- so fall back to a single synthetic message
  // entry built from the result's own preview text rather than rendering
  // blank.
  const entries = item.entries.length > 0
    ? item.entries
    : (item.resultPreview ? [{ kind: 'message', id: 'result', text: item.resultPreview }] : []);
  const entryCount = entries.length;
  const lastEntry = entryCount > 0 ? entries[entryCount - 1] : null;
  const isStreamingLastEntry = item.status === 'running' && item.entries.length > 0 && lastEntry.kind === 'message';
  const toolCount = entries.filter((entry) => entry.kind === 'tool').length;
  const durationLabel = Number.isFinite(item.durationMs) ? formatDuration(item.durationMs) : '';
  const formattedTime = formatTimestamp(item.finishedAt || item.startedAt);
  const stickyKey = `${entryCount}:${lastEntry && lastEntry.kind === 'message' ? lastEntry.text.length : lastEntry?.status}:${item.status}`;
  const { ref: scrollRef, onScroll } = useStickyScroll(stickyKey);

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
            {toolCount > 0 && (
              <span className="muted">{toolCount} tool call{toolCount === 1 ? '' : 's'}</span>
            )}
            {durationLabel && <span className="muted">{durationLabel}</span>}
            {formattedTime && <span className="msg__time entry__time">{formattedTime}</span>}
          </span>
        </div>
        <div className="subagent-card__body entry__content" ref={scrollRef} onScroll={onScroll}>
          {(item.taskSummary || entryCount > 0) && (
            <div className="subagent-card__children">
              {item.taskSummary && (
                <SubagentTaskSummaryEntry taskSummary={item.taskSummary} isLast={entryCount === 0} />
              )}
              {entries.map((entry, index) => (
                entry.kind === 'tool' ? (
                  <SubagentChild key={entry.id} child={entry} isLast={index === entryCount - 1} />
                ) : (
                  <SubagentMessageEntry
                    key={entry.id}
                    entry={entry}
                    isLast={index === entryCount - 1}
                    isStreaming={isStreamingLastEntry && index === entryCount - 1}
                  />
                )
              ))}
            </div>
          )}
          {item.status === 'error' && item.error && (
            <div className="subagent-card__error">{item.error}</div>
          )}
        </div>
      </div>
    </div>
  );
});

export default SubagentCard;
