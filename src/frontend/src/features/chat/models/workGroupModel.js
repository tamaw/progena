/**
 * Pure model behind the collapsible "work" lines of an assistant turn.
 *
 * A turn's timeline is split three ways:
 *   - the answer (`assistant_message` items -- one per turn, plus a terminal
 *     error row when the turn failed),
 *   - delegated-agent cards (`subagent` / `a2a_call`), which stay visible as
 *     their own lines with a short summary of their latest activity,
 *   - everything else (tool calls, reasoning, plan updates, graph steps):
 *     the turn's "work", split into groups. Every reasoning summary (a
 *     `**Heading**` section) opens a new group headed by that summary, and
 *     the work that follows lands in it. Work before any summary forms a
 *     headless group ("Generating…" / "Worked for …").
 *
 * While the agent is working, the latest group previews only its latest
 * steps (plus every still-running step, so parallel tool calls stay
 * visible); once the answer starts streaming every group is collapsed.
 */

import { isA2AToolName } from './a2aTools';
import { isReasoningEvent } from './streamEventsModel';
import { formatToolInputSummary, prettifyLabel } from './messageRenderUtils';

/** Latest steps previewed while the agent is working. */
export const LIVE_STEP_LIMIT = 3;
/** Latest lines shown on a subagent / A2A card before "N earlier". */
export const CARD_SUMMARY_LINES = 3;

const CARD_KINDS = new Set(['subagent', 'a2a_call']);
const TOOL_EVENT_TYPES = new Set(['tool_start', 'tool_result', 'tool_error']);

