/**
 * Work stretches between actual root-agent messages. Short reasoning
 * summaries update one live label; tools, delegated cards and substantive
 * reasoning remain chronological activity inside the stretch.
 */

import { isA2AToolName } from './a2aTools';
import { isToolEventFailure } from './toolFailureModel';
import { isReasoningEvent } from './streamEventsModel';
import { formatToolInputSummary, prettifyLabel } from './messageRenderUtils';

/** Latest lines shown on a subagent / A2A card before "N earlier". */
export const CARD_SUMMARY_LINES = 3;

const CARD_KINDS = new Set(['subagent', 'async_subagent', 'a2a_call']);
const TOOL_EVENT_TYPES = new Set(['tool_start', 'tool_result', 'tool_error']);


// Reasoning rows keep their id while their event type changes (delta ->
// summary); keying on both remounts the row so its disclosure resets.
export function timelineItemKey(item, index) {
  if (item?.kind === 'stream_event' && isReasoningEvent(item.event)) {
    return `${item.id}-${item.event.eventType}`;
  }
  return item?.id || `tl-${index}`;
}

// `task` (subagent dispatch) and A2A tool calls are represented by their own
// cards, so their plain lifecycle rows are not counted as separate steps.
function isCardOwnedTool(toolName) {
  return toolName === 'task' || isA2AToolName(toolName);
}

// `todos_update` has no call id. Match it to the latest correlated
// write_todos lifecycle exactly as mergeToolCallItems does, so the compact
// preview hides only the lifecycle which the visible plan actually replaces.
function writeTodosCallsWithPlans(items) {
  const calls = [];
  const callIndexByKey = new Map();

  for (const item of Array.isArray(items) ? items : []) {
    const eventType = item?.kind === 'stream_event' ? item.event?.eventType : '';
    if (eventType === 'todos_update') {
      const owner = calls.findLastIndex((call) => !call.hasPlan);
      if (owner >= 0) calls[owner].hasPlan = true;
      continue;
    }
    if (!TOOL_EVENT_TYPES.has(eventType) || toolCallNameFromItem(item) !== 'write_todos') continue;

    const key = toolCallKey(item);
    if (!key) continue;
    let callIndex = callIndexByKey.get(key);
    if (callIndex === undefined) {
      callIndex = calls.length;
      callIndexByKey.set(key, callIndex);
      calls.push({ key, hasPlan: false, failed: false });
    }
    if (isToolEventFailure(item.event)) calls[callIndex].failed = true;
  }

  return new Set(calls.filter((call) => call.hasPlan && !call.failed).map((call) => call.key));
}

function toolCallNameFromItem(item) {
  return item?.event?.metadata?.tool_name;
}

