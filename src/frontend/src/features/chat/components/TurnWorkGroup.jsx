import { memo, useEffect, useMemo, useState } from 'react';
import WorkGroup from './WorkGroup';
import styles from './TurnWorkGroup.module.css';
import { normalizeTurnResponseStatus, summarizeTurnWork, turnWorkHeader } from '../models/workGroupModel';

const TICK_MS = 1000;
const EMPTY_GROUPS = [];

/**
 * The single turn-level disclosure for root-agent work. Individual reasoning
 * groups remain available inside it, while delegated-agent cards stay siblings
 * of this component in Message.
 */
const TurnWorkGroup = memo(function TurnWorkGroup({
  groups,
  workItems,
  active,
  endedAt,
  responseStatus,
  onRegenerate,
  summaryMarkers,
  summaryActivities,
}) {
  const normalizedGroups = Array.isArray(groups) ? groups : EMPTY_GROUPS;
  const [disclosure, setDisclosure] = useState(null);
  const status = normalizeTurnResponseStatus(responseStatus);
  // An explicit terminal state wins over stale rendering activity. Older
  // messages have no state, so their supplied active flag remains useful.
  const running = status === 'running' || (status === 'unknown' && Boolean(active));
  const expanded = disclosure?.running === running ? disclosure.open : running;
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, [running]);

  const summary = useMemo(
    () => summarizeTurnWork(normalizedGroups, workItems, running ? now : endedAt),
    [normalizedGroups, workItems, running, now, endedAt],
  );
  const header = turnWorkHeader({ responseStatus: status, running, summary });

  return (
    <section className={`entry entry--chain-item ${styles.work}`} aria-label="Assistant work">
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge ${styles.badge} ${styles[`badge${header.badge}`]}${running ? ' activity-dot' : ''}`} />
      </div>
      <div className="entry__main">
        <button
          type="button"
          className={`entry__head entry-toggle ${styles.toggle}`}
          aria-expanded={expanded}
          onClick={() => setDisclosure({ running, open: !expanded })}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              setDisclosure({ running, open: !expanded });
            }
          }}
        >
          <span className={`entry__role ${styles.title}`}>{header.title}</span>
          {header.meta && <span className={styles.meta}>{header.meta}</span>}
          {summary.failedCount > 0 && <span className={styles.failed}>{summary.failedCount} failed</span>}
          <span className="tool-event-disclosure__chevron" aria-hidden="true" />
        </button>
        {expanded && (
          <div className={styles.body}>
            {normalizedGroups.map((group, index) => (
              <WorkGroup
                key={group.id}
                heading={group.heading}
                items={group.items}
                active={running && index === normalizedGroups.length - 1}
                endedAt={index + 1 < normalizedGroups.length ? normalizedGroups[index + 1].startedAt : endedAt}
                onRegenerate={onRegenerate}
                summaryMarkers={summaryMarkers}
                summaryActivities={summaryActivities}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
});

export default TurnWorkGroup;
