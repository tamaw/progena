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

/**
 * Best-effort extraction of just the `summary` field out of a call_agent
 * tool result string. That string is the whole result dict, JSON-
 * stringified -- rendering it raw as a card's fallback (no progress
 * messages to tell the story instead, e.g. blocking=false, or a reloaded
 * chat with no persisted progress history) is exactly the "meaningless
 * JSON blob" experience live A2A cards exist to fix. A plain JSON.parse
 * handles the common short-result case, but the backend's own preview
 * truncation (chat_sessions.py) cuts long results off wherever the 400-char
 * budget runs out -- almost never on a clean closing brace -- so a regex
 * fallback pulls `summary` out on its own even when the overall string
 * isn't valid JSON, as long as summary itself wasn't the part truncated.
 */
export function extractCallAgentSummary(resultText) {
  if (!resultText) return '';
  try {
    const parsed = JSON.parse(resultText);
    if (typeof parsed?.summary === 'string') return parsed.summary;
  } catch {
    // fall through to regex extraction below
  }
  const match = resultText.match(/"summary"\s*:\s*"((?:[^"\\]|\\.)*)"/);
  if (!match) return '';
  try {
    return JSON.parse(`"${match[1]}"`);
  } catch {
    return '';
  }
}

/**
 * `{ label, tone }` for StatusPill (StatusPill.jsx), shared by every place
 * that renders the running/success/error vocabulary: SubagentCard's own
 * card-level status and SubagentChild's per-nested-call status (both use
 * these exact three values -- see liveAssistantTurnModel.js's item shapes)
 * -- one mapping instead of two copies of the same ternary chain.
 *
 * A2ACallCard's own card-level header deliberately doesn't use this: unlike
 * a subagent (whose only status signal is this running/success/error
 * summary), an A2A card's status progression is already fully visible one
 * level down, as its own chain of submitted/working/completed/... sub-
 * entries (A2ASubEntry) -- a StatusPill up top would just repeat the last
 * sub-entry's own label.
 */
