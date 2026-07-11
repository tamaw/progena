/**
 * Pure rendering utilities shared by Message.jsx and TimelineItem.jsx.
 *
 * All functions here are side-effect-free helpers; the only runtime
 * dependencies are the markdown renderer and the browser's DOM API
 * (for HTML sanitization).
 */

import { renderMarkdownToHtml } from '../../lib/markdown';
import { hasReasoningContent, isReasoningEvent } from './streamEventsModel';

// ─── Markdown / HTML ──────────────────────────────────────────────────────

export function formatMessage(content) {
  return renderMarkdownToHtml(content);
}

const ALLOWED_TAGS = new Set([
  'p', 'br', 'b', 'i', 'em', 'strong', 'code', 'pre', 'blockquote',
  'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr', 'span', 'div',
  'del', 'ins', 'sup', 'sub',
]);
const ALLOWED_ATTRS = new Set(['href', 'title', 'class', 'id', 'target', 'rel']);

function isSafeHref(rawValue) {
  const colonIdx = rawValue.trim().indexOf(':');
  if (colonIdx === -1) return true; // relative URL, no scheme
  // Validate scheme portion strictly — any non-alpha char (including control chars
  // browsers normalize away) makes this an unrecognised / potentially obfuscated scheme.
  const schemePart = rawValue.trim().slice(0, colonIdx);
  if (!/^[a-zA-Z][a-zA-Z0-9+.-]*$/.test(schemePart)) return false;
  const scheme = schemePart.toLowerCase();
  return scheme === 'http' || scheme === 'https' || scheme === 'mailto';
}

function sanitizeNode(node) {
  for (const child of Array.from(node.childNodes)) {
    if (child.nodeType === Node.ELEMENT_NODE) {
      const tag = child.tagName.toLowerCase();
      if (!ALLOWED_TAGS.has(tag)) {
        child.replaceWith(document.createTextNode(child.textContent || ''));
        continue;
      }
      for (const attr of Array.from(child.attributes)) {
        const name = attr.name.toLowerCase();
        if (!ALLOWED_ATTRS.has(name) || (name === 'href' && !isSafeHref(attr.value))) {
          child.removeAttribute(attr.name);
        }
      }
      sanitizeNode(child);
    }
  }
}

export function sanitizeHtmlFragment(html) {
  if (!html) return '';
  const template = document.createElement('template');
  template.innerHTML = html;
  sanitizeNode(template.content);
  const div = document.createElement('div');
  div.appendChild(template.content.cloneNode(true));
  return div.innerHTML;
}

// Splices a blinking cursor span onto the very end of already-sanitized
// message HTML, rather than relying on a container-level `::after` (which
// lands on its own line after the last block element -- e.g. after a </p>
// or </li> -- instead of glued to the last character). Walks to the
// deepest last element (last <li>, last table cell, last line of a code
// block, ...) so the cursor tracks wherever the streamed text actually
// ends, then appends it there as a real trailing node.
export function appendStreamingCursor(html) {
  const cursor = document.createElement('span');
  cursor.className = 'stream-cursor';
  cursor.textContent = '▋';

  const template = document.createElement('template');
  template.innerHTML = html || '';

  let host = template.content;
  while (host.lastElementChild) {
    host = host.lastElementChild;
  }
  host.appendChild(cursor);

  const div = document.createElement('div');
  div.appendChild(template.content.cloneNode(true));
  return div.innerHTML;
}

// ─── Label / display helpers ─────────────────────────────────────────────

