/**
 * One collapsible work line of an assistant turn (see workGroupModel.js):
 * headed by its reasoning summary when it has one, else the typed "-ing"
 * word (TypingWord, as the pending cursor uses) while
 * live / "Worked for 17s" once done. Seconds tick and the tool count grows
 * while it is live.
 *
 * While the agent is working in this group (`active`), it previews the
 * latest steps, including every still-running parallel call. The header
 * toggles the full detail (every original row) either way.
 */

import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import TimelineItem from './TimelineItem';
import TypingWord from './TypingWord';
import { renderSafeMarkdown, sanitizeHtmlFragment } from '../../../lib/safeMarkdown';
import { describeStreamEvent, formatDuration, formatTimestamp, tokenMetaItemsForEvent } from '../models/messageRenderUtils';
import {
  buildWorkSteps,
  mergeToolCallItems,
  reasoningSectionBody,
  selectLiveSteps,
  splitGroupReasoning,
  summarizeWork,
  timelineItemKey,
  workGroupHeader,
} from '../models/workGroupModel';

const TICK_MS = 1000;

const WorkStep = memo(function WorkStep({ step }) {
  const duration = Number.isFinite(step.durationMs) ? formatDuration(step.durationMs) : '';
  return (
    <div className={`entry entry--chain-item work-step work-step--${step.status}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge work-step__badge work-step__badge--${step.kind} work-step__badge--${step.status}`} />
      </div>
      <div className="entry__main">
        <div className="entry__head">
          <span className="work-step__title">{step.title}</span>
          {step.detail && <span className="work-step__detail">{step.detail}</span>}
          {duration && <span className="msg__head-trailing muted">{duration}</span>}
        </div>
      </div>
    </div>
  );
});

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

const WorkGroup = memo(function WorkGroup({ heading, items, active, endedAt, onRegenerate }) {
  const [expanded, setExpanded] = useState(false);
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
  const restSteps = useMemo(() => (reasoningItem ? buildWorkSteps(rest) : steps), [reasoningItem, rest, steps]);
  const end = active ? now : endedAt;
  const summary = useMemo(() => summarizeWork(items, steps, end), [items, steps, end]);
  const header = workGroupHeader({ heading, active, summary });
  const liveSteps = active && !expanded ? selectLiveSteps(restSteps) : [];
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
          <span className={`entry__badge work-group__badge work-group__badge--${badgeState}`} />
        </div>
        <div className="entry__main">
          <button
            type="button"
            className="entry__head entry-toggle work-group__toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
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
      {liveSteps.map((step) => <WorkStep key={step.id} step={step} />)}
      {expanded && mergeToolCallItems(rest).map((item, index) => (
        <TimelineItem
          key={timelineItemKey(item, index)}
          item={item}
          itemIndex={index}
          onRegenerate={onRegenerate}
        />
      ))}
    </>
  );
});

export default WorkGroup;
