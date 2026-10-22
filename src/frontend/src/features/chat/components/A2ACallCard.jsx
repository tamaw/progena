/**
 * Renders a `{ kind: 'a2a_call', ... }` timeline item -- one card per A2A
 * tool invocation of a deployed agent, live-updated as intermediate status
 * updates stream in. Structurally mirrors SubagentCard
 * (same entry/gutter/main layout, same fixed-height auto-scrolling body).
 *
 * Each status update renders as its own small `.entry` node in a nested
 * chain (A2ASubEntry below) -- a second, smaller-scale application of the
 * same .entry/.chat-entry-chain grammar the outer timeline already uses,
 * rather than a bespoke boxed .tool block. The outer card's own connecting
 * line to whatever comes next in the *outer* chain is unaffected by this --
 * it's driven by the outer .entry__gutter (sized to .entry__main's full
 * height via CSS grid stretch), which knows nothing about what's nested
 * inside, so it keeps flowing straight through regardless of how tall the
 * nested sub-chain grows.
 */

import { memo } from 'react';
import {
  formatDuration,
  formatTimestamp,
  extractA2ASummary,
} from '../models/messageRenderUtils';
import useStickyScroll from '../hooks/useStickyScroll';
import { SubmittedGlyph, WorkingGlyph, CompletedGlyph, FailedGlyph } from './eventGlyphs';

// Distinguishes the A2A card's badge from SubagentCard's at a glance -- a
// link between two endpoints, standing in for "agent-to-agent connection".
// Also the fallback glyph for any sub-entry state with no dedicated icon
// below (input-required, auth-required, unknown, ...).
function A2AGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 7h3a5 5 0 0 1 5 5 5 5 0 0 1-5 5h-3" />
      <path d="M9 17H6a5 5 0 0 1-5-5 5 5 0 0 1 5-5h3" />
      <line x1="8" y1="12" x2="16" y2="12" />
    </svg>
  );
}

const A2A_STATE_GLYPHS = {
  submitted: SubmittedGlyph,
  working: WorkingGlyph,
  completed: CompletedGlyph,
  failed: FailedGlyph,
  rejected: FailedGlyph,
  canceled: FailedGlyph,
};

// Called directly as a plain function (not rendered as `<X />`) since these
// glyphs are pure markup with no hooks of their own -- picking one out of a
// stable, module-level map is safe, but JSX-element-style invocation of a
// dynamically-selected component trips react-hooks/static-components, which
// can't verify that stability statically.
//
// `state` comes straight off the wire (a remote A2A agent's own self-reported
// TaskStatusUpdateEvent.status.state, see applyA2ACallEvent in
// liveAssistantTurnModel.js) -- Object.hasOwn guards the lookup so a state
// name that collides with an inherited Object.prototype member (e.g.
// "constructor", "toString") can't resolve to that inherited value instead
// of falling through to the A2AGlyph default, which isn't callable as one of
// these components and would otherwise crash the render.
function glyphForA2AState(state) {
  const Glyph = Object.hasOwn(A2A_STATE_GLYPHS, state) ? A2A_STATE_GLYPHS[state] : A2AGlyph;
  return Glyph();
}

// One status update = one mini entry in the nested chain. Its glyph is the
// visual status indicator; the timestamp remains right-aligned metadata.
const A2ASubEntry = memo(function A2ASubEntry({ message, isLast }) {
  const state = message.state || 'update';
  // A per-entry clock time, not just chain position -- shows exactly when
  // each of the remote agent's own reported states occurred.
  const formattedTime = formatTimestamp(message.timestamp);
  return (
    <div className={`entry a2a-sub-entry ${isLast ? 'entry--chain-end' : 'entry--chain-item'}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge a2a-call-card__badge a2a-sub-entry__badge--${state}`}>
          {glyphForA2AState(state)}
        </span>
      </div>
      <div className="entry__main">
        <div className="entry__head">
          {message.text && <span className="a2a-sub-entry__text">{message.text}</span>}
          <span className="msg__head-trailing">
            {formattedTime && <span className="msg__time entry__time">{formattedTime}</span>}
          </span>
        </div>
      </div>
    </div>
  );
});

const A2ACallCard = memo(function A2ACallCard({ item }) {
  const messageCount = item.messages.length;
  const durationLabel = Number.isFinite(item.durationMs) ? formatDuration(item.durationMs) : '';
  const formattedTime = formatTimestamp(item.finishedAt || item.startedAt);
  // resultPreview is the whole A2A tool result dict, stringified -- only
  // shown when there's no message log to tell the story instead, and even
  // then prefer just the human-readable summary over raw JSON when extractable.
  const displayedResult = extractA2ASummary(item.resultPreview) || item.resultPreview;
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
            <div className="a2a-call-card__subchain">
              {item.messages.map((message, index) => (
                <A2ASubEntry key={message.id} message={message} isLast={index === messageCount - 1} />
              ))}
            </div>
          )}
          {item.status === 'error' && item.error && (
            <div className="a2a-call-card__error">{item.error}</div>
          )}
          {/* resultPreview is a generic, unstructured stringified dump of
              the whole A2A tool result (task id, context id, raw history,
              ...) -- shown only as a fallback when there are no per-step
              messages to tell the story instead, matching SubagentCard's
              content-or-resultPreview fallback. With messages present,
              showing both would repeat the same narrative as raw JSON. */}
          {item.status === 'success' && messageCount === 0 && displayedResult && (
            <div className="a2a-call-card__result">{displayedResult}</div>
          )}
        </div>
      </div>
    </div>
  );
});

export default A2ACallCard;
