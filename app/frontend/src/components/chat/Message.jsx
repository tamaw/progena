import { Fragment, useMemo } from 'react';
import { renderMarkdownToHtml } from '../../lib/markdown';

function isReasoningEvent(event) {
  return event?.eventType === 'reasoning_delta' || event?.eventType === 'reasoning_summary';
}

function hasReasoningContent(event) {
  return typeof event?.content === 'string' && event.content.trim().length > 0;
}

function isTimelineStreamEvent(event) {
  return event?.eventType !== 'message_delta';
}

function buildTimelineStreamEvents(streamEvents, reasoningText, reasoningTokenCount, messageId) {
  const timelineEvents = streamEvents.filter(isTimelineStreamEvent);
  const hasReasoningEntry = timelineEvents.some((event) =>
    isReasoningEvent(event) && hasReasoningContent(event)
  );
  const fallbackReasoningText = typeof reasoningText === 'string' ? reasoningText.trim() : '';

  if (hasReasoningEntry || !fallbackReasoningText) {
    return timelineEvents;
  }

  return [
    {
      id: `${messageId}-reasoning`,
      eventType: 'reasoning_summary',
      content: reasoningText,
      metadata: {
        source: 'message_reasoning',
        ...(Number.isFinite(reasoningTokenCount) && reasoningTokenCount > 0
          ? { reasoning_tokens: reasoningTokenCount }
          : {}),
      },
    },
    ...timelineEvents,
  ];
}

function Message({ message, streamEventFilter }) {
  const {
    role,
    content,
    timestamp,
    id: messageId = `message-${timestamp || 'unknown'}`,
    isStreaming,
    error,
    isScheduled,
    streamEvents = [],
    tokenCount = 0,
    reasoningTokenCount = 0,
    reasoningText = '',
  } = message;

  const visibleStreamEvents = useMemo(() => {
    const timelineEvents = buildTimelineStreamEvents(
      streamEvents,
      reasoningText,
      reasoningTokenCount,
      messageId,
    );
    if (typeof streamEventFilter !== 'function') return timelineEvents;
    return timelineEvents.filter(streamEventFilter);
  }, [messageId, reasoningText, reasoningTokenCount, streamEvents, streamEventFilter]);

  const formattedContent = useMemo(() => {
    if (!content) return '';
    return formatMessage(content);
  }, [content]);

  const formattedTime = useMemo(() => {
    if (!timestamp) return '';
    return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }, [timestamp]);

  const formattedTokenCount = useMemo(() => {
    if (role !== 'assistant' || isStreaming || !Number.isFinite(tokenCount) || tokenCount <= 0) {
      return '';
    }
    const count = new Intl.NumberFormat().format(tokenCount);
    return `${count} ${tokenCount === 1 ? 'token' : 'tokens'}`;
  }, [isStreaming, role, tokenCount]);

  const formattedReasoningTokenCount = useMemo(() => {
    if (
      role !== 'assistant'
      || isStreaming
      || !Number.isFinite(reasoningTokenCount)
      || reasoningTokenCount <= 0
    ) {
      return '';
    }
    const count = new Intl.NumberFormat().format(reasoningTokenCount);
    return `${count} reasoning token${reasoningTokenCount === 1 ? '' : 's'}`;
  }, [isStreaming, reasoningTokenCount, role]);

  const tokenMetaItems = [formattedTokenCount, formattedReasoningTokenCount].filter(Boolean);
  const hasConnectedEvents = role === 'assistant' && visibleStreamEvents.length > 0;
  const entryStateClass = hasConnectedEvents ? 'entry--chain-end' : 'entry--standalone';

  const messageEntry = (
    <article className={`message-wrapper ${role} msg msg--${role} entry entry--${role === 'assistant' ? 'assistant' : 'user'} ${entryStateClass}`}>
      <div className="entry__gutter" aria-hidden="true">
        <span className={`entry__badge entry__badge--${role === 'assistant' ? 'assistant' : 'user'}`}>
          {role === 'assistant' ? 'P' : 'U'}
        </span>
      </div>
      <div className="entry__main msg__entry-main">
        <div className="msg__head entry__head">
          {role === 'assistant' ? (
            <>
              <span className="entry__tag">assistant</span>
              <span className="msg__role">Progenitor</span>
            </>
          ) : (
            <>
              <span className="entry__tag entry__tag--in">user</span>
              <span className="msg__role">You</span>
            </>
          )}
          {isScheduled && (
            <span className="scheduled-badge muted">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="12 6 12 12 16 14"></polyline>
              </svg>
              Scheduled
            </span>
          )}
          <span className="msg__head-trailing">
            {tokenMetaItems.map((item, index) => (
              <Fragment key={`${item}-${index}`}>
                {index > 0 && <span className="message-meta-separator dot-sep" aria-hidden="true" />}
                <span className="msg__stat message-token-count">{item}</span>
              </Fragment>
            ))}
            <MessageActions role={role} content={content} />
            {formattedTime && <span className="msg__time entry__time">{formattedTime}</span>}
          </span>
        </div>
        <div className={`message ${role} ${isStreaming ? 'streaming' : ''} ${error ? 'error' : ''} ${isScheduled ? 'scheduled' : ''}`}>
          <div
            className="message-content"
            dangerouslySetInnerHTML={{ __html: formattedContent }}
          />
        </div>
      </div>
    </article>
  );

  if (role === 'assistant') {
    return (
      <div className={`chat-entry-chain ${hasConnectedEvents ? 'chat-entry-chain--connected' : 'chat-entry-chain--single'}`}>
        {hasConnectedEvents && <StreamEvents events={visibleStreamEvents} />}
        {messageEntry}
      </div>
    );
  }

  return (
    <>
      {messageEntry}
    </>
  );
}

