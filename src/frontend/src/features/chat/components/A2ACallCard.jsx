/**
 * Renders a `{ kind: 'a2a_call', ... }` timeline item -- one card per A2A
 * tool invocation of a deployed agent, live-updated as intermediate status
 * updates stream in. Structurally mirrors SubagentCard
 * (same entry/gutter/main layout, same fixed-height auto-scrolling body).
 *
 * Each status update renders as its own small `.entry` node in a nested
 * chain (A2ASubEntry below), trimmed to the latest CARD_SUMMARY_LINES with
 * an "N earlier" toggle -- a second, smaller-scale application of the same
 * .entry/.chat-entry-chain grammar the outer timeline already uses.
 */

import { memo, useState } from 'react';
import {
  formatDuration,
  formatTimestamp,
  extractA2ASummary,
} from '../models/messageRenderUtils';
import { selectRecentLines } from '../models/workGroupModel';
import useRunningDisclosure from '../hooks/useRunningDisclosure';
import EarlierLinesToggle from './EarlierLinesToggle';

// One status update = one mini entry in the nested chain. Its dot colour is
// the visual status indicator; the timestamp remains right-aligned metadata.
const A2ASubEntry = memo(function A2ASubEntry({ message, isLast }) {
  const state = message.state || 'update';
  // A per-entry clock time, not just chain position -- shows exactly when
  // each of the remote agent's own reported states occurred.
  const formattedTime = formatTimestamp(message.timestamp);
  return (
    <div className={`entry a2a-sub-entry ${isLast ? 'entry--chain-end' : 'entry--chain-item'}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge a2a-call-card__badge a2a-sub-entry__badge--${state}`} />
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
  const hasFailedUpdate = item.messages.some((message) => message.state === 'failed' || message.state === 'rejected');
  const durationLabel = Number.isFinite(item.durationMs) ? formatDuration(item.durationMs) : '';
  const formattedTime = formatTimestamp(item.finishedAt || item.startedAt);
  // resultPreview is the whole A2A tool result dict, stringified -- only
  // shown when there's no message log to tell the story instead, and even
  // then prefer just the human-readable summary over raw JSON when extractable.
  const displayedResult = extractA2ASummary(item.resultPreview) || item.resultPreview;
  const [showAll, setShowAll] = useState(false);
  const { open, toggle } = useRunningDisclosure(item.status === 'running');
  const { visibleLines, hiddenCount } = selectRecentLines(item.messages, showAll);
  const error = item.status === 'error' && item.error
    ? <div className="a2a-call-card__error">{item.error}</div>
    : null;

  return (
    <div className={`entry entry--chain-item a2a-call-card a2a-call-card--${item.status}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge a2a-call-card__badge a2a-call-card__badge--${item.status}`} />
      </div>
      <div className="entry__main">
        <button type="button" className="entry__head entry-toggle" aria-expanded={open} onClick={toggle}>
          <span className="entry__role">{item.agentName || 'agent'}</span>
          {hasFailedUpdate && <span className="work-group__failed">Failed</span>}
          <span className="msg__head-trailing">
            {messageCount > 0 && (
              <span className="muted">{messageCount} update{messageCount === 1 ? '' : 's'}</span>
            )}
            {durationLabel && <span className="muted">{durationLabel}</span>}
            {formattedTime && <span className="msg__time entry__time">{formattedTime}</span>}
          </span>
          <span className="tool-event-disclosure__chevron" aria-hidden="true" />
        </button>
        {!open && error}
        {open && (
          <div className="a2a-call-card__body entry__content">
            <EarlierLinesToggle hiddenCount={hiddenCount} showAll={showAll} onToggle={() => setShowAll((value) => !value)} />
            {visibleLines.length > 0 && (
              <div className="a2a-call-card__subchain">
                {visibleLines.map((message, index) => (
                  <A2ASubEntry key={message.id} message={message} isLast={index === visibleLines.length - 1} />
                ))}
              </div>
            )}
            {error}
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
        )}
      </div>
    </div>
  );
});

export default A2ACallCard;
