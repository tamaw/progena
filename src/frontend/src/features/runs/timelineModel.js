const TERMINAL_STATUSES = new Set([
  'completed', 'failed', 'cancelled', 'stopped', 'error', 'interrupted',
]);

function timestamp(value) {
  if (value instanceof Date) return value.getTime();
  if (typeof value === 'number') return Number.isFinite(value) ? value : NaN;
  if (typeof value !== 'string' || !value.trim()) return NaN;

  const numeric = Number(value);
  if (Number.isFinite(numeric) && /^[-+]?\d+(?:\.\d+)?$/.test(value.trim())) return numeric;
  return Date.parse(value);
}

function clampPercent(value) {
  return Math.min(100, Math.max(0, value));
}

function hasUnavailableTiming(span) {
  return span?.timing_source === 'unavailable';
}

function isLiveTrace(trace) {
  return !TERMINAL_STATUSES.has(String(trace?.status || '').toLowerCase());
}

function spanStart(span) {
  return timestamp(span?.start);
}

function spanEnd(span) {
  return timestamp(span?.end);
}

function rowName(span) {
  return span.name || span.operation || 'Unnamed span';
}

function normalizedKind(span) {
  return String(span?.kind || 'span').toLowerCase();
}

function compareChronologically(left, right) {
  const leftStart = spanStart(left.span);
  const rightStart = spanStart(right.span);
  const leftTime = Number.isFinite(leftStart) ? leftStart : Infinity;
  const rightTime = Number.isFinite(rightStart) ? rightStart : Infinity;
  return leftTime - rightTime || left.index - right.index;
}

function laneStart(lane) {
  return lane.spans.reduce((earliest, node) => {
    const start = spanStart(node.span);
    return Number.isFinite(start) ? Math.min(earliest, start) : earliest;
  }, Infinity);
}

function canPack(span) {
  const kind = normalizedKind(span);
  return kind === 'model' || kind === 'tool';
}

/**
 * Returns the time range used to place trace bars. The returned duration can be
 * zero for a completed trace with no elapsed timing; callers should not turn it
 * into a made-up duration.
 */
export function traceBounds(trace, now = Date.now()) {
  const currentTime = timestamp(now);
  const fallback = Number.isFinite(currentTime) ? currentTime : Date.now();
  const spans = Array.isArray(trace?.spans) ? trace.spans : [];
  const starts = [timestamp(trace?.started_at), ...spans.map(spanStart)].filter(Number.isFinite);
  const ends = [timestamp(trace?.completed_at), ...spans.map(spanEnd)].filter(Number.isFinite);
  const isLive = isLiveTrace(trace);
  const start = starts.length ? Math.min(...starts) : ends.length ? Math.min(...ends) : fallback;
  const endCandidates = [...ends, start];
  if (isLive) endCandidates.push(fallback);
  const end = Math.max(...endCandidates);

  return { start, end, duration: end - start, isLive };
}

/**
 * Converts one span into percentages within bounds. Incomplete or unavailable
 * timing is represented by a zero-width marker instead of an estimated bar.
 */
export function spanGeometry(span, bounds, now = Date.now()) {
  const start = spanStart(span);
  const duration = Number.isFinite(bounds?.duration)
    ? bounds.duration
    : Number.isFinite(bounds?.end) && Number.isFinite(bounds?.start)
      ? bounds.end - bounds.start
      : NaN;
  const boundsStart = Number.isFinite(bounds?.start) ? bounds.start : 0;
  const left = Number.isFinite(start) && duration > 0
    ? clampPercent(((start - boundsStart) / duration) * 100)
    : 0;

  if (!Number.isFinite(start) || hasUnavailableTiming(span)) {
    return { left, width: 0, unavailable: true };
  }

  let end = spanEnd(span);
  if (!Number.isFinite(end) && bounds?.isLive && (span?.end == null || span.end === '')) {
    const currentTime = timestamp(now);
    end = Number.isFinite(currentTime) ? currentTime : bounds.end;
  }
  if (!Number.isFinite(end) || end < start) {
    return { left, width: 0, unavailable: true };
  }
  if (!(duration > 0)) return { left: 0, width: 0, unavailable: false };

  const right = clampPercent(((end - boundsStart) / duration) * 100);
  return { left, width: Math.max(0, right - left), unavailable: false };
}

function buildNodes(spans) {
  const nodes = spans
    .filter((span) => span && typeof span === 'object')
    .map((span, index) => ({
      span,
      index,
      id: span.id == null || span.id === '' ? `span:${index}` : String(span.id),
      children: [],
    }));
  const firstById = new Map();
  for (const node of nodes) {
    if (!firstById.has(node.id)) firstById.set(node.id, node);
  }
  for (const node of nodes) {
    const parentId = node.span.parent_id;
    const parent = parentId == null ? null : firstById.get(String(parentId));
    if (parent && parent !== node) parent.children.push(node);
  }
  for (const node of nodes) node.children.sort(compareChronologically);
  return { nodes, firstById };
}

