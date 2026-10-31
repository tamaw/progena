/**
 * Renders a `{ kind: 'subagent' | 'async_subagent', ... }` timeline item.
 * Nested `task` delegates stream their own reply and tools, while tracked
 * async delegates report state transitions only. Both use a single card per
 * stable task identity, so concurrent delegates stay independent and an
 * approval pause can resume the same card.
 *
 * The header reads "<subagent> <task summary>" in the subagent colour; it
 * stands in for the `task` call itself, whose plain tool row is hidden (see
 * mergeToolCallItems). The body shows only the latest CARD_SUMMARY_LINES
 * chain lines (streamed text, nested tool calls) with an "N earlier" toggle
 * for the rest, so every concurrent card keeps the same small footprint. The
 * header stays bare chrome, styled like any other top-level entry -- the
 * tinted "card" look lives on the body.
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

import { memo, useState } from 'react';
import { renderSafeMarkdown } from '../../../lib/safeMarkdown';
import { formatArgsPreview, formatToolInputSummary, formatDuration, formatTimestamp } from '../models/messageRenderUtils';
import { selectRecentLines } from '../models/workGroupModel';
import useRunningDisclosure from '../hooks/useRunningDisclosure';
import EarlierLinesToggle from './EarlierLinesToggle';
import ConversationSummaryMarker, { ConversationSummaryActivity } from './ConversationSummaryMarker';
import ChatInfoTooltip from './ChatInfoTooltip';

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
// args, duration, and result/error remain available behind the chevron.
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
        <span className={`entry__badge subagent-card__badge subagent-card__badge--${child.status}`} />
      </div>
      <div className="entry__main">
        <details className="tool-event-disclosure">
          <summary>
            <span className="tool__summary tool-event-disclosure__summary">{summary}</span>
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

// Render growing text literally to avoid re-highlighting fenced code on every
// delta. Settled Markdown remains fully readable inside the card disclosure;
// a one-line height clamp can hide an entire leading heading or code block.
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
        <span className={`entry__badge subagent-card__badge${isStreaming ? ' subagent-card__badge--running' : ''}`} />
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
          <div
            className="message-stream-event-body message-content"
            dangerouslySetInnerHTML={{ __html: bodyHtml }}
          />
        )}
      </div>
    </div>
  );
});

const SubagentCard = memo(function SubagentCard({ item, summaryMarkers = [], summaryActivities = [] }) {
  // `item.entries` is empty for cards reconstructed from persisted history
  // on reload -- deltas are never durably stored, only the final
  // subagent_start/subagent_result pair (see buildSubagentDispatchStreamEvents
  // in chatSessionsModel.js) -- so fall back to a single synthetic message
  // entry built from the result's own preview text rather than rendering
  // blank.
  const entries = item.entries.length > 0
    ? item.entries
    : (item.resultPreview ? [{ kind: 'message', id: 'result', text: item.resultPreview }] : []);
  const lastEntry = entries.length > 0 ? entries[entries.length - 1] : null;
  const isStreamingLastEntry = item.status === 'running' && item.entries.length > 0 && lastEntry.kind === 'message';
  const toolCount = entries.filter((entry) => entry.kind === 'tool').length;
  const failedToolCount = entries.filter((entry) => entry.kind === 'tool' && entry.status === 'error').length;
  const durationLabel = Number.isFinite(item.durationMs) ? formatDuration(item.durationMs) : '';
  const formattedTime = formatTimestamp(item.finishedAt || item.startedAt);
  const [showAll, setShowAll] = useState(false);
  const { open, toggle } = useRunningDisclosure(item.status === 'running');
  const statusLabel = item.status === 'interrupted'
    ? 'Awaiting approval'
    : (item.status === 'cancelled' ? 'Cancelled' : '');
  const { visibleLines, hiddenCount } = selectRecentLines(entries, showAll);
  const error = item.status === 'error' && item.error
    ? <div className="subagent-card__error">{item.error}</div>
    : null;

  const header = (
    <button type="button" className="entry__head entry-toggle" aria-expanded={open} onClick={toggle}>
      <span className="entry__role subagent-card__name">{item.subagentName || 'subagent'}</span>
      {item.taskSummary && (
        <span className="subagent-card__title-summary">{item.taskSummary}</span>
      )}
      {item.status === 'running' && <span className="muted" role="status">Working…</span>}
      {statusLabel && <span className="muted">{statusLabel}</span>}
      {failedToolCount > 0 && <span className="work-group__failed">{failedToolCount} failed</span>}
      <span className="msg__head-trailing">
        {toolCount > 0 && (
          <span className="muted">{toolCount} tool call{toolCount === 1 ? '' : 's'}</span>
        )}
        {durationLabel && <span className="muted">{durationLabel}</span>}
        {formattedTime && <span className="msg__time entry__time">{formattedTime}</span>}
      </span>
      <span className="tool-event-disclosure__chevron" aria-hidden="true" />
    </button>
  );
  return (
    <div className={`entry entry--chain-item subagent-card subagent-card--${item.status}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge subagent-card__badge subagent-card__badge--${item.status}`} />
      </div>
      <div className="entry__main">
        {item.taskSummary ? (
          <ChatInfoTooltip className="subagent-card__header" content={item.taskSummary}>
            {header}
          </ChatInfoTooltip>
        ) : header}
        {summaryActivities.map((activity) => (
          <ConversationSummaryActivity key={activity.id} activity={activity} />
        ))}
        {summaryMarkers.map((marker) => (
          <ConversationSummaryMarker key={marker.id} marker={marker} />
        ))}
        {!open && error}
        {open && (visibleLines.length > 0 || error) && (
          <div className="subagent-card__body entry__content">
            <EarlierLinesToggle hiddenCount={hiddenCount} showAll={showAll} onToggle={() => setShowAll((value) => !value)} />
            {visibleLines.length > 0 && (
              <div className="subagent-card__children">
                {visibleLines.map((line, index) => {
                  const isLast = index === visibleLines.length - 1;
                  if (line.kind === 'tool') {
                    return <SubagentChild key={line.id} child={line} isLast={isLast} />;
                  }
                  return (
                    <SubagentMessageEntry
                      key={line.id}
                      entry={line}
                      isLast={isLast}
                      isStreaming={isStreamingLastEntry && line === lastEntry}
                    />
                  );
                })}
              </div>
            )}
            {error}
          </div>
        )}
      </div>
    </div>
  );
});

export default SubagentCard;