function prettifyLabel(value) {
  return value
    .split(/[_-]/g)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
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

export function formatTimestamp(value) {
  if (!Number.isFinite(value)) return '';
  return new Date(value).toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

function summarizeMetadata(metadata) {
  const summaryParts = [];
  if (Array.isArray(metadata.output_keys) && metadata.output_keys.length > 0) {
    summaryParts.push(`outputs: ${metadata.output_keys.join(', ')}`);
  }
  if (typeof metadata.text_length === 'number' && metadata.text_length > 0) {
    summaryParts.push(`${metadata.text_length} chars`);
  }
  if (typeof metadata.stream_metadata?.langgraph_node === 'string') {
    summaryParts.push(`node: ${metadata.stream_metadata.langgraph_node}`);
  }
  return summaryParts.join(' • ');
}

function normalizeDetailText(value) {
  return typeof value === 'string'
    ? value.replace(/\s+/g, ' ').trim().toLowerCase()
    : '';
}

function detailsMatch(left, right) {
  return normalizeDetailText(left) === normalizeDetailText(right);
}

function badgeForEventType(eventType) {
  if (eventType.includes('tool')) return 'Tool';
  if (eventType.includes('agent')) return 'Agent';
  if (eventType.includes('node')) return 'Step';
  if (eventType.includes('reasoning')) return 'Reasoning';
  return 'Event';
}

// ─── Stream event rendering ───────────────────────────────────────────────

export function describeStreamEvent(event) {
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
      // The "Step" tag already labels this as a graph node execution, and
      // the title already carries the node's own name, so there's nothing
      // meaningful left to put in metaDetail (it used to leak a stray
      // "Model"/"Step" kind label here, which just repeated the tag).
      badge: 'Step',
      title: prettifyLabel(nodeName),
      metaDetail: '',
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
      metaDetail: '',
      bodyDetail: '',
    };
  }

  if (eventType === 'todos_update') {
    const todos = Array.isArray(metadata.todos) ? metadata.todos : [];
    const completed = todos.filter((todo) => todo?.status === 'completed').length;
    return {
      badge: 'Plan',
      title: 'Plan',
      metaDetail: todos.length > 0 ? `${completed}/${todos.length} done` : '',
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

export function tokenMetaItemsForEvent(event) {
  const metadata = event?.metadata && typeof event.metadata === 'object' ? event.metadata : {};
  const items = [];

  if (isReasoningEvent(event)) {
    if (typeof metadata.reasoning_tokens === 'number' && metadata.reasoning_tokens > 0) {
      const count = new Intl.NumberFormat().format(metadata.reasoning_tokens);
      items.push(`${count} reasoning token${metadata.reasoning_tokens === 1 ? '' : 's'}`);
    } else {
      const contentLen = typeof event?.content === 'string' ? event.content.trim().length : 0;
      const textLen = contentLen > 0
        ? contentLen
        : (typeof metadata.text_length === 'number' ? metadata.text_length : 0);
      if (textLen > 0) {
        const estimated = Math.round(textLen / 4);
        items.push(`~${new Intl.NumberFormat().format(estimated)} tokens`);
      }
    }
  }

  return items;
}

export function badgeForEvent(eventType) {
  if (eventType === 'tool_error') {
    return { className: 'entry__badge entry__badge--err', label: '!' };
  }
  if (eventType === 'todos_update') {
    return { className: 'entry__badge entry__badge--plan', label: '☰' };
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

export function tagClassForEventType(eventType) {
  if (eventType === 'tool_error') return 'entry__tag entry__tag--err';
  if (eventType === 'todos_update') return 'entry__tag chat-event-tag--plan';
  if (eventType.includes('tool')) return 'entry__tag chat-event-tag--tool';
  if (eventType.includes('reasoning')) return 'entry__tag chat-event-tag--reason';
  if (eventType.includes('node') || eventType.includes('agent')) return 'entry__tag chat-event-tag--step';
  return 'entry__tag chat-event-tag--step';
}

export function todosDetailsForEvent(event) {
  const eventType = typeof event?.eventType === 'string' ? event.eventType : '';
  if (eventType !== 'todos_update') return null;
  const metadata = event?.metadata && typeof event.metadata === 'object' ? event.metadata : {};
  return Array.isArray(metadata.todos) ? metadata.todos : [];
}

export function toolDetailsForEvent(event, description) {
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