function packedLanes(nodes, parentId, kind) {
  const lanes = [];
  let markerLane;
  for (const node of [...nodes].sort(compareChronologically)) {
    const start = spanStart(node.span);
    const end = spanEnd(node.span);
    const unavailable = hasUnavailableTiming(node.span);
    const complete = !unavailable
      && Number.isFinite(start) && Number.isFinite(end) && end >= start;
    let lane;
    if (unavailable) {
      if (!markerLane) {
        markerLane = { spans: [], type: 'marker' };
        lanes.push(markerLane);
      }
      lane = markerLane;
    } else if (complete) {
      lane = lanes.find((candidate) => candidate.type === 'interval' && candidate.end <= start);
      if (!lane) {
        lane = { spans: [], type: 'interval', end: -Infinity };
        lanes.push(lane);
      }
    } else {
      // Open and malformed intervals cannot safely share an interval lane:
      // timelineRows has no current time with which to prove they do not overlap.
      lane = { spans: [], type: 'blocked' };
      lanes.push(lane);
    }
    lane.spans.push(node);
    if (complete) lane.end = end;
  }
  return lanes.map((lane, index) => ({
    id: `lane:${parentId}:${kind}:${index}`,
    kind,
    name: lane.spans.length === 1
      ? rowName(lane.spans[0].span)
      : `${kind === 'model' ? 'Model' : 'Tool'} calls`,
    spans: lane.spans.map((node) => node.span),
    hasChildren: false,
    nodes: lane.spans,
    order: laneStart(lane),
    index: Math.min(...lane.spans.map((node) => node.index)),
  }));
}

/**
 * Produces pre-order trace rows. Model and tool leaf siblings share lanes when
 * their recorded intervals do not overlap; all remaining spans stay structural.
 */
export function timelineRows(spans, collapsed = {}) {
  if (!Array.isArray(spans)) return [];
  const { nodes, firstById } = buildNodes(spans);
  const roots = nodes.filter((node) => {
    const parentId = node.span.parent_id;
    const parent = parentId == null ? null : firstById.get(String(parentId));
    return !parent || parent === node;
  }).sort(compareChronologically);
  const visited = new Set();
  const rows = [];

  const appendChildren = (parent, depth) => {
    const packable = new Map();
    const entries = [];
    for (const child of parent.children) {
      if (child.children.length === 0 && canPack(child.span)) {
        const kind = normalizedKind(child.span);
        if (!packable.has(kind)) packable.set(kind, []);
        packable.get(kind).push(child);
      } else {
        entries.push({ type: 'node', node: child, order: spanStart(child.span), index: child.index });
      }
    }
    for (const [kind, siblings] of packable) {
      for (const lane of packedLanes(siblings, parent.id, kind)) {
        entries.push({ type: 'lane', lane, order: lane.order, index: lane.index });
      }
    }
    entries.sort((left, right) => {
      const leftOrder = Number.isFinite(left.order) ? left.order : Infinity;
      const rightOrder = Number.isFinite(right.order) ? right.order : Infinity;
      return leftOrder - rightOrder || left.index - right.index;
    });
    for (const entry of entries) {
      if (entry.type === 'lane') {
        for (const node of entry.lane.nodes) visited.add(node);
        rows.push({
          id: entry.lane.id,
          depth,
          kind: entry.lane.kind,
          name: entry.lane.name,
          spans: entry.lane.spans,
          hasChildren: false,
        });
      } else {
        visit(entry.node, depth);
      }
    }
  };

  const hide = (node) => {
    if (visited.has(node)) return;
    visited.add(node);
    for (const child of node.children) hide(child);
  };

  const visit = (node, depth) => {
    if (visited.has(node)) return;
    visited.add(node);
    const syntheticRoot = normalizedKind(node.span) === 'run' && !node.span.parent_id;
    if (syntheticRoot) {
      appendChildren(node, depth);
      return;
    }
    const id = node.span.id == null || node.span.id === '' ? node.id : node.span.id;
    rows.push({
      id,
      depth,
      kind: normalizedKind(node.span),
      name: rowName(node.span),
      spans: [node.span],
      hasChildren: node.children.length > 0,
    });
    if (!collapsed[id]) {
      appendChildren(node, depth + 1);
    } else {
      for (const child of node.children) hide(child);
    }
  };

  for (const root of roots) visit(root, 0);
  for (const node of nodes) visit(node, 0);
  return rows;
}

export function tickLabel(milliseconds) {
  if (!Number.isFinite(milliseconds)) return '—';
  if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`;
  if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(1)}s`;
  return `${Math.floor(milliseconds / 60000)}m`;
}

/** Roll up recorded usage without counting inclusive parent totals twice. */
export function activityRollups(spans) {
  const byId = new Map(spans.map((span) => [span.id, span]));
  const children = new Map();
  for (const span of byId.values()) {
    if (!span.parent_id || !byId.has(span.parent_id) || span.parent_id === span.id) continue;
    if (!children.has(span.parent_id)) children.set(span.parent_id, []);
    children.get(span.parent_id).push(span);
  }
  const result = new Map();
  const visiting = new Set();
  const visit = (span) => {
    if (result.has(span.id)) return result.get(span.id);
    if (visiting.has(span.id)) return { tokens: null, descendants: [] };
    visiting.add(span.id);
    const descendants = [];
    let total = 0;
    let known = false;
    for (const child of children.get(span.id) || []) {
      if (visiting.has(child.id)) continue;
      const rollup = visit(child);
      descendants.push(child, ...rollup.descendants);
      if (rollup.tokens !== null) { total += rollup.tokens; known = true; }
    }
    const own = typeof span.tokens === 'number' && Number.isFinite(span.tokens) ? span.tokens : null;
    const value = { tokens: known ? total : own, descendants };
    result.set(span.id, value);
    visiting.delete(span.id);
    return value;
  };
  for (const span of byId.values()) visit(span);
  return result;
}

/** A collapsed live run must not be frozen at its last expanded trace. */
export function runSummarySource(row, trace, expanded) {
  if (trace && (expanded || (!isLiveTrace(row) && row.status === trace.status))) return trace;
  return { ...row, spans: row.overview || [] };
}
