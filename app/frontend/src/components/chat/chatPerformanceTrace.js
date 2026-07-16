const TRACE_QUERY_PARAM = 'chatPerformanceTrace';
const MAX_TRACE_ENTRIES = 2_000;

const SENSITIVE_TRACE_DETAIL_KEYS = new Set([
  'args', 'arguments', 'body', 'content', 'error', 'image', 'images',
  'message', 'output', 'prompt', 'reasoning', 'result', 'summary', 'text',
  'toolarguments', 'toolresult',
]);

const traceStartedAt = performance.now();
const entries = [];
let enabled = false;
let initialized = false;
let longTaskObserver = null;

function isInitialTraceRequest() {
  return new URLSearchParams(window.location.search).get(TRACE_QUERY_PARAM) === '1';
}

function roundMilliseconds(value) {
  return Number.isFinite(value) ? Math.round(value * 100) / 100 : null;
}

function normalizeDetails(details) {
  if (!details || typeof details !== 'object') return {};

  return Object.fromEntries(
    Object.entries(details).filter(([key, value]) => (
      !SENSITIVE_TRACE_DETAIL_KEYS.has(key.toLowerCase())
      && (
        value === null
        || typeof value === 'string'
        || typeof value === 'number'
        || typeof value === 'boolean'
      )
    )),
  );
}

function writeTraceEntry(name, details, { consoleOutput = true } = {}) {
  const event = {
    name,
    atMs: roundMilliseconds(performance.now() - traceStartedAt),
    wallTimeMs: Date.now(),
    ...normalizeDetails(details),
  };
  entries.push(event);
  if (entries.length > MAX_TRACE_ENTRIES) {
    entries.splice(0, entries.length - MAX_TRACE_ENTRIES);
  }
  if (consoleOutput) {
    console.info('[chat-perf]', event);
  }
  return event;
}

function startLongTaskObserver() {
  if (longTaskObserver) return;

  longTaskObserver = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      writeTraceEntry('browser.long_task', {
        durationMs: roundMilliseconds(entry.duration),
        startTimeMs: roundMilliseconds(entry.startTime),
      });
    }
  });
  longTaskObserver.observe({ type: 'longtask', buffered: true });
}

export function isChatPerformanceTracingEnabled() {
  return enabled;
}

export function chatPerformanceNow() {
  return performance.now();
}

export function traceChatPerformance(name, details = {}, options = {}) {
  if (!enabled) return null;
  return writeTraceEntry(name, details, options);
}

export function initializeChatPerformanceTrace() {
  if (initialized) return;

  initialized = true;
  enabled = isInitialTraceRequest();
  if (!enabled) return;

  startLongTaskObserver();
  writeTraceEntry('trace.enabled', { maxEntries: MAX_TRACE_ENTRIES });
}

export function clearChatPerformanceTrace() {
  entries.length = 0;
}

export function getChatPerformanceTrace() {
  return entries.slice();
}

export function summarizeChatPerformanceTrace() {
  const byName = {};
  const byStream = {};
  for (const entry of entries) {
    byName[entry.name] = (byName[entry.name] || 0) + 1;
    const traceId = entry.streamId || entry.requestId;
    if (!traceId) continue;
    const stream = byStream[traceId] || {
      eventCount: 0,
      firstAtMs: entry.atMs,
      lastAtMs: entry.atMs,
      maxDurationMs: 0,
    };
    stream.eventCount += 1;
    stream.lastAtMs = entry.atMs;
    stream.maxDurationMs = Math.max(stream.maxDurationMs, entry.durationMs || 0);
    byStream[traceId] = stream;
  }
  return {
    enabled,
    entryCount: entries.length,
    byName,
    byStream,
  };
}

