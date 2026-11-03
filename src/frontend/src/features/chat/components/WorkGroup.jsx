import { memo, useEffect, useMemo, useState } from 'react';
import TimelineItem from './TimelineItem';
import TokenCountIcon from './TokenCountIcon';
import { renderSafeMarkdown, sanitizeHtmlFragment } from '../../../lib/safeMarkdown';
import { describeStreamEvent, formatTimestamp, tokenMetaItemsForEvent } from '../models/messageRenderUtils';
import { isReasoningEvent } from '../models/streamEventsModel';
import { inspectTranscript } from '../models/transcriptScroll';
import {
  buildWorkSteps, reasoningSectionBody, selectWorkActivity, splitReasoningSections,
  summarizeWork, timelineItemKey, workActivityItems, workGroupHeader,
} from '../models/workGroupModel';
import styles from './WorkGroup.module.css';

const TERMINAL_LABELS = { failed: 'Failed', cancelled: 'Cancelled', interrupted: 'Interrupted', unknown: 'Unknown' };

const ReasoningBody = memo(function ReasoningBody({ event }) {
  const title = splitReasoningSections(event.content).find((section) => section.heading)?.heading || 'Reasoning';
  const html = useMemo(() => {
    const { bodyHtml, reasoningDetailHtml } = describeStreamEvent({
      ...event, content: reasoningSectionBody(event.content),
      metadata: { ...event.metadata, ...(typeof event.metadata?.summary === 'string'
        ? { summary: reasoningSectionBody(event.metadata.summary) } : {}) },
    });
    const presentation = `${bodyHtml || ''}${reasoningDetailHtml || ''}`;
    return presentation ? sanitizeHtmlFragment(presentation) : renderSafeMarkdown(reasoningSectionBody(event.content));
  }, [event]);
  return (
    <div className="chat-entry-chain--item">
      <article className="entry entry--chain-item work-group__reasoning">
        <div className="entry__gutter" aria-hidden="true"><span className="entry__badge chat-event-badge--reason" /></div>
        <div className="entry__main">
          <div className="entry__head"><span className={`entry__role ${styles.reasoningTitle}`}>{title}</span></div>
          <div className="message-content work-group__reasoning-text" dangerouslySetInnerHTML={{ __html: html }} />
        </div>
      </article>
    </div>
  );
});

const WorkEntries = memo(function WorkEntries({ items, onRegenerate, summaryMarkers, summaryActivities }) {
  return items.map((item, index) => isReasoningEvent(item.event)
    ? <ReasoningBody key={timelineItemKey(item, index)} event={item.event} />
    : <TimelineItem key={timelineItemKey(item, index)} item={item} itemIndex={index}
      onRegenerate={onRegenerate} summaryMarkers={summaryMarkers} summaryActivities={summaryActivities} />);
});

function inspectFrom(event) {
  inspectTranscript(event.currentTarget.closest('.chat-transcript'));
}

const WorkGroup = memo(function WorkGroup({
  heading, items, active = false, startedAt, endedAt, terminal = false,
  responseStatus, onRegenerate, summaryMarkers, summaryActivities,
}) {
  // Completion always collapses the live stretch; subsequent manual expansion
  // shows its complete history. New events never reset a reader's choice.
  const [disclosure, setDisclosure] = useState(null);
  const [showEarlier, setShowEarlier] = useState(false);
  const expanded = disclosure?.active === active ? disclosure.open : active;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);

  const steps = useMemo(() => buildWorkSteps(items), [items]);
  const end = active ? now : endedAt;
  const summary = useMemo(() => summarizeWork(items, steps, end, startedAt), [items, steps, end, startedAt]);
  const header = workGroupHeader({ heading, active, summary });
  const activityItems = useMemo(() => workActivityItems(items), [items]);
  const { visibleItems, hiddenCount } = useMemo(
    () => selectWorkActivity(activityItems, !active || showEarlier), [activityItems, active, showEarlier],
  );
  const reasoningEvent = items.findLast((item) => isReasoningEvent(item.event))?.event;
  const tokenMeta = reasoningEvent ? tokenMetaItemsForEvent(reasoningEvent) : [];
  const canExpand = activityItems.length > 0;
  const statusLabel = terminal && !active ? TERMINAL_LABELS[responseStatus] : null;
  const badgeState = active ? 'running' : (responseStatus === 'failed' || summary.failedCount > 0 ? 'error' : 'done');
  const time = formatTimestamp(startedAt ?? items[0]?.timestamp ?? items[0]?.event?.timestamp);
  const toggle = (event) => {
    if (!expanded) inspectFrom(event);
    setDisclosure({ active, open: !expanded });
  };
  const title = <>
    <span className={`entry__role work-group__title ${active ? styles.title : styles.settledTitle}`}>{header.title}</span>
    {header.meta && <span className="work-group__meta">{header.meta}</span>}
    {summary.failedCount > 0 && <span className="work-group__failed">{summary.failedCount} failed</span>}
    {statusLabel && <span className="work-group__status">{statusLabel}</span>}
    <span className={`msg__head-trailing ${styles.trailing}`}>
      {tokenMeta.map((meta) => <TokenCountIcon key={meta} label={meta} passive />)}
      {time && <span className="msg__time entry__time">{time}</span>}
    </span>
  </>;
  return <>
    <div className={`entry entry--chain-item work-group${active ? ' work-group--active' : ''}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge work-group__badge work-group__badge--${badgeState}${active ? ' activity-dot' : ''}`} />
      </div>
      <div className="entry__main">
        {canExpand ? <button type="button" className={`entry__head entry-toggle work-group__toggle ${styles.toggle}`}
          aria-expanded={expanded} onClick={toggle}>
          {title}<span className="tool-event-disclosure__chevron" aria-hidden="true" />
        </button> : <div className={`entry__head work-group__toggle ${styles.toggle}`}>{title}</div>}
      </div>
    </div>
    {expanded && <>
      {(hiddenCount > 0 || (active && showEarlier && activityItems.length > 5)) && <div className={styles.earlier}>
        <button type="button" className="earlier-lines-toggle" aria-expanded={showEarlier} onClick={(event) => {
          inspectFrom(event);
          setShowEarlier((previous) => !previous);
        }}>{showEarlier ? 'Show fewer items' : `Show ${hiddenCount} earlier items`}</button>
      </div>}
      <WorkEntries items={visibleItems} onRegenerate={onRegenerate}
        summaryMarkers={summaryMarkers} summaryActivities={summaryActivities} />
    </>}
  </>;
});

export default WorkGroup;