export function partitionTurnTimeline(timelineItems) {
  const workItems = [];
  const cardItems = [];
  const answerItems = [];
  for (const item of Array.isArray(timelineItems) ? timelineItems : []) {
    if (item?.kind === 'assistant_message') {
      answerItems.push(item);
    } else if (CARD_KINDS.has(item?.kind)) {
      cardItems.push(item);
    } else if (item) {
      workItems.push(item);
    }
  }
  return { workItems, cardItems, answerItems };
}

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

  for (const item of Array.isArray(workItems) ? workItems : []) {
    const event = item?.event;
    const eventType = typeof event?.eventType === 'string' ? event.eventType : '';
    if (!eventType || eventType === 'node_update') continue;
    const metadata = event.metadata && typeof event.metadata === 'object' ? event.metadata : {};

    if (TOOL_EVENT_TYPES.has(eventType)) {
      const toolName = typeof metadata.tool_name === 'string' && metadata.tool_name ? metadata.tool_name : 'tool';
      if (isCardOwnedTool(toolName)) continue;
      const key = typeof metadata.tool_call_id === 'string' && metadata.tool_call_id ? metadata.tool_call_id : item.id;
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
        step.detail = toolCallSummary(toolName, metadata);
      } else {
        step.status = eventType === 'tool_error' ? 'error' : 'success';
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
      const activeTodo = todos.find((todo) => todo?.status === 'in_progress');
      steps.push({
        id: `step-${item.id}`,
        kind: 'plan',
        title: 'Plan',
        detail: typeof activeTodo?.content === 'string' ? activeTodo.content : '',
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

/**
 * The one line describing a tool call: the agent's own commentary written
 * alongside the call when it has any, else a summary of its input.
 */
export function toolCallSummary(toolName, metadata) {
  const commentary = typeof metadata?.commentary === 'string' ? metadata.commentary.trim() : '';
  return commentary || formatToolInputSummary(toolName, metadata?.args_preview);
}

function toolCallKey(item) {
  const callId = item?.event?.metadata?.tool_call_id;
  return typeof callId === 'string' && callId ? callId : null;
}

/**
 * Merge each tool call's start and result/error rows into one
 * `{ kind: 'tool_call', id, start, end }` item at the call's first position,
 * so the expanded detail shows one entry per call. Rows without a call id
 * pass through unchanged.
 */
export function mergeToolCallItems(items) {
  const merged = [];
  const indexByKey = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    const eventType = item?.kind === 'stream_event' ? item.event?.eventType : '';
    const key = TOOL_EVENT_TYPES.has(eventType) ? toolCallKey(item) : null;
    if (!key) {
      merged.push(item);
      continue;
    }
    const slot = eventType === 'tool_start' ? 'start' : 'end';
    const existing = indexByKey.get(key);
    if (existing === undefined) {
      indexByKey.set(key, merged.length);
      merged.push({ kind: 'tool_call', id: `call-${key}`, start: null, end: null, [slot]: item });
    } else {
      merged[existing] = { ...merged[existing], [slot]: item };
    }
  }
  return merged;
}

/**
 * The live preview: the latest `limit` steps, plus any older step still
 * running (parallel tool calls), in original order.
 */
export function selectLiveSteps(steps, limit = LIVE_STEP_LIMIT) {
  const firstRecent = Math.max(0, steps.length - limit);
  return steps.filter((step, index) => index >= firstRecent || step.status === 'running');
}

/** Show the latest `limit` lines unless the user asked for all of them. */
export function selectRecentLines(lines, showAll, limit = CARD_SUMMARY_LINES) {
  const hiddenCount = showAll ? 0 : Math.max(0, lines.length - limit);
  return { visibleLines: lines.slice(hiddenCount), hiddenCount };
}

export function formatElapsed(ms) {
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) {
    const seconds = totalSeconds % 60;
    return seconds > 0 ? `${totalMinutes}m ${seconds}s` : `${totalMinutes}m`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
}

/**
 * Elapsed time and tool counts for one group. Elapsed runs from the group's
 * first timestamped item to `endedAt` (the next group's start, the answer's
 * start, or "now" while live), so model time after the last tool counts and
 * live and reloaded turns are measured the same way.
 */
export function summarizeWork(workItems, steps, endedAt = null) {
  const toolSteps = steps.filter((step) => step.kind === 'tool');
  const timestamps = (Array.isArray(workItems) ? workItems : [])
    .map(itemTimestamp)
    .filter((value) => value !== null);
  const end = itemTimestamp({ timestamp: endedAt });
  if (end !== null) timestamps.push(end);
  return {
    elapsedMs: timestamps.length > 1 ? Math.max(...timestamps) - Math.min(...timestamps) : 0,
    toolCount: toolSteps.length,
    failedCount: toolSteps.filter((step) => step.status === 'error').length,
    onlyReasoning: toolSteps.length === 0 && steps.length > 0 && steps.every((step) => step.kind === 'reasoning'),
  };
}

/**
 * Header text for a group: its reasoning summary, else "Generating…" while
 * live, else "Worked for 17s". `meta` carries the counters that are not
 * already in the title (seconds tick live; tool count increments).
 */
export function workGroupHeader({ heading, active, summary }) {
  const elapsed = formatElapsed(summary.elapsedMs);
  const tools = summary.toolCount > 0 ? `${summary.toolCount} tool${summary.toolCount === 1 ? '' : 's'}` : '';
  if (heading || active) {
    const showElapsed = active || summary.elapsedMs >= 1000;
    return {
      title: heading || 'Generating…',
      meta: [showElapsed ? elapsed : '', tools].filter(Boolean).join(' · '),
    };
  }
  const verb = summary.onlyReasoning ? 'Thought' : 'Worked';
  return {
    title: summary.elapsedMs >= 1000 ? `${verb} for ${elapsed}` : verb,
    meta: tools,
  };
}

// A summary heading is a bold run on its own line. Providers may stream
// summary parts back to back without a separator, so a heading may also
// directly follow sentence punctuation.
const SECTION_HEADING = /(?:^|(?<=\n)|(?<=[.!?)]))\*\*([^*\n]{1,160})\*\*[ \t]*(?=\n|$)/g;

/** A reasoning section's text without its own `**Heading**` line. */
export function reasoningSectionBody(text) {
  return (typeof text === 'string' ? text : '').replace(/^\s*\*\*[^*\n]+\*\*[ \t]*\n*/, '').trim();
}

/**
 * A headed group always opens with the reasoning section that named it; the
 * group header shows that section, so it is split off from the other rows.
 * Returns `{ reasoningItem, rest }` (reasoningItem null for headless groups).
 */
export function splitGroupReasoning(heading, items) {
  const first = Array.isArray(items) ? items[0] : null;
  if (heading && first?.kind === 'stream_event' && isReasoningEvent(first.event)) {
    return { reasoningItem: first, rest: items.slice(1) };
  }
  return { reasoningItem: null, rest: Array.isArray(items) ? items : [] };
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
 * Split work items into groups, one per reasoning summary, each keeping the
 * work that followed it. Returns `{ id, heading, startedAt, items }[]`.
 */
export function buildWorkGroups(workItems) {
  const groups = [];
  let current = null;
  const open = (heading, item) => {
    current = { id: `group-${item.id}`, heading, startedAt: itemTimestamp(item), items: [] };
    groups.push(current);
  };

  for (const item of Array.isArray(workItems) ? workItems : []) {
    const event = item?.event;
    if (item?.kind === 'stream_event' && isReasoningEvent(event) && typeof event.content === 'string' && event.content.trim()) {
      const sections = splitReasoningSections(event.content);
      sections.forEach((section, index) => {
        const sectionItem = reasoningSectionItem(item, section, index, sections.length);
        // A headless preamble belongs to whatever group is already open.
        if (section.heading || !current) open(section.heading, sectionItem);
        current.items.push(sectionItem);
      });
      continue;
    }
    if (!current) open(null, item);
    current.items.push(item);
  }
  return groups;
}