function itemTimestamp(item) {
  const value = item?.timestamp ?? item?.event?.timestamp;
  const parsed = Number.isFinite(value) ? value : Date.parse(value || '');
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Collapse work items into user-facing steps: one step per tool call
 * (start + result/error merged by tool_call_id), one per reasoning block or
 * plan update. Graph `node_update` steps only appear in the expanded detail.
 */
export function buildWorkSteps(workItems) {
  const steps = [];
  const toolStepIndex = new Map();
  const writeTodosWithPlans = writeTodosCallsWithPlans(workItems);

  for (const item of Array.isArray(workItems) ? workItems : []) {
    if (CARD_KINDS.has(item?.kind)) {
      steps.push({
        id: `step-${item.id}`, kind: 'tool',
        title: item.subagentName || item.agentName || 'Agent',
        status: item.status === 'error' ? 'error' : item.status === 'running' ? 'running' : 'success',
        timestamp: itemTimestamp(item), durationMs: item.durationMs ?? null,
      });
      continue;
    }
    const event = item?.event;
    const eventType = typeof event?.eventType === 'string' ? event.eventType : '';
    if (!eventType || eventType === 'node_update') continue;
    const metadata = event.metadata && typeof event.metadata === 'object' ? event.metadata : {};

    if (TOOL_EVENT_TYPES.has(eventType)) {
      const toolName = typeof metadata.tool_name === 'string' && metadata.tool_name ? metadata.tool_name : 'tool';
      const toolCallId = typeof metadata.tool_call_id === 'string' && metadata.tool_call_id ? metadata.tool_call_id : null;
      if (isCardOwnedTool(toolName) || (toolName === 'write_todos' && toolCallId && writeTodosWithPlans.has(toolCallId))) continue;
      const key = toolCallId || item.id;
      const existingIndex = toolStepIndex.get(key);
      const step = existingIndex === undefined
        ? {
          id: `step-${key}`,
          kind: 'tool',
          title: prettifyLabel(toolName),
          detail: '',
          status: 'running',
          durationMs: null,
          timestamp: itemTimestamp(item),
        }
        : { ...steps[existingIndex] };

      if (eventType === 'tool_start') {
        step.detail = formatToolInputSummary(toolName, metadata.args_preview);
      } else {
        step.status = isToolEventFailure(event) ? 'error' : 'success';
        if (Number.isFinite(metadata.duration_ms)) step.durationMs = metadata.duration_ms;
      }

      if (existingIndex === undefined) {
        toolStepIndex.set(key, steps.length);
        steps.push(step);
      } else {
        steps[existingIndex] = step;
      }
      continue;
    }

    if (isReasoningEvent(event)) {
      steps.push({
        id: `step-${item.id}`,
        kind: 'reasoning',
        title: 'Reasoning',
        detail: '',
        status: 'success',
        durationMs: null,
        timestamp: itemTimestamp(item),
      });
      continue;
    }

    if (eventType === 'todos_update') {
      const todos = Array.isArray(metadata.todos) ? metadata.todos : [];
      const current = currentTodo(todos);
      steps.push({
        id: `step-${item.id}`,
        kind: 'plan',
        title: 'Todo',
        detail: typeof current?.content === 'string' ? current.content : '',
        status: 'success',
        durationMs: null,
        timestamp: itemTimestamp(item),
      });
      continue;
    }

    steps.push({
      id: `step-${item.id}`,
      kind: 'other',
      title: prettifyLabel(eventType),
      detail: '',
      status: 'success',
      durationMs: null,
      timestamp: itemTimestamp(item),
    });
  }

  return steps;
}

function toolCallKey(item) {
  const callId = item?.event?.metadata?.tool_call_id;
  return typeof callId === 'string' && callId ? callId : null;
}

/** The plan item being worked on: in progress, else the next pending, else the last. */
export function currentTodo(todos) {
  const list = Array.isArray(todos) ? todos : [];
  return list.find((todo) => todo?.status === 'in_progress')
    || list.find((todo) => todo?.status !== 'completed')
    || list[list.length - 1]
    || null;
}

export function todoProgress(todos) {
  const list = Array.isArray(todos) ? todos : [];
  const completed = list.filter((todo) => todo?.status === 'completed').length;
  return { completed, total: list.length, percent: list.length ? Math.round((completed / list.length) * 100) : 0 };
}

/**
 * Merge each tool call's start and result/error rows into one
 * `{ kind: 'tool_call', id, start, end, plan }` item at the call's first
 * position, so the expanded detail shows one entry per call. A plan update
 * (`todos_update`) carries no call id; it is the output of the latest
 * write_todos call that has none yet, so it becomes that call's `plan`.
 * Other rows pass through unchanged.
 *
 * Calls in `hiddenToolCallIds` are represented by their delegated-agent card.
 */
export function mergeToolCallItems(items, { hiddenToolCallIds } = {}) {
  const merged = [];
  const indexByKey = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    const eventType = item?.kind === 'stream_event' ? item.event?.eventType : '';
    if (eventType === 'todos_update') {
      const owner = merged.findLastIndex((entry) => (
        entry.kind === 'tool_call' && !entry.plan && toolCallName(entry) === 'write_todos'
      ));
      if (owner >= 0) {
        merged[owner] = { ...merged[owner], plan: item };
        continue;
      }
    }
    const key = TOOL_EVENT_TYPES.has(eventType) ? toolCallKey(item) : null;
    if (!key) {
      merged.push(item);
      continue;
    }
    const slot = eventType === 'tool_start' ? 'start' : 'end';
    const existing = indexByKey.get(key);
    if (existing === undefined) {
      indexByKey.set(key, merged.length);
      merged.push({ kind: 'tool_call', id: `call-${key}`, key, start: null, end: null, plan: null, [slot]: item });
    } else {
      merged[existing] = { ...merged[existing], [slot]: item };
    }
  }
  return merged.flatMap((entry) => {
    if (entry.kind !== 'tool_call') return [entry];
    const { key, ...call } = entry;
    return hiddenToolCallIds?.has(key) ? [] : [call];
  });
}


