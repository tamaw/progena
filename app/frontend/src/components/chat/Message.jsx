import { useMemo } from 'react';

function Message({ message }) {
  const { role, content, timestamp, isStreaming, error, isScheduled, scheduleId } = message;

  const formattedContent = useMemo(() => {
    if (!content) return '';
    return formatMessage(content);
  }, [content]);

  const formattedTime = useMemo(() => {
    if (!timestamp) return '';
    return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }, [timestamp]);

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
        <div
          className="message-content"
          dangerouslySetInnerHTML={{ __html: formattedContent }}
        />
        {formattedTime && (
          <div className="message-timestamp">{formattedTime}</div>
        )}
      </div>
      <MessageActions role={role} content={content} />
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

  return formatted;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

export default Message;