export function statusPillProps(status) {
  if (status === 'error') return { label: 'error', tone: 'err' };
  if (status === 'success') return { label: 'done', tone: 'ok' };
  return { label: 'running', tone: 'pending' };
}
function firstStringArg(args, keys) {
  for (const key of keys) {
    const value = args?.[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function compactInputValue(value, limit = 96) {
  if (typeof value === 'string') {
    const compact = value.replace(/\s+/g, ' ').trim();
    return compact.length > limit ? `${compact.slice(0, limit - 1)}…` : compact;
  }
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return `${value.length} item${value.length === 1 ? '' : 's'}`;
  return '';
}

/**
 * Deterministically describes the intent of a normal tool invocation without
 * sending arguments through an LLM. The original args remain in metadata and
 * are still rendered in the tool detail block; this is only the terse header
 * label used by tool_start rows.
 */
export function formatToolInputSummary(toolName, args) {
  const name = typeof toolName === 'string' && toolName.trim() ? toolName.trim().toLowerCase() : 'tool';
  let input = args && typeof args === 'object' && !Array.isArray(args) ? args : {};
  if (typeof args === 'string') {
    try {
      const parsed = JSON.parse(args);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) input = parsed;
    } catch {
      return compactInputValue(args) || 'invoking…';
    }
  }
  if (name === 'task') {
    const subagentType = firstStringArg(input, ['subagent_type']);
    return subagentType ? `subagent_type: ${compactInputValue(subagentType)}` : 'invoke subagent';
  }
  const path = firstStringArg(input, ['path', 'relative_path', 'file_path', 'filename', 'directory']);

  if (name === 'read_file' || name === 'readfile' || name === 'file_read') {
    return path ? `path: ${compactInputValue(path)}` : 'read file';
  }

  if (name === 'glob' || name === 'glob_files' || name === 'search_files') {
    const pattern = firstStringArg(input, ['pattern', 'glob_pattern', 'query']);
    const parts = [];
    if (pattern) parts.push(`pattern: ${compactInputValue(pattern)}`);
    if (path) parts.push(`path: ${compactInputValue(path)}`);
    return parts.join(' • ') || 'find files';
  }

  if (name === 'ls' || name === 'list_dir' || name === 'list_directory') {
    return `path: ${compactInputValue(path || '.')}`;
  }

  if (name === 'list_agents' || name === 'rediscover_agents') {
    return 'list agents';
  }

  if (name === 'write_todos' || name === 'write_todo' || name === 'update_todos') {
    const todos = input.todos || input.tasks || input.items;
    if (Array.isArray(todos)) {
      const active = todos.find((todo) => todo && (todo.status === 'in_progress' || todo.status === 'pending'));
      const activeText = compactInputValue(active?.content || active?.title || active?.task, 72);
      return `${todos.length} todo${todos.length === 1 ? '' : 's'}${activeText ? ` • ${activeText}` : ''}`;
    }
    return 'update todos';
  }

  const isImageTool = name.includes('image') || name.includes('vision') || name.includes('draw') || name.includes('generate');
  const isSearchTool = name.includes('search') || name.includes('query');
  const query = firstStringArg(input, ['query', 'q', 'search_query', 'prompt', 'text', 'request', 'description']);
  if (query && (isImageTool || isSearchTool)) {
    return `${isImageTool ? 'prompt' : 'query'}: ${compactInputValue(query)}`;
  }

  const keys = Object.keys(input)
    .filter((key) => !/token|secret|password|api[_-]?key|authorization/i.test(key))
    .sort()
    .slice(0, 3);
  const generic = keys.map((key) => {
    const value = compactInputValue(input[key], 64);
    return value ? `${key}: ${value}` : `${key}: ${Array.isArray(input[key]) ? `${input[key].length} items` : 'set'}`;
  });
  return generic.join(' • ') || 'invoking…';
}

function artifactSummary(metadata) {
  const artifact = metadata?.artifact;
  return artifact && typeof artifact === 'object' && !Array.isArray(artifact)
    && typeof artifact.summary === 'string' && artifact.summary.trim()
    ? artifact.summary.trim()
    : '';
}

function todoItemCount(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  const raw = value.trim();
  try {
    const parsed = JSON.parse(raw);
    const items = Array.isArray(parsed)
      ? parsed
      : (parsed && typeof parsed === 'object' && (parsed.todos || parsed.tasks || parsed.items));
    return Array.isArray(items) ? items.length : null;
  } catch {
    // Some tool adapters stringify Python-style lists with single quotes.
    // Count item objects without displaying that serialized representation.
    const matches = raw.match(/\{\s*["'](?:content|title|task)["']\s*:/g);
    return matches && matches.length > 0 ? matches.length : null;
  }
}

function structuredResultSummary(raw, toolName) {
  if (!raw || typeof raw !== 'string') return '';
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    if (!raw.startsWith('{') && !raw.startsWith('[')) return '';
    if (toolName === 'list_agents') {
      return 'Listed available agents';
    }
    return 'Returned structured data';
  }
  if (toolName === 'list_agents' && parsed && Array.isArray(parsed.agents)) {
    const count = parsed.agents.length;
    return `Found ${count} agent${count === 1 ? '' : 's'}`;
  }
  if (Array.isArray(parsed)) {
    return `Returned ${parsed.length} item${parsed.length === 1 ? '' : 's'}`;
  }
  if (parsed && typeof parsed === 'object') {
    const keys = Object.keys(parsed);
    return keys.length > 0
      ? `Returned structured data · ${keys.length} field${keys.length === 1 ? '' : 's'}`
      : 'Completed with no structured output';
  }
  return '';
}
function toolOutputLabel(metadata, fallbackKey, toolName = '') {
  const artifact = artifactSummary(metadata);
  if (artifact) return artifact;
  const fallback = typeof metadata?.[fallbackKey] === 'string' ? metadata[fallbackKey].trim() : '';
  if (toolName === 'write_todos' && fallbackKey === 'result_preview') {
    const count = todoItemCount(fallback);
    if (count !== null) return `Updated todo list · ${count} item${count === 1 ? '' : 's'}`;
  }
  return structuredResultSummary(fallback, toolName) || fallback;
}

function isNormalTool(toolName) {
  return Boolean(toolName);
}


export function formatArgsPreview(args) {
  if (args === null || args === undefined) return '';
  if (typeof args === 'string') return args;
  try {
    const json = JSON.stringify(args);
    return json.length > 240 ? `${json.slice(0, 237)}…` : json;
  } catch {
    return String(args);
  }
}

export function formatDuration(ms) {
  if (!Number.isFinite(ms)) return '';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

export function formatTimestamp(value) {
  const timestamp = Number.isFinite(value)
    ? value
    : (typeof value === 'string' ? Date.parse(value) : NaN);
  if (!Number.isFinite(timestamp)) return '';
  return new Date(timestamp).toLocaleTimeString([], {
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
    return {
      badge: 'Tool',
      title: `→ ${prettifyLabel(toolName)}`,
      metaDetail: isNormalTool(toolName)
        ? ''
        : (formatArgsPreview(metadata.args_preview) || 'invoking…'),
      bodyDetail: '',
    };
  }

  if (eventType === 'tool_result') {
    const toolName = typeof metadata.tool_name === 'string' ? metadata.tool_name : 'tool';
    const duration = Number.isFinite(metadata.duration_ms) ? formatDuration(metadata.duration_ms) : '';
    const normalTool = isNormalTool(toolName);
    const output = normalTool
      ? ''
      : (typeof metadata.result_preview === 'string' ? metadata.result_preview.trim() : '');
    return {
      badge: 'Tool',
      title: `✓ ${prettifyLabel(toolName)}`,
      metaDetail: normalTool ? duration : [duration, output].filter(Boolean).join(' • ') || 'completed',
      bodyDetail: '',
    };
  }

  if (eventType === 'tool_error') {
    const toolName = typeof metadata.tool_name === 'string' ? metadata.tool_name : 'tool';
    const duration = Number.isFinite(metadata.duration_ms) ? formatDuration(metadata.duration_ms) : '';
    const normalTool = isNormalTool(toolName);
    const output = normalTool
      ? ''
      : (typeof metadata.error === 'string' ? metadata.error.trim() : '');
    return {
      badge: 'Tool',
      title: `✗ ${prettifyLabel(toolName)}`,
      metaDetail: normalTool ? duration : [duration, output].filter(Boolean).join(' • ') || 'error',
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
    // Todos carry no plan-level name of their own (write_todos items only
    // have content/status) -- the in_progress item is the closest thing to a
    // label for "what this plan update is about", falling back to the next
    // pending item, then the last item once everything is done.
    const activeTodo = todos.find((todo) => todo?.status === 'in_progress')
      || todos.find((todo) => todo?.status === 'pending')
      || todos[todos.length - 1];
    return {
      badge: 'Plan',
      title: activeTodo?.content || 'Plan',
      metaDetail: todos.length > 0 ? `${completed}/${todos.length} done` : '',
      bodyDetail: '',
    };
  }

  // Flat subagent_* events only reach this generic renderer via the reload
  // fallback (buildDefaultAssistantTimeline groups them into a single card
  // when it can -- see liveAssistantTurnModel.js) or chats persisted before
  // grouping shipped. Handled explicitly, setting badge: 'Subagent' directly,
  // rather than falling into badgeForEventType()'s generic fallback below --
  // "subagent_start" etc. accidentally contain the substring "agent" and
  // would otherwise be mislabeled "Agent".
  if (eventType.startsWith('subagent_')) {
    const subagentName = typeof metadata.subagent_name === 'string' && metadata.subagent_name
      ? metadata.subagent_name
      : 'subagent';

    if (eventType === 'subagent_start') {
      return { badge: 'Subagent', title: `→ ${subagentName}`, metaDetail: 'started', bodyDetail: '' };
    }

    if (eventType === 'subagent_result') {
      const isError = metadata.success === false;
      const parts = [];
      if (typeof metadata.duration_ms === 'number') parts.push(formatDuration(metadata.duration_ms));
      if (isError && typeof metadata.error === 'string' && metadata.error) {
        parts.push(metadata.error);
      } else if (typeof metadata.result_preview === 'string' && metadata.result_preview) {
        parts.push(metadata.result_preview);
      }
      return {
        badge: 'Subagent',
        title: `${isError ? '✗' : '✓'} ${subagentName}`,
        metaDetail: parts.join(' • ') || (isError ? 'error' : 'completed'),
        bodyDetail: '',
      };
    }

    if (eventType === 'subagent_message_delta') {
      return {
        badge: 'Subagent',
        title: subagentName,
        bodyHtml: typeof event?.content === 'string' && event.content ? formatMessage(event.content) : '',
        metaDetail: '',
        bodyDetail: '',
      };
    }

    const nestedToolName = typeof metadata.nested_tool_name === 'string' && metadata.nested_tool_name
      ? metadata.nested_tool_name
      : 'tool';

    if (eventType === 'subagent_tool_start') {
      return {
        badge: 'Subagent',
        title: `${subagentName} → ${prettifyLabel(nestedToolName)}`,
        metaDetail: formatArgsPreview(metadata.args_preview) || 'invoking…',
        bodyDetail: '',
      };
    }

    if (eventType === 'subagent_tool_result') {
      const parts = [];
      if (typeof metadata.duration_ms === 'number') parts.push(formatDuration(metadata.duration_ms));
      if (typeof metadata.result_preview === 'string' && metadata.result_preview) parts.push(metadata.result_preview);
      return {
        badge: 'Subagent',
        title: `${subagentName} ✓ ${prettifyLabel(nestedToolName)}`,
        metaDetail: parts.join(' • ') || 'completed',
        bodyDetail: '',
      };
    }

    // subagent_tool_error
    const errorParts = [];
    if (typeof metadata.duration_ms === 'number') errorParts.push(formatDuration(metadata.duration_ms));
    if (typeof metadata.error === 'string' && metadata.error) errorParts.push(metadata.error);
    return {
      badge: 'Subagent',
      title: `${subagentName} ✗ ${prettifyLabel(nestedToolName)}`,
      metaDetail: errorParts.join(' • ') || 'error',
      bodyDetail: '',
    };
  }

  // a2a_call_progress only reaches this generic renderer via the reload
  // fallback (buildDefaultAssistantTimeline groups it into the a2a_call card
  // when it can -- see liveAssistantTurnModel.js) or chats persisted before
  // grouping shipped. call_agent's own tool_start/tool_result/tool_error
  // get no equivalent special-casing here -- Progenitor's dispatch/return
  // for that call is an ordinary tool call and is handled by the plain
  // tool_start/tool_result/tool_error branches above like any other tool,
  // with the same 'Tool' badge; only a2a_call_progress is genuinely new
  // information (the A2A protocol's own status stream) with no tool-call
  // equivalent, so it alone gets its own badge.
  if (eventType === 'a2a_call_progress') {
    const agentName = typeof metadata.agent_name === 'string' && metadata.agent_name
      ? metadata.agent_name
      : 'agent';
    return {
      badge: 'A2A',
      title: agentName,
      metaDetail: typeof metadata.state === 'string' ? metadata.state : '',
      bodyDetail: typeof metadata.message_text === 'string' ? metadata.message_text : '',
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
  // subagent_tool_* events use nested_tool_name/nested_tool_call_id, not
  // tool_name/tool_call_id -- rendering them through this tool_name-keyed box
  // would silently produce an empty/generic one. describeStreamEvent's own
  // subagent_* branch already renders a sensible title/metaDetail for them.
  if (!eventType.includes('tool') || eventType.startsWith('subagent_')) return null;

  const metadata = event?.metadata && typeof event.metadata === 'object' ? event.metadata : {};
  const toolName = typeof metadata.tool_name === 'string' ? metadata.tool_name : 'tool';
  const isNormal = isNormalTool(toolName);
  const args = formatArgsPreview(metadata.args_preview);
  const isError = eventType === 'tool_error';
  const result = typeof metadata.result_preview === 'string' ? metadata.result_preview.trim() : '';
  const error = typeof metadata.error === 'string' ? metadata.error.trim() : '';
  const duration = typeof metadata.duration_ms === 'number' ? formatDuration(metadata.duration_ms) : '';
  const summary = eventType === 'tool_start'
    ? (isNormal ? formatToolInputSummary(toolName, metadata.args_preview) : '')
    : (isNormal
      ? (toolOutputLabel(metadata, isError ? 'error' : 'result_preview', toolName) || (isError ? 'error' : 'completed'))
      : '');

  return {
    kind: toolName,
    desc: description.title.replace(/^[→✓✗]\s*/, ''),
    status: isError ? 'error' : eventType === 'tool_result' ? 'complete' : 'running',
    statusTone: isError ? 'err' : 'ok',
    summary,
    duration: isNormal ? duration : '',
    command: args,
    body: isNormal ? (isError ? error : result) : [duration, isError ? error : result].filter(Boolean).join('\n'),
    isNormal,
  };
}