function StreamEvents({ events }) {
  return (
    <>
      {events.map((event) => {
        const description = describeStreamEvent(event);
        const badge = badgeForEvent(event.eventType || 'stream_event');
        const tool = toolDetailsForEvent(event, description);
        return (
          <article
            key={event.id}
            className={`message-stream-event chat-event-entry entry entry--chain-item ${event.eventType || 'stream_event'}`}
            role="listitem"
          >
            <div className="entry__gutter" aria-hidden="true">
              <span className={badge.className}>{badge.label}</span>
            </div>
            <div className="entry__main">
              <div className="message-stream-event-header entry__head">
                <span className={tagClassForEventType(event.eventType || 'stream_event')}>{description.badge}</span>
                <span className="entry__role">{description.title}</span>
                {description.metaDetail && !tool && <span className="muted">{description.metaDetail}</span>}
              </div>
              {description.bodyHtml && (
                <div
                  className="message-stream-event-body entry__content"
                  dangerouslySetInnerHTML={{ __html: description.bodyHtml }}
                />
              )}
              {description.bodyDetail && !tool && !description.bodyHtml && (
                <div className="message-stream-event-detail entry__content">{description.bodyDetail}</div>
              )}
              {tool && (
                <div className="tool chat-event-tool">
                  <div className="tool__head">
                    <span className="tool__kind">{tool.kind}</span>
                    <span className="tool__desc">{tool.desc}</span>
                    <span className={`tool__status ${tool.statusTone === 'err' ? 'tool__status--err' : 'tool__status--ok'}`}>
                      {tool.status}
                    </span>
                  </div>
                  {tool.command && <div className="tool__cmd"><span className="prompt">$</span>{tool.command}</div>}
                  {tool.body && <pre className="tool__body tool__body--out">{tool.body}</pre>}
                </div>
              )}
            </div>
          </article>
        );
      })}
    </>
  );
}

function MessageActions({ role, content }) {
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
    } catch (e) {
      console.error('Failed to copy:', e);
    }
  };

  return (
    <span className="message-actions msg__actions">
      <button className="msg-action-btn copy-btn" onClick={handleCopy} title="Copy" aria-label="Copy message">
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3">
          <rect x="4" y="4" width="9" height="9" rx="1.5"></rect>
          <path d="M3 11V3.5A1.5 1.5 0 0 1 4.5 2H11"></path>
        </svg>
        <span>Copy</span>
      </button>
      {role === 'assistant' && (
        <button className="msg-action-btn regen-btn" title="Regenerate" aria-label="Regenerate response">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
            <path d="M13.5 8a5.5 5.5 0 1 1-1.7-3.98M13.5 2.5v3h-3"></path>
          </svg>
          <span>Regenerate</span>
        </button>
      )}
    </span>
  );
}

