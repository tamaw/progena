/**
 * A headed reasoning group inside an assistant turn. The turn owns the one
 * generic work disclosure; headless groups render their timeline rows directly
 * so a tool is never hidden behind a second, identical work summary.
 *
 * Headed groups retain their reasoning summary and their own optional detail
 * disclosure. While a group is active, its rows are expanded.
 */

import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import TimelineItem from './TimelineItem';
import TypingWord from './TypingWord';
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

// The reasoning behind a headed group, under its header: clamped to a few
// lines, with a right-hand chevron (like a tool row's) only when the text is
// actually cut off at the current width.
const ReasoningBody = memo(function ReasoningBody({ event }) {
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const textRef = useRef(null);
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

  useLayoutEffect(() => {
    const element = textRef.current;
    if (!element || open) return undefined;
    const measure = () => setOverflows(element.scrollHeight > element.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [html, open]);

  return (
    <div className={`work-group__reasoning${open ? ' work-group__reasoning--open' : ''}`}>
      <div
        ref={textRef}
        className="message-content work-group__reasoning-text"
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {(overflows || open) && (
        <button
          type="button"
          className="work-group__reasoning-toggle"
          aria-expanded={open}
          aria-label={open ? 'Show less reasoning' : 'Show full reasoning'}
          onClick={() => setOpen((value) => !value)}
        >
          <span className="tool-event-disclosure__chevron" aria-hidden="true" />
        </button>
      )}
    </div>
  );
});

const WorkEntries = memo(function WorkEntries({
  items,
  onRegenerate,
  summaryMarkers,
  summaryActivities,
}) {
  return mergeToolCallItems(items).map((item, index) => (
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

const WorkGroup = memo(function WorkGroup(props) {
  if (!props.heading) {
    return <WorkEntries {...props} />;
  }
  return <HeadedWorkGroup {...props} />;
});

const HeadedWorkGroup = memo(function HeadedWorkGroup({ heading, items, active, endedAt, onRegenerate, summaryMarkers, summaryActivities }) {
  // A manual choice applies only to this phase. Finishing work must collapse
  // it even if the user reopened it while it was active.
  const [disclosure, setDisclosure] = useState(null);
  const expanded = disclosure?.active === active ? disclosure.open : active;
  const toggle = () => setDisclosure({ active, open: !expanded });
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, [active]);

  // The header already names the group's reasoning, so that reasoning is not
  // repeated as a row or a preview step; its text is the group's body.
  const { reasoningItem, rest } = useMemo(() => splitGroupReasoning(heading, items), [heading, items]);
  const steps = useMemo(() => buildWorkSteps(items), [items]);
  const end = active ? now : endedAt;
  const summary = useMemo(() => summarizeWork(items, steps, end), [items, steps, end]);
  const header = workGroupHeader({ heading, active, summary });
  const badgeState = active ? 'running' : (summary.failedCount > 0 ? 'error' : 'done');
  const tokenMeta = reasoningItem ? tokenMetaItemsForEvent(reasoningItem.event) : [];
  const firstItem = items[0];
  const time = formatTimestamp(firstItem?.timestamp ?? firstItem?.event?.timestamp);

  // The group's rows belong to the same agent and conversation, so they are
  // siblings in the turn's chain (same gutter, same line) rather than nested
  // under the header. Only delegated agents (subagent/A2A cards) nest.
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
          >
            <span className="entry__role work-group__title">
              {header.title ?? <TypingWord reserveWidth />}
            </span>
            {header.meta && <span className="work-group__meta">{header.meta}</span>}
            {summary.failedCount > 0 && (
              <span className="work-group__failed">{summary.failedCount} failed</span>
            )}
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
          onRegenerate={onRegenerate}
          summaryMarkers={summaryMarkers}
          summaryActivities={summaryActivities}
        />
      )}
    </>
  );
});

export default WorkGroup;
