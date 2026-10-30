import { memo, useMemo, useState } from 'react';

import { renderSafeMarkdown } from '../../../lib/safeMarkdown';
import { formatTimestamp } from '../models/messageRenderUtils';
import { summaryScopeLabel } from '../models/conversationSummaryModel';
import styles from './ConversationSummaryMarker.module.css';

function scopeAttribution(scope) {
  if (scope?.kind !== 'subagent') return '';
  return `Subagent: ${summaryScopeLabel(scope)}`;
}

export const ConversationSummaryActivity = memo(function ConversationSummaryActivity({ activity }) {
  const attribution = scopeAttribution(activity?.scope);
  return (
    <article className={`${styles.activity} entry entry--chain-item`} role="status">
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge ${styles.badge}`} />
      </div>
      <div className="entry__main">
        <div className="entry__head">
          <span className="entry__role">Compacting conversation</span>
          {attribution && <span className={styles.attribution}>{attribution}</span>}
        </div>
      </div>
    </article>
  );
});

const ConversationSummaryMarker = memo(function ConversationSummaryMarker({ marker }) {
  const [open, setOpen] = useState(false);
  const summaryHtml = useMemo(
    // Compacted history can be large; Markdown remains lazy until expanded.
    () => (open ? renderSafeMarkdown(marker.summary) : ''),
    [open, marker.summary],
  );
  const attribution = scopeAttribution(marker.scope);
  const timestamp = formatTimestamp(marker.committedAt);

  return (
    <article
      id={`conversation-summary-${marker.id}`}
      className={`${styles.marker} entry entry--chain-item`}
      data-summary-scope={marker.scope?.id || undefined}
      aria-label={attribution ? `Conversation compacted, ${attribution}` : 'Conversation compacted'}
    >
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge ${styles.badge}`} />
      </div>
      <div className="entry__main">
        <details
          className="tool-event-disclosure"
          onToggle={(event) => setOpen(event.currentTarget.open)}
        >
          <summary className={`entry__head ${styles.summary}`}>
            <span className="entry__role">Conversation compacted</span>
            {attribution && <span className={styles.attribution}>{attribution}</span>}
            <span className="msg__head-trailing">
              {timestamp && <span className="msg__time entry__time">{timestamp}</span>}
            </span>
            <span className="tool-event-disclosure__chevron" aria-hidden="true" />
          </summary>
          {open && (
            <div
              className={`${styles.body} message-content`}
              dangerouslySetInnerHTML={{ __html: summaryHtml }}
            />
          )}
        </details>
      </div>
    </article>
  );
});

export default ConversationSummaryMarker;