function describeStreamEvent(event) {
  const eventType = typeof event?.eventType === 'string' ? event.eventType : 'stream_event';
  const metadata = event?.metadata && typeof event.metadata === 'object' ? event.metadata : {};

  if (eventType === 'node_update') {
    const nodeName = typeof metadata.node_name === 'string' ? metadata.node_name : 'unknown';
    const detailParts = [];

    if (Array.isArray(metadata.output_keys) && metadata.output_keys.length > 0) {
      detailParts.push(`outputs: ${metadata.output_keys.join(', ')}`);
    }
    if (metadata.contains_messages) {
      detailParts.push('messages updated');
    }
    if (metadata.contains_pending_response) {
      detailParts.push('draft updated');
    }

    return {
      badge: 'Step',
      title: prettifyLabel(nodeName),
      metaDetail: summarizeNodeKind(nodeName),
      bodyDetail: detailParts.join(' • '),
    };
  }

  if (eventType === 'tool_start') {
    const toolName = typeof metadata.tool_name === 'string' ? metadata.tool_name : 'tool';
    const args = formatArgsPreview(metadata.args_preview);
    return {
      badge: 'Tool',
      title: `→ ${prettifyLabel(toolName)}`,
      metaDetail: args || 'invoking…',
      bodyDetail: '',
    };
  }

  if (eventType === 'tool_result') {
    const toolName = typeof metadata.tool_name === 'string' ? metadata.tool_name : 'tool';
    const parts = [];
    if (typeof metadata.duration_ms === 'number') {
      parts.push(formatDuration(metadata.duration_ms));
    }
    if (typeof metadata.result_preview === 'string' && metadata.result_preview) {
      parts.push(metadata.result_preview);
    }
    return {
      badge: 'Tool',
      title: `✓ ${prettifyLabel(toolName)}`,
      metaDetail: parts.join(' • ') || 'completed',
      bodyDetail: '',
    };
  }

  if (eventType === 'tool_error') {
    const toolName = typeof metadata.tool_name === 'string' ? metadata.tool_name : 'tool';
    const parts = [];
    if (typeof metadata.duration_ms === 'number') {
      parts.push(formatDuration(metadata.duration_ms));
    }
    if (typeof metadata.error === 'string' && metadata.error) {
      parts.push(metadata.error);
    }
    return {
      badge: 'Tool',
      title: `✗ ${prettifyLabel(toolName)}`,
      metaDetail: parts.join(' • ') || 'error',
      bodyDetail: '',
    };
  }

  if (isReasoningEvent(event)) {
    return {
      badge: 'Reasoning',
      title: 'Reasoning',
      bodyHtml: hasReasoningContent(event) ? formatMessage(event.content) : '',
      metaDetail: summarizeMetadata(metadata),
      bodyDetail: '',
    };
  }

  const explicitName = [
    metadata.tool_name,
    metadata.name,
    metadata.agent_name,
    metadata.node_name,
  ].find((value) => typeof value === 'string' && value.trim().length > 0);

  const contentDetail = typeof event?.content === 'string' ? event.content.trim() : '';
  const metadataSummary = summarizeMetadata(metadata);

  return {
    badge: badgeForEventType(eventType),
    title: explicitName ? prettifyLabel(explicitName) : prettifyLabel(eventType),
    metaDetail: metadataSummary,
    bodyDetail: contentDetail && !detailsMatch(contentDetail, metadataSummary) ? contentDetail : '',
  };
}

function detailsMatch(left, right) {
  return normalizeDetailText(left) === normalizeDetailText(right);
}

function normalizeDetailText(value) {
  return typeof value === 'string'
    ? value.replace(/\s+/g, ' ').trim().toLowerCase()
    : '';
}

