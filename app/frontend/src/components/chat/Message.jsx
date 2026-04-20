import { Fragment, useMemo } from 'react';

function isReasoningEvent(event) {
  return event?.eventType === 'reasoning_delta' || event?.eventType === 'reasoning_summary';
}

function Message({ message, streamEventFilter }) {
  const {
    role,
    content,
    timestamp,
    isStreaming,
    error,
    isScheduled,
    streamEvents = [],
    tokenCount = 0,
    reasoningTokenCount = 0,
    reasoningText = '',
  } = message;

  const visibleStreamEvents = useMemo(() => {
    if (typeof streamEventFilter !== 'function') return streamEvents;
    return streamEvents.filter(streamEventFilter);
  }, [streamEvents, streamEventFilter]);
  const visibleReasoningEvents = useMemo(
    () => visibleStreamEvents.filter(isReasoningEvent),
    [visibleStreamEvents],
  );

  const formattedContent = useMemo(() => {
    if (!content) return '';
    return formatMessage(content);
  }, [content]);

  const formattedReasoningText = useMemo(() => {
    if (!reasoningText) return '';
    return formatMessage(reasoningText);
  }, [reasoningText]);

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

  const metaItems = [formattedTime, formattedTokenCount, formattedReasoningTokenCount].filter(Boolean);
  const hasMessageMeta = metaItems.length > 0;
  const shouldShowReasoningText =
    role === 'assistant' && visibleReasoningEvents.length > 0 && reasoningText.trim().length > 0;

  return (
    <div className={`message-wrapper ${role}`}>
      <div className={`message ${role} ${isStreaming ? 'streaming' : ''} ${error ? 'error' : ''} ${isScheduled ? 'scheduled' : ''}`}>
        {isScheduled && (
          <div className="scheduled-badge">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10"></circle>
              <polyline points="12 6 12 12 16 14"></polyline>
            </svg>
            Scheduled
          </div>
        )}
        {role === 'assistant' && visibleStreamEvents.length > 0 && (
          <StreamEvents events={visibleStreamEvents} />
        )}
        {shouldShowReasoningText && (
          <div
            className="message-reasoning-text"
            dangerouslySetInnerHTML={{ __html: formattedReasoningText }}
          />
        )}
        <div
          className="message-content"
          dangerouslySetInnerHTML={{ __html: formattedContent }}
        />
        {hasMessageMeta && (
          <div className="message-meta">
            {metaItems.map((item, index) => (
              <Fragment key={`${item}-${index}`}>
                {index > 0 && <span className="message-meta-separator" aria-hidden="true" />}
                <span className={index === 0 ? 'message-timestamp' : 'message-token-count'}>
                  {item}
                </span>
              </Fragment>
            ))}
          </div>
        )}
      </div>
      <MessageActions role={role} content={content} />
    </div>
  );
}

function StreamEvents({ events }) {
  return (
    <div className="message-stream-events">
      {events.map((event) => {
        const description = describeStreamEvent(event);
        return (
          <div
            key={event.id}
            className={`message-stream-event ${event.eventType || 'stream_event'}`}
          >
            <div className="message-stream-event-header">
              <span className="message-stream-event-badge">{description.badge}</span>
              <span className="message-stream-event-title">{description.title}</span>
            </div>
            {description.bodyHtml && (
              <div
                className="message-stream-event-body"
                dangerouslySetInnerHTML={{ __html: description.bodyHtml }}
              />
            )}
            {description.detail && (
              <div className="message-stream-event-detail">{description.detail}</div>
            )}
          </div>
        );
      })}
    </div>
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
    <div className="message-actions">
      <button className="msg-action-btn copy-btn" onClick={handleCopy}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
        </svg>
        Copy
      </button>
      {role === 'assistant' && (
        <button className="msg-action-btn regen-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="1 4 1 10 7 10"></polyline>
            <polyline points="23 20 23 14 17 14"></polyline>
            <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"></path>
          </svg>
          Regenerate
        </button>
      )}
    </div>
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
      detail: detailParts.join(' • ') || 'Node updated',
    };
  }

  if (eventType === 'tool_start') {
    const toolName = typeof metadata.tool_name === 'string' ? metadata.tool_name : 'tool';
    const args = formatArgsPreview(metadata.args_preview);
    return {
      badge: 'Tool',
      title: `→ ${prettifyLabel(toolName)}`,
      detail: args || 'invoking…',
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
      detail: parts.join(' • ') || 'completed',
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
      detail: parts.join(' • ') || 'error',
    };
  }

  if (eventType === 'message_delta') {
    const text = typeof event?.content === 'string' ? event.content : '';
    return {
      badge: 'Token',
      title: text || '·',
      detail: typeof metadata.text_length === 'number'
        ? `${metadata.text_length} chars`
        : '',
    };
  }

  if (isReasoningEvent(event)) {
    const detail = summarizeMetadata(metadata);
    return {
      badge: 'Reasoning',
      title: 'Reasoning',
      detail: detail || (eventType === 'reasoning_delta' ? 'Tracing response generation' : 'Reasoning captured'),
    };
  }

  const explicitName = [
    metadata.tool_name,
    metadata.name,
    metadata.agent_name,
    metadata.node_name,
  ].find((value) => typeof value === 'string' && value.trim().length > 0);

  const detailParts = [];
  if (typeof event?.content === 'string' && event.content.trim()) {
    detailParts.push(event.content.trim());
  }

  const metadataSummary = summarizeMetadata(metadata);
  if (metadataSummary) {
    detailParts.push(metadataSummary);
  }

  return {
    badge: badgeForEventType(eventType),
    title: explicitName ? prettifyLabel(explicitName) : prettifyLabel(eventType),
    detail: detailParts.join(' • '),
  };
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