function toolCallName(entry) {
  return (entry.start || entry.end)?.event?.metadata?.tool_name;
}


/** Show the latest `limit` lines unless the user asked for all of them. */
export function selectRecentLines(lines, showAll, limit = CARD_SUMMARY_LINES) {
  const hiddenCount = showAll ? 0 : Math.max(0, lines.length - limit);
  return { visibleLines: lines.slice(hiddenCount), hiddenCount };
}

export function formatElapsed(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  if (minutes < 60) return `${minutes}m ${totalSeconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/**
 * Elapsed time and tool counts for one group. `endedAt` is an authoritative
 * phase boundary: a tool result can arrive after the next reasoning section,
 * but it still belongs to the group that started the call and must not extend
 * that earlier phase's duration.
 */
export function summarizeWork(workItems, steps, endedAt = null, startedAt = null) {
  const toolSteps = steps.filter((step) => step.kind === 'tool');
  const timestamps = (Array.isArray(workItems) ? workItems : [])
    .map(itemTimestamp)
    .filter((value) => value !== null);
  const end = itemTimestamp({ timestamp: endedAt });
  const start = itemTimestamp({ timestamp: startedAt }) ?? (timestamps.length > 0 ? Math.min(...timestamps) : end);
  const finish = end ?? (timestamps.length > 0 ? Math.max(...timestamps) : null);
  return {
    elapsedMs: start !== null && finish !== null ? Math.max(0, finish - start) : 0,
    toolCount: toolSteps.length,
    failedCount: toolSteps.filter((step) => step.status === 'error').length,
    onlyReasoning: toolSteps.length === 0 && steps.length > 0 && steps.every((step) => step.kind === 'reasoning'),
  };
}

/**
 * Header text for a group. A reasoning title replaces the generic title
 * verbatim. The initial non-reasoning group remains a stable Working line
 * while active, then reports elapsed work once an end boundary is known.
 */
export function workGroupHeader({ heading, active, summary }) {
  const elapsed = formatElapsed(summary.elapsedMs);
  const tools = summary.toolCount > 0 ? `${summary.toolCount} tool${summary.toolCount === 1 ? '' : 's'}` : '';
  return active
    ? { title: heading || 'Working…', meta: [elapsed, tools].filter(Boolean).join(' · ') }
    : { title: `Worked for ${elapsed}`, meta: tools };
}


// A summary heading is a bold run on its own line. Providers may stream
// summary parts back to back without a separator, so a heading may also
// directly follow sentence punctuation.
const SECTION_HEADING = /(?:^|(?<=\n)|(?<=[.!?)]))\*\*([^*\n]{1,160})\*\*[ \t]*(?=\n|$)/g;

/** A reasoning section's text without its own `**Heading**` line. */
export function reasoningSectionBody(text) {
  return (typeof text === 'string' ? text : '').replace(/^\s*\*\*[^*\n]+\*\*[ \t]*\n*/, '').trim();
}


/** Split reasoning text into `{ heading, text }` sections (heading null for a preamble). */
export function splitReasoningSections(text) {
  const source = typeof text === 'string' ? text : '';
  const matches = [...source.matchAll(SECTION_HEADING)];
  if (matches.length === 0) return source.trim() ? [{ heading: null, text: source }] : [];
  const sections = [];
  const preamble = source.slice(0, matches[0].index);
  if (preamble.trim()) sections.push({ heading: null, text: preamble });
  matches.forEach((match, index) => {
    const end = index + 1 < matches.length ? matches[index + 1].index : source.length;
    sections.push({ heading: match[1].trim(), text: source.slice(match.index, end) });
  });
  return sections;
}

function reasoningSectionItem(item, section, index, sectionCount) {
  const id = `${item.id}-s${index}`;
  const metadata = { ...(item.event.metadata || {}) };
  // A turn-level token count belongs to no single section once split.
  if (sectionCount > 1) delete metadata.reasoning_tokens;
  return { ...item, id, event: { ...item.event, id, content: section.text, metadata } };
}

/**
 * Only actual root text closes a work stretch. Parallel lifecycle results
 * remain with the call's initiating stretch, even after a progress reply.
 */
export function buildWorkGroups(timelineItems) {
  const rows = [];
  const ownerByCall = new Map();
  let current = null;
  for (const item of Array.isArray(timelineItems) ? timelineItems : []) {
    if (item?.kind === 'assistant_message') {
      if (current) current.endedAt = itemTimestamp(item);
      rows.push(item);
      current = null;
      continue;
    }
    if (!item) continue;
    const eventType = item.event?.eventType;
    const callId = toolCallKey(item);
    const owner = callId && eventType !== 'tool_start' ? ownerByCall.get(callId) : null;
    if (owner) {
      owner.items.push(item);
      continue;
    }
    if (!current) {
      current = {
        kind: 'work_group', id: `group-${item.id}`, heading: null,
        startedAt: itemTimestamp(item), endedAt: null, items: [],
      };
      rows.push(current);
    }
    if (isReasoningEvent(item.event)) {
      const text = item.event.content || item.event.metadata?.summary || '';
      const sections = splitReasoningSections(text);
      if (sections.length === 0) current.items.push(item);
      for (const [index, section] of sections.entries()) {
        if (section.heading) current.heading = section.heading;
        current.items.push(reasoningSectionItem(item, section, index, sections.length));
      }
    } else {
      current.items.push(item);
    }
    if (callId && eventType === 'tool_start') ownerByCall.set(callId, current);
  }
  return rows;
}

/** Heading-only reasoning belongs in the status, not in the activity list. */
export function hasReasoningBody(event) {
  return Boolean(reasoningSectionBody(event?.content)
    || reasoningSectionBody(event?.metadata?.summary)
    || (typeof event?.metadata?.detail === 'string' && event.metadata.detail.trim()));
}

export function workActivityItems(items) {
  const hiddenToolCallIds = new Set(items
    .filter((item) => CARD_KINDS.has(item?.kind) && item.toolCallId)
    .map((item) => item.toolCallId));
  return mergeToolCallItems(items, { hiddenToolCallIds }).filter((item) => (
    item.event?.eventType !== 'node_update'
    && (!isReasoningEvent(item.event) || hasReasoningBody(item.event))
  ));
}

export function isWorkItemRunning(item) {
  if (item.kind === 'tool_call') return !item.end;
  return CARD_KINDS.has(item.kind) && item.status === 'running';
}

/** Keep every active invocation, alongside a bounded tail of finished items. */
export function selectWorkActivity(items, showAll, limit = 5) {
  if (showAll) return { visibleItems: items, hiddenCount: 0 };
  let completedToSkip = Math.max(0, items.filter((item) => !isWorkItemRunning(item)).length - limit);
  const visibleItems = items.filter((item) => {
    if (isWorkItemRunning(item) || completedToSkip === 0) return true;
    completedToSkip -= 1;
    return false;
  });
  return { visibleItems, hiddenCount: items.length - visibleItems.length };
}
