import { memo, useId, useMemo, useState } from 'react';
import { renderSafeMarkdown } from '../../../lib/safeMarkdown';
import { summaryScopeLabel } from '../models/conversationSummaryModel';
import styles from './ConversationSummaryMarker.module.css';

function SummaryIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 3.5h10M3 6.75h10M3 10h6.5M3 13.25h4" />
      <path d="m11.25 10.75 1.25 1.25 2-2" />
    </svg>
  );
}

export const ConversationSummaryActivity = memo(function ConversationSummaryActivity({ activity }) {
  const label = summaryScopeLabel(activity?.scope);
  return (
    <div className={styles.activity} role="status">
      <span className={styles.activityIcon} aria-hidden="true"><SummaryIcon /></span>
      <span>Summarizing conversation…</span>
      {label === 'Conversation' ? null : <span className={styles.attribution}>for {label}</span>}
    </div>
  );
});

const ConversationSummaryMarker = memo(function ConversationSummaryMarker({ marker }) {
  const tooltipId = useId();
  const [hasOpened, setHasOpened] = useState(false);
  const summaryHtml = useMemo(
    () => (hasOpened ? renderSafeMarkdown(marker.summary) : ''),
    [hasOpened, marker.summary],
  );
  const label = summaryScopeLabel(marker.scope);
  const attribution = marker.scope?.kind === 'subagent' ? label : '';

  return (
    <aside className={styles.marker} aria-label={attribution ? `Conversation summarized by ${attribution}` : 'Conversation summarized'}>
      <details className={styles.disclosure} onToggle={(event) => {
        if (event.currentTarget.open) setHasOpened(true);
      }}>
        <summary className={styles.summary} aria-describedby={tooltipId}>
          <span className={styles.icon} aria-hidden="true"><SummaryIcon /></span>
          <span>Conversation summarized</span>
          {attribution ? <span className={styles.attribution}>by {attribution}</span> : null}
          <span className="tool-event-disclosure__chevron" aria-hidden="true" />
        </summary>
        {hasOpened ? <div className={`${styles.body} message-content`} dangerouslySetInnerHTML={{ __html: summaryHtml }} /> : null}
      </details>
      <span id={tooltipId} className={styles.tooltip} role="tooltip">Earlier messages remain available. Show the summary created at this point.</span>
    </aside>
  );
});

export default ConversationSummaryMarker;