function prettifyLabel(value) {
  return value
    .split(/[_-]/g)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function formatMessage(content) {
  if (!content) return '';

  let formatted = escapeHtml(content);

  // Code blocks with syntax highlighting
  formatted = formatted.replace(/```(\w*)\n([\s\S]*?)```/g, (match, lang, code) => {
    const langClass = lang ? ` language-${lang}` : '';
    const langLabel = lang ? `<span class="code-lang">${lang}</span>` : '';
    return `<div class="code-block">${langLabel}<pre><code class="${langClass}">${code.trim()}</code></pre></div>`;
  });

  // Inline code
  formatted = formatted.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

  // Headers
  formatted = formatted.replace(/^(#{1,6})\s+(.+)$/gm, (match, hashes, text) => {
    const level = hashes.length;
    return `<h${level} class="md-heading">${text}</h${level}>`;
  });

  // Bold
  formatted = formatted.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  formatted = formatted.replace(/__([^_]+)__/g, '<strong>$1</strong>');

  // Italic
  formatted = formatted.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  formatted = formatted.replace(/_([^_]+)_/g, '<em>$1</em>');

  // Strikethrough
  formatted = formatted.replace(/~~([^~]+)~~/g, '<del>$1</del>');

  // Links
  formatted = formatted.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

  // Horizontal rules
  formatted = formatted.replace(/^---$/gm, '<hr class="md-hr">');
  formatted = formatted.replace(/^\*\*\*$/gm, '<hr class="md-hr">');

  // Blockquotes
  formatted = formatted.replace(/^&gt;\s*(.+)$/gm, '<blockquote>$1</blockquote>');
  formatted = formatted.replace(/<\/blockquote>\s*<blockquote>/g, '<br>');

  // Unordered lists
  formatted = formatted.replace(/^[-*+]\s+(.+)$/gm, '<li class="md-li">$1</li>');

  // Ordered lists
  formatted = formatted.replace(/^\d+\.\s+(.+)$/gm, '<li class="md-li-ordered">$1</li>');

  // Task lists
  formatted = formatted.replace(/\[ \]\s+(.+)/g, '<span class="task-item">☐ $1</span>');
  formatted = formatted.replace(/\[x\]\s+(.+)/gi, '<span class="task-item completed">☑ $1</span>');

  // Line breaks
  formatted = formatted.replace(/\n\n/g, '</p><p>');
  formatted = formatted.replace(/\n/g, '<br>');

  if (!formatted.startsWith('<') || formatted.startsWith('<br>')) {
    formatted = `<p>${formatted}</p>`;
  }

  return normalizeListMarkup(formatted);
}

function normalizeListMarkup(html) {
  const container = document.createElement('div');
  container.innerHTML = html;

  if (!container.querySelector('li.md-li, li.md-li-ordered')) {
    return html;
  }

  const children = Array.from(container.childNodes);
  const fragment = document.createDocumentFragment();
  let activeList = null;
  let mutated = false;

  const isListItem = (node) =>
    node instanceof HTMLElement
    && (node.matches('li.md-li') || node.matches('li.md-li-ordered'));

  const isBreak = (node) =>
    node instanceof HTMLElement && node.tagName === 'BR';

  const trimTrailingBreaks = (node) => {
    while (isBreak(node.lastChild)) {
      node.lastChild.remove();
      mutated = true;
    }
  };

  children.forEach((node, index) => {
    const previous = children[index - 1];
    const next = children[index + 1];

    if (node instanceof HTMLParagraphElement) {
      trimTrailingBreaks(node);
      if (!node.textContent?.trim() && (isListItem(previous) || isListItem(next))) {
        mutated = true;
        return;
      }

      activeList = null;
      fragment.appendChild(node);
      return;
    }

    if (isListItem(node)) {
      const tagName = node.matches('li.md-li-ordered') ? 'ol' : 'ul';
      if (!activeList || activeList.tagName.toLowerCase() !== tagName) {
        activeList = document.createElement(tagName);
        activeList.className = tagName === 'ol' ? 'md-ol' : 'md-ul';
        fragment.appendChild(activeList);
      }

      activeList.appendChild(node);
      mutated = true;
      return;
    }

    if (isBreak(node) && (isListItem(previous) || isListItem(next))) {
      mutated = true;
      return;
    }

    activeList = null;
    fragment.appendChild(node);
  });

  if (mutated) {
    container.replaceChildren(fragment);
  }

  return container.innerHTML;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

export default Message;
