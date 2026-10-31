/**
 * One collapsible phase of root-agent work. The initial generic phase and
 * each reasoning section are peers in the transcript chain; expanding a
 * reasoning phase reveals its complete reasoning and its following tools.
 */

import { memo, useEffect, useMemo, useState } from 'react';
import TimelineItem from './TimelineItem';
import { renderSafeMarkdown, sanitizeHtmlFragment } from '../../../lib/safeMarkdown';
import { describeStreamEvent, formatTimestamp, tokenMetaItemsForEvent } from '../models/messageRenderUtils';
import {
  buildWorkSteps,
  mergeToolCallItems,
  reasoningSectionBody,
  splitGroupReasoning,
  summarizeWork,
  timelineItemKey,
  workGroupHeader,
} from '../models/workGroupModel';

const TICK_MS = 1000;

// The group's disclosure is the only reasoning disclosure. Provider detail
// metadata remains preferred by describeStreamEvent, then all resulting HTML
// is sanitized before rendering.
const ReasoningBody = memo(function ReasoningBody({ event }) {
  const { bodyHtml, reasoningDetailHtml } = useMemo(() => describeStreamEvent({
    ...event,
    content: reasoningSectionBody(event.content),
  }), [event]);
  const html = useMemo(() => {
    const presentationHtml = `${bodyHtml || ''}${reasoningDetailHtml || ''}`;
    return presentationHtml
      ? sanitizeHtmlFragment(presentationHtml)
      : renderSafeMarkdown(reasoningSectionBody(event.content));
  }, [bodyHtml, event.content, reasoningDetailHtml]);

  return (
    <div className="work-group__reasoning">
      <div
        className="message-content work-group__reasoning-text"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
});

const WorkEntries = memo(function WorkEntries({
  items,
  cardToolCallIds,
  onRegenerate,
  summaryMarkers,
  summaryActivities,
}) {
  const hiddenToolCallIds = useMemo(() => new Set(cardToolCallIds ? cardToolCallIds.split('\n') : []), [cardToolCallIds]);
  return mergeToolCallItems(items, { hiddenToolCallIds }).map((item, index) => (
    <TimelineItem
      key={timelineItemKey(item, index)}
      item={item}
      itemIndex={index}
      onRegenerate={onRegenerate}
      summaryMarkers={summaryMarkers}
      summaryActivities={summaryActivities}
    />
  ));
});

function terminalStatusLabel(responseStatus) {
  if (responseStatus === 'failed') return 'Failed';
  if (responseStatus === 'cancelled') return 'Cancelled';
  if (responseStatus === 'interrupted') return 'Interrupted';
  if (responseStatus === 'unknown') return 'Unknown';
  return '';
}

function terminalGenericTitle(responseStatus) {
  if (responseStatus === 'failed') return 'Work failed';
  if (responseStatus === 'cancelled') return 'Work cancelled';
  if (responseStatus === 'interrupted') return 'Work interrupted';
  if (responseStatus === 'unknown') return 'Work';
  return '';
}

const WorkGroup = memo(function WorkGroup({
  heading,
  items,
  active = false,
  endedAt,
  terminal = false,
  responseStatus,
  onRegenerate,
  // Newline-joined ids of `task` calls shown as subagent cards; a string so
  // this memoized group only re-renders when the set actually changes.
  cardToolCallIds = '',
  summaryMarkers,
  summaryActivities,
}) {
  // A manual choice only applies to this phase while it retains the same
  // running state. Finishing work collapses a live group automatically.
  const [disclosure, setDisclosure] = useState(null);
  const expanded = disclosure?.active === active ? disclosure.open : active;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, [active]);

  const { reasoningItem, rest } = useMemo(() => splitGroupReasoning(heading, items), [heading, items]);
  const steps = useMemo(() => buildWorkSteps(items), [items]);
  const end = active ? now : endedAt;
  const summary = useMemo(() => summarizeWork(items, steps, end), [items, steps, end]);
  const defaultHeader = workGroupHeader({ heading, active, summary });
  const explicitGenericTitle = terminal && !active && !heading ? terminalGenericTitle(responseStatus) : '';
  const header = explicitGenericTitle ? { ...defaultHeader, title: explicitGenericTitle } : defaultHeader;
  const terminalLabel = terminal && !active && !explicitGenericTitle ? terminalStatusLabel(responseStatus) : '';
  const badgeState = active
    ? 'running'
    : (responseStatus === 'failed' ? 'error' : (summary.failedCount > 0 ? 'error' : 'done'));
  const tokenMeta = reasoningItem ? tokenMetaItemsForEvent(reasoningItem.event) : [];
  const firstItem = items[0];
  const time = formatTimestamp(firstItem?.timestamp ?? firstItem?.event?.timestamp);

  const toggle = () => setDisclosure({ active, open: !expanded });
  const toggleOnKeyDown = (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggle();
    }
  };

  return (
    <>
      <div className={`entry entry--chain-item work-group${active ? ' work-group--active' : ''}`}>
        <div className="entry__gutter" aria-hidden="true">
          <span className={`entry__badge work-group__badge work-group__badge--${badgeState}${active ? ' activity-dot' : ''}`} />
        </div>
        <div className="entry__main">
          <button
            type="button"
            className="entry__head entry-toggle work-group__toggle"
            aria-expanded={expanded}
            onClick={toggle}
            onKeyDown={toggleOnKeyDown}
          >
            <span className="entry__role work-group__title">{header.title}</span>
            {header.meta && <span className="work-group__meta">{header.meta}</span>}
            {summary.failedCount > 0 && (
              <span className="work-group__failed">{summary.failedCount} failed</span>
            )}
            {terminalLabel && <span className="work-group__status">{terminalLabel}</span>}
            <span className="msg__head-trailing">
              {tokenMeta.map((meta) => <span key={meta} className="msg__stat message-token-count">{meta}</span>)}
              {time && <span className="msg__time entry__time">{time}</span>}
            </span>
            <span className="tool-event-disclosure__chevron" aria-hidden="true" />
          </button>
          {expanded && reasoningItem && <ReasoningBody event={reasoningItem.event} />}
        </div>
      </div>
      {expanded && (
        <WorkEntries
          items={rest}
          cardToolCallIds={cardToolCallIds}
          onRegenerate={onRegenerate}
          summaryMarkers={summaryMarkers}
          summaryActivities={summaryActivities}
        />
      )}
    </>
  );
});

export default WorkGroup;
