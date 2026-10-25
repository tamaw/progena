/**
 * Turns one tool call's input and output into the two boxes ToolCallEntry
 * renders, each `{ label, meta, tone, body }`:
 *
 *   body.kind  'fields'  rows of { name, value }
 *              'code'    { text, startLine? }  -- monospace, optional line numbers
 *              'command' { text }              -- a shell command ($ prefix)
 *              'diff'    { removed, added }    -- edit_file old/new strings
 *              'list'    { items }             -- paths
 *              'text'    { text }              -- verbatim output
 *
 * The built-in tools (Deep Agents filesystem tools and the javascript code
 * interpreter) get views shaped by their known argument names and output
 * formats; every other tool gets the generic pair (fields / raw text).
 *
 * Outputs are previews: the runtime caps them (1 KB live, 400 chars in
 * history), so every parser tolerates a cut-off tail and marks it.
 */

const TRUNCATION_TAIL = /(?:…\[truncated\]|…)$/;
const LONG_VALUE_CHARS = 120;

function isRecord(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

/** Tool arguments as an object when they are one (live previews over 2 KB arrive as a cut-off string). */
export function parseToolArgs(argsPreview) {
  if (isRecord(argsPreview)) return argsPreview;
  if (typeof argsPreview === 'string' && argsPreview.trim().startsWith('{')) {
    try {
      const parsed = JSON.parse(argsPreview);
      if (isRecord(parsed)) return parsed;
    } catch {
      // A cut-off preview is not valid JSON; shown raw below.
    }
  }
  return null;
}

function displayValue(value) {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return '';
  return JSON.stringify(value);
}

function stripTail(text) {
  const truncated = TRUNCATION_TAIL.test(text);
  return { text: truncated ? text.replace(TRUNCATION_TAIL, '') : text, truncated };
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

function lineCount(text) {
  return text ? text.split('\n').length : 0;
}

/** Short scalar arguments as rows; null/empty ones dropped (tools send their defaults). */
function fieldRows(args, skip = []) {
  return Object.entries(args)
    .filter(([name, value]) => !skip.includes(name) && value !== null && value !== undefined && value !== '')
    .map(([name, value]) => ({ name, value: displayValue(value) }));
}

function box(label, body, { meta = '', tone = '' } = {}) {
  return { label, meta, tone, body };
}

// Input box from a few named fields plus at most one promoted long block.
function inputBox(args, rows, block, meta = '') {
  if (!args) return null;
  if (block && rows.length === 0) return box('Input', block, { meta });
  if (!block) return rows.length ? box('Input', { kind: 'fields', rows }, { meta }) : null;
  return box('Input', { kind: 'fields', rows, block }, { meta });
}

// ---------------------------------------------------------------- outputs

/** Python-style list repr (`['/a', "/b"]`) or JSON list -> items; tolerates a cut-off tail. */
export function parsePathList(text) {
  const trimmed = text.trim();
  if (!trimmed.startsWith('[')) return null;
  const items = [];
  const pattern = /'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"/g;
  for (const match of trimmed.matchAll(pattern)) {
    items.push((match[1] ?? match[2]).replace(/\\(.)/g, '$1'));
  }
  return items;
}

function listOutput(raw) {
  const { text, truncated } = stripTail(raw);
  const items = parsePathList(text);
  if (!items) return box('Output', { kind: 'text', text: raw });
  const complete = !truncated && text.trim().endsWith(']');
  if (items.length === 0 && complete) return box('Output', null, { meta: 'empty' });
  return box('Output', { kind: 'list', items }, { meta: complete ? plural(items.length, 'item') : `${items.length}+ items` });
}

/**
 * read_file: optional notices, an `@@ field | field @@` status header, then
 * verbatim file text -- or, from older Deep Agents, `cat -n` style lines
 * (`     1\ttext`). Either way the body gets real line numbers.
 */
export function parseReadFileOutput(raw) {
  const { text, truncated } = stripTail(raw);
  const header = /^@@ (.+?) @@[ \t]*$/m.exec(text);
  if (!header) return numberedLinesOutput(text, truncated) || box('Output', { kind: 'text', text: raw });
  const fields = header[1].split('|').map((field) => field.trim()).filter(Boolean);
  const startLine = Number(/lines (\d+)/.exec(header[1])?.[1]) || 1;
  const notice = text.slice(0, header.index).trim();
  const body = text.slice(header.index + header[0].length).replace(/^\n/, '');
  const meta = [...fields, truncated ? 'preview' : ''].filter(Boolean).join(' · ');
  return box('Output', { kind: 'code', text: notice ? `${notice}\n\n${body}` : body, startLine: notice ? null : startLine }, { meta });
}

function numberedLinesOutput(text, truncated) {
  const lines = text.split('\n');
  const numbered = /^\s*(\d+)\t/;
  const first = numbered.exec(lines[0]);
  if (!first) return null;
  const body = lines.map((line) => line.replace(numbered, '')).join('\n');
  const startLine = Number(first[1]);
  const lastNumber = [...lines].reverse().map((line) => numbered.exec(line)).find(Boolean);
  const range = `lines ${startLine}–${lastNumber ? lastNumber[1] : startLine}`;
  return box('Output', { kind: 'code', text: body, startLine }, { meta: truncated ? `${range} · preview` : range });
}

function grepOutput(raw, args) {
  if (raw.trim() === 'No matches found') return box('Output', null, { meta: 'no matches' });
  if (args?.output_mode === 'files_with_matches' || args?.output_mode === undefined) {
    const { text, truncated } = stripTail(raw);
    const items = text.split('\n').map((line) => line.trim()).filter(Boolean);
    if (items.every((item) => item.startsWith('/'))) {
      return box('Output', { kind: 'list', items }, { meta: truncated ? `${items.length}+ files` : plural(items.length, 'file') });
    }
  }
  return box('Output', { kind: 'code', text: raw, startLine: null });
}

function executeOutput(raw) {
  const status = /\[Command (succeeded|failed) with exit code (-?\d+)\]\s*$/.exec(raw);
  if (!status) return box('Output', raw.trim() ? { kind: 'code', text: raw, startLine: null } : null);
  const body = raw.slice(0, status.index).trimEnd();
  const failed = status[1] === 'failed';
  return box(failed ? 'Error' : 'Output', body ? { kind: 'code', text: body, startLine: null } : null, {
    meta: `exit ${status[2]}`,
    tone: failed ? 'error' : '',
  });
}

const XML_ENTITIES = { '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'", '&amp;': '&' };

function unescapeXml(text) {
  return text.replace(/&(?:lt|gt|quot|apos|amp);/g, (entity) => XML_ENTITIES[entity]);
}

/** javascript (QuickJS): `<stdout>…</stdout>`, then `<error type="T">…</error>` or `<result kind="k">…</result>`. */
export function parseJavascriptOutput(raw) {
  const { text } = stripTail(raw);
  const section = (tag) => {
    const open = new RegExp(`<${tag}(?:\\s+(\\w+)="([^"]*)")?>`).exec(text);
    if (!open) return null;
    const start = open.index + open[0].length;
    const close = text.indexOf(`</${tag}>`, start);
    return { attr: open[2] || '', body: unescapeXml(text.slice(start, close >= 0 ? close : text.length)).replace(/^\n|\n$/g, '') };
  };
  const stdout = section('stdout');
  const error = section('error');
  const result = section('result');
  if (!stdout && !error && !result) return box('Output', { kind: 'text', text: raw });
  const parts = [stdout?.body, error?.body ?? result?.body].filter((part) => part && part !== 'undefined');
  return box(error ? 'Error' : 'Output', parts.length ? { kind: 'code', text: parts.join('\n\n'), startLine: null } : null, {
    meta: error ? error.attr : (result?.attr || ''),
    tone: error ? 'error' : '',
  });
}

// --------------------------------------------------------------- per tool

const CONFIRMATIONS = {
  write_file: { pattern: /^Updated file /, meta: () => 'written' },
  edit_file: {
    pattern: /^Successfully replaced (\d+) instance/,
    meta: (match) => `replaced ${match[1]}`,
  },
};

function confirmationOutput(toolName, raw) {
  const confirmation = CONFIRMATIONS[toolName];
  const match = confirmation?.pattern.exec(raw.trim());
  return match ? box('Output', null, { meta: confirmation.meta(match) }) : box('Output', { kind: 'text', text: raw });
}

const BUILT_IN_VIEWS = {
  ls: {
    input: (args) => inputBox(args, fieldRows(args)),
    output: listOutput,
  },
  glob: {
    input: (args) => inputBox(args, fieldRows(args)),
    output: listOutput,
  },
  grep: {
    input: (args) => inputBox(args, fieldRows(args)),
    output: grepOutput,
  },
  read_file: {
    input: (args) => inputBox(args, fieldRows(args)),
    output: parseReadFileOutput,
  },
  write_file: {
    input: (args) => {
      const content = displayValue(args.content);
      return inputBox(args, fieldRows(args, ['content']), { kind: 'code', text: content, startLine: 1 }, plural(lineCount(content), 'line'));
    },
    output: (raw) => confirmationOutput('write_file', raw),
  },
  edit_file: {
    input: (args) => {
      const removed = displayValue(args.old_string);
      const added = displayValue(args.new_string);
      const rows = fieldRows(args, ['old_string', 'new_string', 'replace_all']);
      if (args.replace_all === true) rows.push({ name: 'replace_all', value: 'true' });
      return inputBox(args, rows, { kind: 'diff', removed, added }, `−${lineCount(removed)} +${lineCount(added)}`);
    },
    output: (raw) => confirmationOutput('edit_file', raw),
  },
  execute: {
    input: (args) => inputBox(args, fieldRows(args, ['command']), { kind: 'command', text: displayValue(args.command) }),
    output: executeOutput,
  },
  javascript: {
    input: (args) => inputBox(args, fieldRows(args, ['code']), { kind: 'code', text: displayValue(args.code), startLine: null }),
    output: parseJavascriptOutput,
  },
};

export function isBuiltInTool(toolName) {
  return Object.hasOwn(BUILT_IN_VIEWS, toolName);
}

function genericInput(args) {
  const rows = fieldRows(args);
  const long = rows.filter((row) => row.value.length > LONG_VALUE_CHARS || row.value.includes('\n'));
  const short = rows.filter((row) => !long.includes(row));
  // The longest value (prompt, message, payload) becomes the block; the rest stay rows.
  const block = long.sort((a, b) => b.value.length - a.value.length)[0];
  const others = [...short, ...long.filter((row) => row !== block)];
  return inputBox(args, others, block ? { kind: 'code', text: block.value, startLine: null, name: block.name } : null, plural(rows.length, 'field'));
}

/**
 * The input and output boxes for one tool call. `output` is null while the
 * call is still running; an errored call's output is labelled Error.
 *
 * `artifact.summary` is the runtime's authoritative, user-facing account of
 * the result (including omitted-line notices). Keep it beside—not instead
 * of—the preview so a useful raw result remains inspectable.
 */
function artifactSummary(artifact) {
  return artifact && typeof artifact === 'object' && !Array.isArray(artifact)
    && typeof artifact.summary === 'string' && artifact.summary.trim()
    ? artifact.summary.trim()
    : '';
}

function withArtifactSummary(output, artifact) {
  const summary = artifactSummary(artifact);
  if (!summary) return output;
  if (!output) return box('Output', null, { meta: summary });
  if (output.meta.includes(summary)) return output;
  return { ...output, meta: [summary, output.meta].filter(Boolean).join(' · ') };
}

export function buildToolCallView({
  toolName, argsPreview, resultText, errorText, artifact, status,
}) {
  const view = BUILT_IN_VIEWS[toolName];
  const args = parseToolArgs(argsPreview);
  let input = null;
  if (args) {
    input = view ? view.input(args) : genericInput(args);
  } else if (typeof argsPreview === 'string' && argsPreview.trim()) {
    input = box('Input', { kind: 'text', text: argsPreview }, { meta: 'preview' });
  }

  let output = null;
  if (status === 'error') {
    const text = errorText || resultText || '';
    output = box('Error', text ? { kind: 'text', text } : null, { tone: 'error' });
  } else if (status === 'success') {
    const raw = typeof resultText === 'string' ? resultText : '';
    if (view) {
      output = view.output(raw, args);
      // Every output is a capped preview; say so wherever the cap cut it.
      if (output && stripTail(raw).truncated && !output.meta.includes('preview')) {
        output = { ...output, meta: [output.meta, 'preview'].filter(Boolean).join(' · ') };
      }
    } else if (raw.trim()) {
      const { truncated } = stripTail(raw);
      output = box('Output', { kind: 'text', text: raw }, { meta: truncated ? 'preview' : '' });
    }
  }
  return { input, output: withArtifactSummary(output, artifact) };
}
