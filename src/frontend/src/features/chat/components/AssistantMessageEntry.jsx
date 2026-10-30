import { useMemo, useRef } from 'react';
import { formatDisplayToolCalls } from '../models/messageDisplayModel';
import { renderSafeMarkdown } from '../../../lib/safeMarkdown';
import { formatTimestamp } from '../models/messageRenderUtils';
import { useMermaidRender } from '../../../hooks/useMermaidRender';
import MessageActions from './MessageActions';
import MessageTokenUsage from './MessageTokenUsage';
import styles from './AssistantMessageEntry.module.css';

const TERMINAL_LABELS = { failed: 'Failed', cancelled: 'Cancelled', interrupted: 'Interrupted' };

export default function AssistantMessageEntry({ item, agentName, onRegenerate, showResponseStatus = true }) {
  const contentRef = useRef(null);
  const formattedContent = useMemo(
    () => item.isStreaming ? '' : renderSafeMarkdown(item.content || ''),
    [item.content, item.isStreaming],
  );
  // Mermaid replaces the mounted HTML outside React. Keep its DOM intact
  // when only usage/status metadata changes.
  const markdownHtml = useMemo(() => ({ __html: formattedContent }), [formattedContent]);
  useMermaidRender(contentRef, formattedContent);
  const formattedTime = formatTimestamp(item.timestamp);
  const statusLabel = showResponseStatus ? TERMINAL_LABELS[item.responseStatus] : null;

  return (
    <div className="chat-entry-chain--item">
      <article className={`message-wrapper assistant msg msg--assistant entry entry--assistant ${item.error ? 'entry--standalone' : 'entry--chain-end'}`}>
        <div className="entry__gutter" aria-hidden="true">
          <span className={`entry__badge entry__badge--assistant${item.isStreaming && !item.error ? ' activity-dot' : ''}`} />
        </div>
        <div className="entry__main msg__entry-main">
          <div className="msg__head entry__head">
            <span className="msg__role">{item.displayAgentName || agentName}</span>
            {item.isScheduled && (
              <span className="scheduled-badge muted">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10"></circle>
                  <polyline points="12 6 12 12 16 14"></polyline>
                </svg>
                Scheduled
              </span>
            )}
            <span className="msg__head-trailing">
              {statusLabel && (
                <span className={`${styles.status}${item.responseStatus === 'failed' ? ` ${styles.failed}` : ''}`} role="status">
                  {statusLabel}
                </span>
              )}
              <MessageTokenUsage tokenCount={item.tokenCount} finalCallTokenCount={item.finalCallTokenCount} />
              {Array.isArray(item.displayToolCalls) && item.displayToolCalls.length > 0 && (
                <span className="msg__model">{formatDisplayToolCalls(item.displayToolCalls)}</span>
              )}
              <MessageActions content={item.content || ''} onRegenerate={onRegenerate} />
              {formattedTime && <span className="msg__time entry__time">{formattedTime}</span>}
            </span>
          </div>
          <div className={`message assistant ${item.isStreaming ? 'streaming' : ''} ${item.error ? 'error' : ''} ${item.isScheduled ? 'scheduled' : ''}`}>
            {item.isStreaming ? (
              <div className="message-content message-content--streaming-text">
                {item.content || ''}
                {item.content && <span className="stream-cursor" aria-hidden="true">▋</span>}
              </div>
            ) : (
              <div ref={contentRef} className="message-content" dangerouslySetInnerHTML={markdownHtml} />
            )}
          </div>
        </div>
      </article>
    </div>
  );
}