function formatArgsPreview(args) {
  if (args === null || args === undefined) return '';
  if (typeof args === 'string') return args;
  try {
    const json = JSON.stringify(args);
    return json.length > 240 ? `${json.slice(0, 237)}…` : json;
  } catch {
    return String(args);
  }
}

function formatDuration(ms) {
  if (!Number.isFinite(ms)) return '';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function summarizeMetadata(metadata) {
  const summaryParts = [];

  if (Array.isArray(metadata.output_keys) && metadata.output_keys.length > 0) {
    summaryParts.push(`outputs: ${metadata.output_keys.join(', ')}`);
  }
  if (typeof metadata.text_length === 'number' && metadata.text_length > 0) {
    summaryParts.push(`${metadata.text_length} chars`);
  }
  if (typeof metadata.reasoning_tokens === 'number' && metadata.reasoning_tokens > 0) {
    summaryParts.push(`${metadata.reasoning_tokens} reasoning tokens`);
  }
  if (typeof metadata.stream_metadata?.langgraph_node === 'string') {
    summaryParts.push(`node: ${metadata.stream_metadata.langgraph_node}`);
  }

  return summaryParts.join(' • ');
}

function badgeForEventType(eventType) {
  if (eventType.includes('tool')) {
    return 'Tool';
  }
  if (eventType.includes('agent')) {
    return 'Agent';
  }
  if (eventType.includes('node')) {
    return 'Step';
  }
  if (eventType.includes('reasoning')) {
    return 'Reasoning';
  }
  return 'Event';
}

function badgeForEvent(eventType) {
  if (eventType === 'tool_error') {
    return { className: 'entry__badge entry__badge--err', label: '!' };
  }
  if (eventType.includes('tool')) {
    return { className: 'entry__badge entry__badge--tool', label: '⌘' };
  }
  if (eventType.includes('reasoning')) {
    return { className: 'entry__badge chat-event-badge--reason', label: '?' };
  }
  if (eventType.includes('node') || eventType.includes('agent')) {
    return { className: 'entry__badge chat-event-badge--step', label: '◇' };
  }
  return { className: 'entry__badge entry__badge--tool', label: '•' };
}

function tagClassForEventType(eventType) {
  if (eventType === 'tool_error') {
    return 'entry__tag entry__tag--err';
  }
  if (eventType.includes('reasoning')) {
    return 'entry__tag chat-event-tag--reason';
  }
  if (eventType.includes('node') || eventType.includes('agent')) {
    return 'entry__tag entry__tag--in';
  }
  return 'entry__tag';
}

function toolDetailsForEvent(event, description) {
  const eventType = typeof event?.eventType === 'string' ? event.eventType : '';
  if (!eventType.includes('tool')) return null;

  const metadata = event?.metadata && typeof event.metadata === 'object' ? event.metadata : {};
  const toolName = typeof metadata.tool_name === 'string' ? metadata.tool_name : 'tool';
  const args = formatArgsPreview(metadata.args_preview);
  const isError = eventType === 'tool_error';
  const result = typeof metadata.result_preview === 'string' ? metadata.result_preview : '';
  const error = typeof metadata.error === 'string' ? metadata.error : '';
  const duration = typeof metadata.duration_ms === 'number' ? formatDuration(metadata.duration_ms) : '';

  return {
    kind: toolName,
    desc: description.title.replace(/^[→✓✗]\s*/, ''),
    status: isError ? 'error' : eventType === 'tool_result' ? 'complete' : 'running',
    statusTone: isError ? 'err' : 'ok',
    command: args,
    body: [duration, error || result].filter(Boolean).join('\n'),
  };
}

function prettifyLabel(value) {
  return value
    .split(/[_-]/g)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function summarizeNodeKind(nodeName) {
  const normalized = typeof nodeName === 'string' ? nodeName.trim().toLowerCase() : '';
  if (normalized.includes('tool')) {
    return 'Tool';
  }
  if (normalized.includes('model') || normalized.includes('llm')) {
    return 'Model';
  }
  return 'Step';
}

function formatMessage(content) {
  return renderMarkdownToHtml(content);
}

export { Message };

export default Message;
