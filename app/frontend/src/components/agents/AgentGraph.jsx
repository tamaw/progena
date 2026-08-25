import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { createPortal } from 'react-dom';
import Select from '../common/Select';
import PopupModal from '../common/PopupModal';
import { useConfig } from '../../context/ConfigContext';
import {
  fetchRoutineRevision,
  fetchRoutineRuns,
  fetchRunState,
  selectAgentProfiles,
  selectAgentRoutineRevisionContent,
  selectAgentRoutineRuns,
  selectAgentRunState,
} from '../../store/uiDataSlice';
import { DeleteActionIcon, DetailHeaderAction, DetailHeaderActions, OpenActionIcon, PlayActionIcon } from '../layout/DetailHeader';
import { formatAge } from './agentFormat';
import { checkExpressionSyntax, tokenize } from '../../lib/restrictedExpression';
import {
  DISCOVERY_POLL_INTERVAL_MS,
  DISCOVERY_TIMEOUT_MS,
  SETTLE_STABLE_POLLS,
  STATE_POLL_INTERVAL_MS,
  STATE_POLL_TIMEOUT_MS,
  findRunBySessionId,
  latestCompletionMs,
  parseRunThreadId,
  randomSessionId,
  totalRunMetaEntries,
} from '../../lib/routineRunPolling';
import { diffRoutineGraphs, isNodeRunValid } from '../../lib/routineGraphDiff';
// Reuse the shared canvas chrome (dock icons, JSON-editor panel/side-dock) so
// this still shares CSS with the Workflows canvas where the visuals overlap.
// The Routines graph itself is NOT a free canvas though (see rationale below),
// so it does not use React Flow at all.
import '../../styles/canvas.css';
import '../../styles/nodes.css';
import hljs from 'highlight.js/lib/common';

// Escapes `text` for use inside a `dangerouslySetInnerHTML` layer -- only the
// three characters that change HTML parsing matter here (this never lands in
// an attribute), unlike markdown.js's escapeHtml which also escapes quotes.
function escapeHtmlText(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// A routine graph is compiled straight into a runnable LangGraph agent: node
// position on screen carries no meaning (there's no "x"/"y" in the schema),
// and every node MUST be reachable from a single `entry` point to ever run.
// A free-drag canvas invites disconnected islands and arbitrary spatial
// layout that says nothing about execution order, so this renders a rigid,
// top-down chain instead: a fixed Start cap, one card per step, a fixed End
// cap at every place the chain terminates, and a `+` control on each
// connector to insert a step at that exact point. A `state_equals` router
// node forks the chain into one column per branch (mirrors Azure Logic
// Apps'/Power Automate's Condition/Switch actions) rather than trying to
// force branching logic onto a single line.

// Routine step kinds offered at each insertion point. Mirrors
// app.progenitor.routines.graph.VALID_ROUTINE_NODE_KINDS.
const GRAPH_NODE_KINDS = [
  { kind: 'prompt', label: 'Prompt' },
  { kind: 'eval', label: 'Eval', icon: '<>' },
  { kind: 'condition', label: 'Branch' },
  { kind: 'switch', label: 'Switch' },
  { kind: 'split', label: 'Fork' },
  { kind: 'classify', label: 'Select' },
  { kind: 'agent', label: 'Agent', icon: '✦' },
  { kind: 'a2a', label: 'A2A', icon: '⇄' },
  { kind: 'human', label: 'Human approval', icon: '⚑' },
  { kind: 'foreach', label: 'Each', icon: '⇉' },
  { kind: 'loop', label: 'Loop', icon: '↺' },
  { kind: 'routine', label: 'Routine', icon: '⧉' },
  { kind: 'noop', label: 'Noop', icon: '·' },
];

const GRAPH_KIND_COLORS = {
  prompt: '#3b82f6',
  eval: '#6366f1',
  condition: '#f59e0b',
  switch: '#eab308',
  split: '#84cc16',
  classify: '#d946ef',
  agent: '#10b981',
  a2a: '#f97316',
  human: '#f43f5e',
  foreach: '#06b6d4',
  loop: '#d29922',
  routine: '#8b5cf6',
  noop: '#6b7280',
};

// A `condition` node is a two-way gate, so its router's branch set is fixed
// rather than author-defined. Mirrors app.progenitor.routines.graph's
// CONDITION_BRANCH_VALUES; order here is the left-to-right lane order.
const CONDITION_BRANCHES = ['true', 'false'];

const GRAPH_KIND_ICONS = Object.fromEntries(GRAPH_NODE_KINDS.map((item) => [item.kind, item.icon]));
// The picker's display name for a kind, everywhere a kind needs to read as
// text: a fresh node's default label, the inspector's kind badge, a step
// card's aria-label. Deriving these from the raw `kind` string instead
// (`titleCase('condition')` -> "Condition", not "Branch"; `titleCase('a2a')`
// -> "A2a", not "A2A") drifts from the picker the moment a kind's label
// diverges from its identifier or isn't simple title case to begin with.
const GRAPH_KIND_LABELS = Object.fromEntries(GRAPH_NODE_KINDS.map((item) => [item.kind, item.label]));

// Renders a kind's icon. `prompt`, `condition`/`decision`, `switch`, and
// `split` get real (`currentColor`-stroked) SVGs instead of a plain
// character; everything else stays a plain Unicode glyph. This has to be a
// component (evaluated during render), not a value baked into
// GRAPH_NODE_KINDS/GRAPH_KIND_ICONS at module scope -- those are built
// eagerly on import, and this project's plain-esbuild vitest transform
// (no @vitejs/plugin-react, see vitest.config.js) compiles JSX to
// `React.createElement`, which would throw immediately on import for any
// JSX evaluated outside a component body.
//
// `condition` (Decision) keeps the plain diamond -- the base binary gate.
// `switch` and `split` are visually unrelated to it on purpose (an earlier
// pass tried differentiating them only by a small mark inside the same
// diamond, which read as near-identical at 12px): `switch` is a `{}` pair
// (the case/switch-statement glyph -- reads unambiguously as "multiple
// named cases"), `split` is an inverted fork -- a plain Y, one stem
// branching into two downward tines (a literal fork in the road: one path
// becomes several, all taken).
function DiamondIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path d="M8 1.5 14.5 8 8 14.5 1.5 8Z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

function SwitchIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path
        d="M6.5 2.5c-1.4 0-2 .7-2 2v1.3c0 .9-.4 1.3-1.3 1.5v.4c.9.2 1.3.6 1.3 1.5V10.5c0 1.3.6 2 2 2M9.5 2.5c1.4 0 2 .7 2 2v1.3c0 .9.4 1.3 1.3 1.5v.4c-.9.2-1.3.6-1.3 1.5V10.5c0 1.3-.6 2-2 2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SplitIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path
        d="M8 2.5v3.8M8 6.3 4 13M8 6.3l4 6.7"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SelectIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="8" cy="8" r="2.2" fill="currentColor" />
    </svg>
  );
}

function KindIcon({ kind }) {
  if (kind === 'prompt') {
    return (
      <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
        <path
          d="M2.5 3.5A1.5 1.5 0 0 1 4 2h8a1.5 1.5 0 0 1 1.5 1.5v5A1.5 1.5 0 0 1 12 10H6.5l-2.75 2.5V10H4a1.5 1.5 0 0 1-1.5-1.5z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
    );
  }
  if (kind === 'condition') return <DiamondIcon />;
  if (kind === 'classify') return <SelectIcon />;
  if (kind === 'switch') return <SwitchIcon />;
  if (kind === 'split') return <SplitIcon />;
  return GRAPH_KIND_ICONS[kind] || '•';
}

function titleCase(kind) {
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

function uniqueNodeId(kind, nodes) {
  const ids = new Set(nodes.map((node) => node.id));
  if (!ids.has(kind)) return kind;
  let i = 1;
  while (ids.has(`${kind}-${i}`)) i += 1;
  return `${kind}-${i}`;
}

function uniqueBranchValue(branches) {
  const used = new Set(Object.keys(branches || {}));
  let i = 1;
  while (used.has(`case-${i}`)) i += 1;
  return `case-${i}`;
}

function makeNode(kind) {
  const config = {
    prompt: { prompt_template: '' },
    eval: { expression: '' },
    condition: { expression: '' },
    switch: { expression: '' },
    split: {},
    classify: { rubric_prompt: '', response_schema: {} },
    agent: { agent_name: '', prompt_template: '' },
    a2a: { agent_name: '', prompt_template: '' },
    human: { message: '', allowed_responses: [] },
    foreach: { mode: 'send', items_from: '', item_key: 'item' },
    routine: { routine_name: '', input_map: {}, output_map: {} },
    noop: {},
  }[kind] || {};
  return { id: '', kind, label: GRAPH_KIND_LABELS[kind] || titleCase(kind), description: '', config };
}

// What each kind must carry before the backend will accept it, mirroring the
// `required`/`oneOf` blocks of the *Config defs in graph.routine.schema.v1.json.
// A nested array means "exactly one of these" (a prompt takes an inline
// template or a file, never both). Kept as data rather than spread through the
// field renderers so the inspector, the canvas badge and the autosave gate all
// read one source -- and so it stays diffable against the schema it mirrors.
export const REQUIRED_CONFIG = {
  prompt: [['prompt_template', 'prompt_file']],
  eval: [['expression', 'expression_file']],
  condition: [['expression', 'expression_file']],
  switch: [['expression', 'expression_file']],
  split: [],
  classify: ['rubric_prompt', 'response_schema'],
  agent: ['agent_name', ['prompt_template', 'prompt_file', 'inputs']],
  a2a: ['agent_name', ['prompt_template', 'prompt_file', 'inputs']],
  human: ['message'],
  routine: ['routine_name'],
  noop: [],
};

function isBlank(value) {
  if (value === undefined || value === null) return true;
  if (typeof value === 'string') return value.trim() === '';
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'object') return Object.keys(value).length === 0;
  return false;
}

// `foreach` is the one kind whose requirements depend on its own config: the
// schema splits it into foreachSendConfig/foreachAgentConfig on `mode`.
function requiredFieldsFor(node) {
  if (node?.kind === 'foreach') {
    return node.config?.mode === 'agent'
      ? ['items_from', 'agent_name', 'prompt_template']
      : ['items_from'];
  }
  return REQUIRED_CONFIG[node?.kind] || [];
}

// The requirements a node has not met, one entry per requirement. A one-of
// group stays grouped -- "a prompt needs a template or a file" is a single
// unmet requirement offering two ways to satisfy it, so counting its members
// would tell the author two fields are missing when either one will do.
export function missingRequirements(node) {
  const config = node?.config || {};
  return requiredFieldsFor(node)
    .map((requirement) => (Array.isArray(requirement) ? requirement : [requirement]))
    .filter((group) => group.every((field) => isBlank(config[field])));
}

// The same thing flattened to field names, for marking every alternative in
// the inspector and for deciding whether the graph is safe to PATCH.
export function missingRequiredConfig(node) {
  return missingRequirements(node).flat();
}

// One predicate behind the inspector lock, the canvas badge and the autosave
// gate, so a step can never look complete in one place and not another.
export function isNodeComplete(node) {
  return missingRequirements(node).length === 0;
}

// Nodes the backend would reject, in canvas order. Empty means the graph is
// safe to PATCH as far as per-node completeness goes.
export function incompleteNodes(nodes) {
  return (nodes || [])
    .map((node) => ({ node, missing: missingRequiredConfig(node) }))
    .filter((entry) => entry.missing.length > 0);
}

// Backend's LoopGuardConfig requires a non-empty expression -- a fresh
// Loop's tail starts blank (see insertAt), same "not saved yet" gate as an
// Eval/Condition with an empty expression, just read off the edge instead
// of a node's config.
// eslint-disable-next-line react-refresh/only-export-components
export function incompleteLoopEdges(edges) {
  return (edges || []).filter((edge) => edge.loop_guard && !edge.loop_guard.expression?.trim());
}

// Edit callbacks shared with the card/branch renderers (avoids threading
// mutable refs through node data, which React forbids reading during render).
const GraphEditContext = createContext(null);

// The required fields the inspected node is still missing. A context rather
// than a prop so the marker reaches every ConfigField without rethreading each
// of NodeConfigBody's per-kind call sites.
const EMPTY_MISSING = new Set();
const MissingFieldsContext = createContext(EMPTY_MISSING);

// Bottom-left zoom control's range/step -- 50%-200% in 10% increments,
// starting (and Reset zoom-ing back to) 100%.
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 2;
const ZOOM_STEP = 0.1;

// One config field bound to `data.config[field]` (a plain string value).
function ConfigField({ id, config, field, label, placeholder, multiline, updateNodeConfig }) {
  const missing = useContext(MissingFieldsContext);
  const value = config?.[field] || '';
  const required = missing.has(field);
  const onChange = (event) => updateNodeConfig(id, { [field]: event.target.value });
  const inputClass = (base) => `${base}${required ? ' agents-graph-node__input--required' : ''}`;
  return (
    <label className={`agents-graph-node__field${required ? ' agents-graph-node__field--required' : ''}`}>
      <span className="agents-graph-node__field-label">
        {label}
        {required ? <span className="agents-graph-node__required" title="Required before this routine can be saved">Required</span> : null}
      </span>
      {multiline ? (
        <textarea className={inputClass('agents-graph-node__prompt')} value={value} placeholder={placeholder} onChange={onChange} aria-required={required || undefined} />
      ) : (
        <input className={inputClass('agents-graph-node__toolinput')} value={value} placeholder={placeholder} onChange={onChange} aria-required={required || undefined} />
      )}
    </label>
  );
}

// What identifier/dotted-attribute chain, if any, ends right at the caret --
// tokenizes the text up to `caret` with the exact tokenizer
// `checkExpressionSyntax` uses (see lib/restrictedExpression.js), so a caret
// sitting inside a string or number literal correctly gets no suggestions:
// an unterminated-string tokenize error, or the caret landing right after a
// closed str/num token, both mean "not actually typing an identifier here".
// Anywhere else -- nothing typed yet, right after whitespace, right after an
// operator -- returns an EMPTY query rather than null, so the menu can open
// the moment the field is focused (the caller gates that on focus, not on
// this function) and show every candidate before narrowing as the author
// types. A DANGLING dot ("classify_intent.") is its own case: the query
// includes the trailing dot, which narrows suggestions to that node's own
// properties via a plain substring match (nothing else contains "x.") without
// needing any special-cased "drill into a specific node" branch. Backward-
// only, same convention as chatInputModel.js's mention detector -- whatever's
// already typed past the caret on the same token is left alone.
// eslint-disable-next-line react-refresh/only-export-components
export function getExpressionAutocompleteContext(text, caret) {
  const source = typeof text === 'string' ? text : '';
  const position = Math.min(Number.isFinite(caret) ? caret : source.length, source.length);
  const tokenized = tokenize(source.slice(0, position));
  if (tokenized.error) return null;
  const tokens = tokenized.tokens;
  const lastIndex = tokens.length - 2; // skip the trailing synthetic eof token
  if (lastIndex < 0) return { query: '', tokenStart: position, tokenEnd: position };
  const last = tokens[lastIndex];
  if (last.end !== position) return { query: '', tokenStart: position, tokenEnd: position };
  if (last.type === 'str' || last.type === 'num') return null;
  const danglingDot = last.type === 'op' && last.value === '.';
  if (last.type !== 'name' && !danglingDot) {
    return { query: '', tokenStart: position, tokenEnd: position };
  }
  let start = last.start;
  let i = lastIndex - 1;
  let expectName = danglingDot;
  while (i >= 0) {
    const tok = tokens[i];
    if (expectName) {
      if (tok.type !== 'name' || tok.end !== start) break;
    } else {
      if (tok.type !== 'op' || tok.value !== '.' || tok.end !== start) break;
    }
    start = tok.start;
    expectName = !expectName;
    i -= 1;
  }
  return { query: source.slice(start, position), tokenStart: start, tokenEnd: position };
}

// `Expression` fields (eval/condition/switch config, Loop's loop_guard) hold
// a restricted Python expression -- worth highlighting the same way the
// "Edit as JSON" panel does: a transparent <textarea> stacked over a
// highlight.js `<pre>` so syntax colors show through while the caret still
// lands in a real, editable, screen-reader-visible textarea underneath. A
// second, equally transparent `<pre>` sits between the two: same text, but
// every character is `color: transparent` except the span
// `checkExpressionSyntax` flags as the first construct outside
// `expression.py`'s whitelist, which gets a red wavy underline via
// `text-decoration-color` (independent of the (transparent) text color, so
// the squiggly shows without a second copy of the glyphs).
//
// `autocomplete` layers a caret-anchored state-path menu on top -- open the
// moment the field is focused (showing every candidate), narrowing as the
// author types. Expressions resolve names directly against full routine
// state now (see expression.py's `ast.Attribute` handling), so there's no
// separate `inputs` binding step left to author; the same predecessor/
// response_schema suggestions just get typed straight into the expression.
function ExpressionField({ id, config, field, label, placeholder, updateNodeConfig, autocomplete = false }) {
  const missing = useContext(MissingFieldsContext);
  const graph = useContext(GraphEditContext);
  const value = config?.[field] || '';
  const required = missing.has(field);
  const highlightRef = useRef(null);
  const errorLayerRef = useRef(null);
  const textareaRef = useRef(null);
  const [focused, setFocused] = useState(false);
  const [caret, setCaret] = useState(0);
  const [rawActiveIndex, setRawActiveIndex] = useState(0);
  const [dismissedAt, setDismissedAt] = useState(null);
  const [panelPos, setPanelPos] = useState(null);
  const highlighted = useMemo(() => {
    try {
      return hljs.highlight(value, { language: 'python' }).value;
    } catch {
      return value;
    }
  }, [value]);
  const issue = useMemo(() => checkExpressionSyntax(value), [value]);
  const errorHtml = useMemo(() => {
    if (issue.ok) return null;
    const before = escapeHtmlText(value.slice(0, issue.start));
    const flagged = escapeHtmlText(value.slice(issue.start, issue.end)) || ' ';
    const after = escapeHtmlText(value.slice(issue.end));
    return `${before}<span class="agents-graph-node__expr-error-span">${flagged}</span>${after}\n`;
  }, [issue, value]);

  const suggestions = useMemo(
    () => (autocomplete ? buildStatePathSuggestions(id, graph?.nodes, graph?.edges, graph?.inputSchema) : EMPTY_SUGGESTIONS),
    [autocomplete, id, graph?.nodes, graph?.edges, graph?.inputSchema],
  );
  const context = useMemo(
    () => (autocomplete && focused ? getExpressionAutocompleteContext(value, caret) : null),
    [autocomplete, focused, value, caret],
  );
  const filtered = useMemo(
    () => (context ? filterStatePathSuggestions(suggestions, context.query) : EMPTY_SUGGESTIONS),
    [suggestions, context],
  );
  const activeIndex = filtered.length ? ((rawActiveIndex % filtered.length) + filtered.length) % filtered.length : 0;
  const menuOpen = !!context && filtered.length > 0 && dismissedAt !== context.tokenStart;

  useLayoutEffect(() => {
    if (!menuOpen || !textareaRef.current) return;
    const rect = textareaRef.current.getBoundingClientRect();
    const coords = getCaretCoordinates(textareaRef.current, context.tokenStart);
    setPanelPos({
      top: rect.top - textareaRef.current.scrollTop + coords.top + coords.height + 4,
      left: rect.left - textareaRef.current.scrollLeft + coords.left,
    });
  }, [menuOpen, context?.tokenStart, value]);

  const onChange = (event) => {
    updateNodeConfig(id, { [field]: event.target.value });
    setCaret(event.target.selectionStart);
  };

  function accept(item) {
    if (!item || !context) return;
    const nextValue = `${value.slice(0, context.tokenStart)}${item.path}${value.slice(context.tokenEnd)}`;
    const nextCaret = context.tokenStart + item.path.length;
    updateNodeConfig(id, { [field]: nextValue });
    setDismissedAt(null);
    setRawActiveIndex(0);
    requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (!textarea) return;
      textarea.focus();
      textarea.setSelectionRange(nextCaret, nextCaret);
      setCaret(nextCaret);
    });
  }

  function handleKeyDown(event) {
    if (!menuOpen) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setRawActiveIndex((current) => current + 1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setRawActiveIndex((current) => current - 1);
    } else if (event.key === 'Enter') {
      // Tab deliberately does NOT accept here (unlike InputsField's old
      // line-based menu) -- with the menu now open for the whole time a
      // field is focused rather than only after typing a character, always
      // treating Tab as "accept" would trap normal keyboard navigation:
      // an author could no longer Tab out of an empty/finished expression
      // without first dismissing the menu.
      event.preventDefault();
      accept(filtered[activeIndex]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      setDismissedAt(context.tokenStart);
    }
  }

  const syncCaret = (event) => setCaret(event.target.selectionStart);

  function syncScroll(event) {
    const { scrollTop, scrollLeft } = event.target;
    if (highlightRef.current) {
      highlightRef.current.scrollTop = scrollTop;
      highlightRef.current.scrollLeft = scrollLeft;
    }
    if (errorLayerRef.current) {
      errorLayerRef.current.scrollTop = scrollTop;
      errorLayerRef.current.scrollLeft = scrollLeft;
    }
  }
  return (
    <label className={`agents-graph-node__field${required ? ' agents-graph-node__field--required' : ''}`}>
      <span className="agents-graph-node__field-label">
        {label}
        {required ? <span className="agents-graph-node__required" title="Required before this routine can be saved">Required</span> : null}
      </span>
      <div
        className={`agents-graph-node__expr${required ? ' agents-graph-node__input--required' : ''}${issue.ok ? '' : ' agents-graph-node__expr--invalid'}`}
        title={issue.ok ? undefined : issue.message}
      >
        <pre ref={highlightRef} className="agents-graph-node__expr-highlight" aria-hidden="true">
          <code className="hljs language-python" dangerouslySetInnerHTML={{ __html: `${highlighted}\n` }} />
        </pre>
        {errorHtml ? (
          <pre ref={errorLayerRef} className="agents-graph-node__expr-error" aria-hidden="true" dangerouslySetInnerHTML={{ __html: errorHtml }} />
        ) : null}
        <textarea
          ref={textareaRef}
          className="agents-graph-node__expr-textarea"
          value={value}
          placeholder={placeholder}
          onChange={onChange}
          onScroll={syncScroll}
          onKeyDown={handleKeyDown}
          onKeyUp={syncCaret}
          onClick={syncCaret}
          onFocus={(event) => {
            setFocused(true);
            setDismissedAt(null);
            setCaret(event.target.selectionStart);
          }}
          onBlur={() => setFocused(false)}
          spellCheck={false}
          aria-required={required || undefined}
          aria-invalid={issue.ok ? undefined : true}
        />
      </div>
      {issue.ok ? null : <span className="agents-graph-node__expr-error-hint" role="alert">{issue.message}</span>}
      {menuOpen ? (
        <StatePathSuggestionsMenu items={filtered} activeIndex={activeIndex} position={panelPos} onHover={setRawActiveIndex} onSelect={accept} />
      ) : null}
    </label>
  );
}

// `agent_name` field for the `agent`/`a2a`/foreach-agent-mode node kinds: a
// searchable dropdown over the live agent roster (redux `agents.snapshot`)
// instead of a free-text box the author has to get exactly right by hand.
// `agentKind` scopes the roster to what that node kind can actually resolve
// at runtime -- `agent` nodes delegate to a local object agent
// (routines/factory.py `_build_agent_node`), `a2a` nodes delegate to a
// registered Agent2Agent peer (`_build_a2a_node`) -- so the two must not
// share one option list. The current value is always kept in the list even
// if the live roster no longer has it (renamed/removed agent, or the
// snapshot just hasn't arrived yet) so a saved reference is never silently
// blanked out from under the author.
function AgentSelectField({ id, config, field = 'agent_name', label, agentKind, updateNodeConfig }) {
  const missing = useContext(MissingFieldsContext);
  const required = missing.has(field);
  const profiles = useSelector(selectAgentProfiles);
  const value = config?.[field] || '';
  const options = useMemo(() => {
    const names = profiles.filter((agent) => agent.kind === agentKind).map((agent) => agent.name).filter(Boolean);
    const known = new Set(names);
    if (value && !known.has(value)) names.push(value);
    return [...new Set(names)].sort((a, b) => a.localeCompare(b)).map((name) => ({ value: name, label: name }));
  }, [profiles, agentKind, value]);
  return (
    <label className={`agents-graph-node__field${required ? ' agents-graph-node__field--required' : ''}`}>
      <span className="agents-graph-node__field-label">
        {label}
        {required ? <span className="agents-graph-node__required" title="Required before this routine can be saved">Required</span> : null}
      </span>
      <Select
        value={value}
        onChange={(next) => updateNodeConfig(id, { [field]: next })}
        options={options}
        placeholder="Select an agent…"
        searchable
        portal
        ariaLabel={label}
        className={`agents-graph-node__select${required ? ' agents-graph-node__select--required' : ''}`}
      />
    </label>
  );
}

// `eval`/`condition` nodes resolve named inputs from dotted `data` paths before
// evaluation (config.inputs, an object). Edited as one "name: path" per line so
// authors don't have to hand-write JSON for a small map.
function inputsToText(inputs) {
  return Object.entries(inputs || {}).map(([key, path]) => `${key}: ${path}`).join('\n');
}

function textToInputs(text) {
  const result = {};
  text.split('\n').forEach((line) => {
    const separator = line.indexOf(':');
    if (separator === -1) return;
    const key = line.slice(0, separator).trim();
    const path = line.slice(separator + 1).trim();
    if (key) result[key] = path;
  });
  return result;
}

const EMPTY_SUGGESTIONS = [];


// `startsWith` matches first, `includes` matches after, both case-
// insensitive against the dotted path text (not the display source/label).
// A dangling-dot query ("classify_intent.") also keeps the bare object
// itself in the list even though it doesn't literally contain the trailing
// dot -- accepting it undoes the dot (replaces the whole "classify_intent."
// span with just "classify_intent"), which matters most for a node with no
// declared response_schema: `prompt.` has no `.property` entries to narrow
// to at all, so without this the menu would just show nothing once you
// typed the dot, even though `prompt` itself is a perfectly good state
// reference on its own.
// eslint-disable-next-line react-refresh/only-export-components
export function filterStatePathSuggestions(suggestions, query) {
  const q = (query || '').toLowerCase();
  const bareMatch = q.endsWith('.') ? q.slice(0, -1) : null;
  return suggestions
    .filter((item) => {
      const path = item.path.toLowerCase();
      return path.includes(q) || path === bareMatch;
    })
    .sort((a, b) => {
      const aStarts = a.path.toLowerCase().startsWith(q) ? 0 : 1;
      const bStarts = b.path.toLowerCase().startsWith(q) ? 0 : 1;
      return aStarts - bStarts;
    });
}

const MIRROR_STYLE_PROPS = [
  'boxSizing', 'width', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
  'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
  'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing', 'lineHeight',
  'textTransform', 'wordSpacing', 'tabSize', 'whiteSpace', 'wordWrap', 'overflowWrap',
];

// A <textarea> exposes no native API for "where on screen is character N" --
// the standard workaround (same technique as the "textarea-caret-position"
// pattern) is an offscreen div mirroring the textarea's exact box/font
// metrics, filled with the text up to `position`, whose trailing marker
// span's offset IS that position. Only invoked while the menu is open, not
// on every keystroke.
function getCaretCoordinates(textarea, position) {
  const div = document.createElement('div');
  const style = window.getComputedStyle(textarea);
  MIRROR_STYLE_PROPS.forEach((prop) => { div.style[prop] = style[prop]; });
  div.style.position = 'absolute';
  div.style.visibility = 'hidden';
  div.style.top = '0';
  div.style.left = '-9999px';
  div.style.whiteSpace = 'pre-wrap';
  div.style.wordWrap = 'break-word';
  div.style.overflowWrap = 'break-word';
  document.body.appendChild(div);
  div.textContent = textarea.value.slice(0, position);
  const marker = document.createElement('span');
  marker.textContent = textarea.value.slice(position) || '.';
  div.appendChild(marker);
  const coords = { top: marker.offsetTop, left: marker.offsetLeft, height: marker.offsetHeight || parseFloat(style.lineHeight) || 16 };
  document.body.removeChild(div);
  return coords;
}

// Caret-anchored, keeps focus in the textarea throughout (onMouseDown
// preventDefault on every option -- a real mousedown would blur the
// textarea before the click landed, closing the menu out from under it).
function StatePathSuggestionsMenu({ items, activeIndex, position, onHover, onSelect }) {
  if (!position) return null;
  return createPortal(
    <div className="app-listbox__panel agents-graph-node__suggest-menu" style={{ position: 'fixed', top: position.top, left: position.left }}>
      <ul className="app-listbox__list" role="listbox" aria-label="State path suggestions">
        {items.map((item, index) => (
          <li key={item.path} role="option" aria-selected={index === activeIndex} data-index={index}>
            <button
              type="button"
              className={`app-listbox__option ${index === activeIndex ? 'is-active' : ''}`}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => onHover(index)}
              onClick={() => onSelect(item)}
            >
              <span className="app-listbox__option-label">{item.path}</span>
              <span className="agents-graph-node__suggest-menu-source">{item.source}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>,
    document.body,
  );
}

// Shared by every "text <-> structured value" field (Inputs, response_schema
// field lists, routine input/output maps) whose parser drops an in-progress
// line -- that's how each one recognizes "not a real entry yet" (no colon,
// no name). Deriving the textarea's value fresh from `toText(fromText(raw))`
// on every render, as these fields did before this hook existed, erases
// whatever the author is mid-typing (a name with no colon after it yet, a
// colon with nothing after it yet) the instant it re-renders -- keystrokes
// before the FIRST fully-formed line just vanish, which is indistinguishable
// from "I can't type in this field" (confirmed live: typing into a fresh
// response_schema field did nothing at all, every character wiped before
// the next keystroke could build on it).
//
// The buffer resets from `value` only when `id` changes (a different node
// is now selected) or `value` changed for a reason other than this field's
// own last edit (e.g. the "Edit as JSON" panel) -- tracked by comparing
// object/array identity against what this field itself last emitted.
// `toText` must be pure/stable (called during render).
function useBufferedFieldText(id, value, toText) {
  const [trackedId, setTrackedId] = useState(id);
  const [trackedValue, setTrackedValue] = useState(value);
  const [text, setText] = useState(() => toText(value));
  if (trackedId !== id || value !== trackedValue) {
    setTrackedId(id);
    setTrackedValue(value);
    const nextText = toText(value);
    if (nextText !== text) setText(nextText);
  }
  return [text, setText, setTrackedValue];
}

function InputsField({ id, config, updateNodeConfig }) {
  const [text, setText, setTrackedInputs] = useBufferedFieldText(id, config?.inputs, inputsToText);
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">Inputs (name: path, e.g. host: parse_json.host)</span>
      <textarea
        className="agents-graph-node__prompt"
        value={text}
        placeholder="host: parse_json.host"
        onChange={(event) => {
          const nextText = event.target.value;
          setText(nextText);
          const parsed = textToInputs(nextText);
          setTrackedInputs(parsed);
          updateNodeConfig(id, { inputs: parsed });
        }}
      />
    </label>
  );
}

// `classify`/`prompt`/`agent` nodes' response_schema is plain JSON Schema, but
// authoring one by hand for a flat set of output fields is needless ceremony.
// Edited as one "name: type[ #description]" per line -- the same "name: value"
// convention as inputsToText/textToInputs above -- and converted to a strict
// JSON Schema object (every field required, additionalProperties: false, since
// that's what with_structured_output(method="json_schema", strict=True) needs).
// Schemas this can't represent (anything not a flat object of primitives --
// $ref indirection, nested objects, oneOf, etc.) round-trip to an empty list;
// authors needing that still reach it via the graph-level "Edit as JSON" dock.
const SCHEMA_FIELD_TYPE_ALIASES = { bool: 'boolean', str: 'string', int: 'integer', num: 'number' };
const SCHEMA_FIELD_PRIMITIVE_TYPES = ['string', 'number', 'integer', 'boolean'];

function parseSchemaFieldType(raw) {
  const trimmed = raw.trim();
  const enumMatch = trimmed.match(/^enum\((.*)\)$/i);
  if (enumMatch) {
    return { type: 'string', enum: enumMatch[1].split(',').map((value) => value.trim()).filter(Boolean) };
  }
  const arrayMatch = trimmed.match(/^(\w+)\[\]$/);
  if (arrayMatch) {
    const itemType = SCHEMA_FIELD_TYPE_ALIASES[arrayMatch[1].toLowerCase()] || arrayMatch[1].toLowerCase();
    return { type: 'array', items: { type: SCHEMA_FIELD_PRIMITIVE_TYPES.includes(itemType) ? itemType : 'string' } };
  }
  const normalized = SCHEMA_FIELD_TYPE_ALIASES[trimmed.toLowerCase()] || trimmed.toLowerCase();
  return { type: SCHEMA_FIELD_PRIMITIVE_TYPES.includes(normalized) ? normalized : 'string' };
}

// eslint-disable-next-line react-refresh/only-export-components
export function schemaFieldsToText(schema) {
  const properties = schema?.properties;
  if (!properties || typeof properties !== 'object') return '';
  return Object.entries(properties).map(([name, def]) => {
    let typeText;
    if (Array.isArray(def?.enum)) typeText = `enum(${def.enum.join(', ')})`;
    else if (def?.type === 'array') typeText = `${def.items?.type || 'string'}[]`;
    else typeText = def?.type || 'string';
    return def?.description ? `${name}: ${typeText} # ${def.description}` : `${name}: ${typeText}`;
  }).join('\n');
}

// eslint-disable-next-line react-refresh/only-export-components
export function textToSchemaFields(text) {
  const properties = {};
  const required = [];
  text.split('\n').forEach((line) => {
    const separator = line.indexOf(':');
    if (separator === -1) return;
    const name = line.slice(0, separator).trim();
    if (!name) return;
    const rest = line.slice(separator + 1).trim();
    const hashIndex = rest.indexOf('#');
    const typeRaw = (hashIndex === -1 ? rest : rest.slice(0, hashIndex)).trim();
    const description = hashIndex === -1 ? '' : rest.slice(hashIndex + 1).trim();
    const def = parseSchemaFieldType(typeRaw || 'string');
    properties[name] = description ? { ...def, description } : def;
    required.push(name);
  });
  return { type: 'object', properties, required, additionalProperties: false };
}

function ResponseSchemaField({ id, config, field, label, updateNodeConfig }) {
  const [text, setText, setTrackedValue] = useBufferedFieldText(id, config?.[field], schemaFieldsToText);
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">{label}</span>
      <textarea
        className="agents-graph-node__prompt"
        value={text}
        placeholder={'category: enum(billing, technical, other)\nsummary: string # one sentence recap of the request'}
        onChange={(event) => {
          const nextText = event.target.value;
          setText(nextText);
          const parsed = textToSchemaFields(nextText);
          setTrackedValue(parsed);
          updateNodeConfig(id, { [field]: parsed });
        }}
      />
    </label>
  );
}


function KeyValueMapField({ id, config, field, label, placeholder, updateNodeConfig }) {
  const [text, setText, setTrackedValue] = useBufferedFieldText(id, config?.[field], inputsToText);
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">{label}</span>
      <textarea
        className="agents-graph-node__prompt"
        value={text}
        placeholder={placeholder}
        onChange={(event) => {
          const nextText = event.target.value;
          setText(nextText);
          const parsed = textToInputs(nextText);
          setTrackedValue(parsed);
          updateNodeConfig(id, { [field]: parsed });
        }}
      />
    </label>
  );
}


function StringListField({ id, config, field, label, placeholder, updateNodeConfig }) {
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">{label}</span>
      <input
        className="agents-graph-node__toolinput"
        value={(config?.[field] || []).join(', ')}
        placeholder={placeholder}
        onChange={(event) => updateNodeConfig(id, {
          [field]: event.target.value.split(',').map((value) => value.trim()).filter(Boolean),
        })}
      />
    </label>
  );
}


function NumberField({ id, config, field, label, placeholder, updateNodeConfig, min = 1 }) {
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">{label}</span>
      <input
        type="number"
        min={min}
        className="agents-graph-node__toolinput"
        value={config?.[field] ?? ''}
        placeholder={placeholder}
        onChange={(event) => updateNodeConfig(id, {
          [field]: event.target.value === '' ? undefined : Number(event.target.value),
        })}
      />
    </label>
  );
}

// Kind-specific config fields, mirroring app.progenitor.routines.graph's per-kind
// config shape (prompt_template/system_prompt, expression, rubric_prompt/
// response_schema, agent_name + prompt_template/inputs). `noop` has no config.
function NodeConfigBody({ id, kind, config, updateNodeConfig, replaceNodeConfig }) {
  if (kind === 'prompt') {
    return (
      <>
        <ConfigField id={id} config={config} field="prompt_template" label="Prompt" placeholder="Template… use {request} / {other_node} to reference state" multiline updateNodeConfig={updateNodeConfig} />
        <ConfigField id={id} config={config} field="system_prompt" label="System prompt (optional)" placeholder="Node-specific system prompt…" multiline updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'eval' || kind === 'condition' || kind === 'switch') {
    return (
      <ExpressionField id={id} config={config} field="expression" label="Expression" placeholder="Restricted Python expression…" updateNodeConfig={updateNodeConfig} autocomplete />
    );
  }
  if (kind === 'classify') {
    return (
      <>
        <ConfigField id={id} config={config} field="rubric_prompt" label="Prompt" placeholder="e.g. Categorize this request: {request}" multiline updateNodeConfig={updateNodeConfig} />
        <ResponseSchemaField id={id} config={config} field="response_schema" label="Response fields (required)" updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'agent') {
    return (
      <>
        <AgentSelectField id={id} config={config} label="Agent" agentKind="object" updateNodeConfig={updateNodeConfig} />
        <ConfigField id={id} config={config} field="prompt_template" label="Prompt" placeholder="Template sent to the agent…" multiline updateNodeConfig={updateNodeConfig} />
        <InputsField id={id} config={config} updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'a2a') {
    return (
      <>
        <AgentSelectField id={id} config={config} label="Agent" agentKind="a2a" updateNodeConfig={updateNodeConfig} />
        <ConfigField id={id} config={config} field="prompt_template" label="Prompt" placeholder="Template sent to the peer…" multiline updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'human') {
    return (
      <>
        <ConfigField id={id} config={config} field="message" label="Approval message" placeholder="What should a person approve?" multiline updateNodeConfig={updateNodeConfig} />
        <StringListField id={id} config={config} field="allowed_responses" label="Allowed responses (comma-separated)" placeholder="approve, reject" updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'foreach') {
    const mode = config?.mode || 'send';
    return (
      <>
        <label className="agents-graph-node__field">
          <span className="agents-graph-node__field-label">Execution mode</span>
          <select
            className="agents-graph-node__toolinput"
            value={mode}
            onChange={(event) => replaceNodeConfig(
              event.target.value === 'agent'
                ? { mode: 'agent', items_from: '', agent_name: '', prompt_template: '', item_key: 'item' }
                : { mode: 'send', items_from: '', item_key: 'item' },
            )}
          >
            <option value="send">Send to connected node</option>
            <option value="agent">Invoke named agent</option>
          </select>
        </label>
        <ConfigField id={id} config={config} field="items_from" label="Items from data path" placeholder="items" updateNodeConfig={updateNodeConfig} />
        <ConfigField id={id} config={config} field="item_key" label="Item state key" placeholder="item" updateNodeConfig={updateNodeConfig} />
        <NumberField id={id} config={config} field="max_items" label="Maximum items (optional)" placeholder="100" updateNodeConfig={updateNodeConfig} />
        {mode === 'agent' ? (
          <>
            <AgentSelectField id={id} config={config} label="Agent" agentKind="object" updateNodeConfig={updateNodeConfig} />
            <ConfigField id={id} config={config} field="prompt_template" label="Prompt per item" placeholder="Handle {item}" multiline updateNodeConfig={updateNodeConfig} />
            <NumberField id={id} config={config} field="max_concurrency" label="Maximum concurrency (optional)" placeholder="4" updateNodeConfig={updateNodeConfig} />
          </>
        ) : null}
      </>
    );
  }
  if (kind === 'routine') {
    return (
      <>
        <ConfigField id={id} config={config} field="routine_name" label="Routine name" placeholder="shared-validation-routine" updateNodeConfig={updateNodeConfig} />
        <KeyValueMapField id={id} config={config} field="input_map" label="Input map (child field: parent data path)" placeholder="request: request" updateNodeConfig={updateNodeConfig} />
        <KeyValueMapField id={id} config={config} field="output_map" label="Output map (parent field: child data path)" placeholder="result: answer" updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  return null;
}

// The state key an already-routed node's branches route on lives here.
// `condition` and `split` are exempt: `condition` is born a router over a
// fixed `true`/`false` pair keyed on its own output, and `split` never
// evaluates anything -- its branches all fire unconditionally, so there is
// no state key to author for either. A node with no branches yet isn't
// offered a way to become a router from here -- routing is chosen at
// insert time by picking a routing-capable node kind (`condition`,
// `switch`, `split`, `foreach`), not toggled on after the fact.
function RoutingFooter({ id, kind, routerEdge }) {
  const { onChangeStateKey, onRemoveBranching } = useContext(GraphEditContext);
  if (kind === 'condition' || kind === 'split' || !routerEdge) return null;
  return (
    <div className="agents-graph-node__routing">
      <input
        className="agents-graph-node__toolinput"
        placeholder="route on <key>"
        onChange={(event) => onChangeStateKey(id, event.target.value)}
      />
      <button type="button" className="agents-graph-node__unsplit" onClick={() => onRemoveBranching(id)} title="Remove branching">
        Remove branching
      </button>
    </div>
  );
}


function formatSeconds(ms) {
  const seconds = ms / 1000;
  return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
}

// Ticks once a second while a node is presumed active (see
// deriveActiveNodeIds/estimatedActiveStartMs) -- isolated in its own
// component so only this one card re-renders each tick, not the whole
// canvas. This is a client-side estimate, not a real measured duration: it
// starts from the latest known finish time across the whole run (or the
// Run click itself, before anything has finished), since LangGraph's
// checkpointer only ever records a node's real duration_ms AFTER it
function RunningTimer({ startedAtMs }) {
  const [elapsedMs, setElapsedMs] = useState(0);
  useEffect(() => {
    let timer;
    const tick = () => {
      const next = Math.max(0, Date.now() - startedAtMs);
      setElapsedMs(next);
      // Sub-second precision (formatSeconds' tenths) only shows below the
      // 10s cutover -- tick fast enough to actually see it count up to
      // then, and back off to once a second after, so a long-running node
      // doesn't re-render 10x/sec for a display that no longer shows it.
      timer = setTimeout(tick, next < 10000 ? 100 : 1000);
    };
    tick();
    return () => clearTimeout(timer);
  }, [startedAtMs]);
  return <>{formatSeconds(elapsedMs)}…</>;
}

// The sub-header under a step's title once run data exists for it -- a
// finalized "Xs · N tok" once its own checkpoint has landed, or a ticking
// "estimated live" timer while it's presumed still executing. Renders
// nothing at all otherwise (see StepCard), not a placeholder row.
// Given a node's own run_meta entries and the iteration currently selected
// for its enclosing loop (see buildLoopMembership/LoopBlock), returns the
// entry belonging to that iteration -- bucketed by each entry's `step`
// (factory.py's _instrument_node_callable) against the guard node's own
// per-iteration step, since a loop-body node can run more than once and
// "just take the last entry" would otherwise always show the latest
// iteration regardless of which one is selected. `iterationIndex` null
// means "latest" (today's default, no override).
// eslint-disable-next-line react-refresh/only-export-components
export function pickEntryForIteration(entries, iterationIndex, guardEntries) {
  if (!entries?.length) return undefined;
  if (iterationIndex == null) return entries[entries.length - 1];
  const upper = guardEntries?.[iterationIndex - 1]?.step ?? Infinity;
  const lower = guardEntries?.[iterationIndex - 2]?.step ?? -Infinity;
  const inWindow = entries.filter((entry) => typeof entry.step === 'number' && entry.step > lower && entry.step <= upper);
  return inWindow[inWindow.length - 1] ?? entries[entries.length - 1];
}

function StepRunSummary({ nodeId }) {
  const { runMeta, activeNodeIds, estimatedActiveStartMs, staleNodeIds, warmingUp, nodes, loopMembership, loopIterations } = useContext(GraphEditContext);
  const entries = runMeta?.[nodeId];
  const isActive = activeNodeIds?.has(nodeId);
  if (!entries?.length) {
    // Nothing anywhere has completed yet -- this can only be the entry
    // node (see deriveActiveNodeIds), and there's no real anchor to count
    // from yet. Start's own pulsing glow (see ChainCap) carries that
    // signal instead of a click-time-anchored guess here.
    if (!isActive || warmingUp) return null;
    return (
      <div className="routine-step-card__run routine-step-card__run--active">
        <span className="routine-step-card__run-dot" aria-hidden="true" />
        <span className="routine-step-card__run-time"><RunningTimer startedAtMs={estimatedActiveStartMs} /></span>
      </div>
    );
  }
  // A node inside a loop body follows that loop's own selected iteration
  // (see LoopBlock's prev/next nav) instead of always showing its latest
  // execution -- null (no loop, or nothing explicitly selected yet) keeps
  // today's "last entry" default.
  const guardEdge = loopMembership?.get(nodeId);
  const guardEntries = guardEdge ? runMeta?.[guardEdge.from] : null;
  const selectedIteration = guardEdge ? (loopIterations?.[guardEdge.from] ?? null) : null;
  const last = pickEntryForIteration(entries, selectedIteration, guardEntries);
  const tokens = last.tokens?.total_tokens;
  // Cached run details predate an edit made to this step (or something
  // upstream of it) since -- still shown (still real data, just no longer
  // guaranteed to reflect the current graph), just visually muted with a
  // note instead of read as current. See lib/routineGraphDiff.js.
  const stale = !isNodeRunValid(nodeId, staleNodeIds || EMPTY_MISSING);
  // A raised node still gets an entry (see factory.py's
  // _instrument_node_callable): real duration, plus `error` instead of
  // `tokens` -- surfaced as a red variant of this same badge rather than a
  // separate element, so a step's run state is always in one place.
  const failed = Boolean(last.error);
  // `route`, present only on a router/loop_guard node's own entry, is
  // which target it actually sent execution to this time (see
  // factory.py's route_fn) -- resolved to that node's label the same way
  // the canvas already labels everything else, not shown as a raw id.
  const routeLabel = last.route == null ? null
    : (Array.isArray(last.route) ? last.route : [last.route])
      .map((targetId) => (nodes || []).find((node) => node.id === targetId)?.label || targetId)
      .join(', ');
  return (
    <div
      className={`routine-step-card__run${stale ? ' routine-step-card__run--stale' : ''}${failed ? ' routine-step-card__run--error' : ''}`}
      title={[
        failed ? last.error : null,
        typeof last.step === 'number' ? `Step ${last.step}` : null,
        routeLabel ? `Routed to ${routeLabel}` : null,
        entries.length > 1 ? `${entries.length} executions this run` : null,
        stale ? 'Graph changed since this ran' : null,
      ].filter(Boolean).join(' \u2014 ') || undefined}
    >
      <span className="routine-step-card__run-time">{formatSeconds(last.duration_ms)}</span>
      {failed ? <span className="routine-step-card__run-error-badge" aria-hidden="true">failed</span> : null}
      {!failed && typeof tokens === 'number' ? <span className="routine-step-card__run-tokens">{tokens} tok</span> : null}
      {stale ? <span className="routine-step-card__run-stale-badge" aria-hidden="true">stale</span> : null}
    </div>
  );
}
// Compact map card. Editing lives in NodeInspector so the chain stays readable.
function StepCard({ node }) {
  const { selectNode, selectedNodeId, edges, readOnly } = useContext(GraphEditContext);
  // A step can arrive incomplete from the JSON editor or the runtime, not just
  // from the picker, so the chain has to say which one is holding the save.
  const incomplete = !isNodeComplete(node);
  // A loop's start is a plain `noop` on the wire (see insertAt's `kind ===
  // 'loop'` comment) -- styled as its own kind here purely for legibility,
  // the same way it already gets its own picker entry and inspector badge.
  const isLoopStart = edges.some((edge) => edge.loop_guard && edge.to === node.id);
  const displayKind = isLoopStart ? 'loop' : node.kind;
  return (
    <div
      className={`canvas-node agents-graph-node agents-graph-node--${displayKind} routine-step-card${selectedNodeId === node.id ? ' selected' : ''}${incomplete ? ' routine-step-card--incomplete' : ''}${readOnly ? ' routine-step-card--readonly' : ''}`}
      data-node-id={node.id}
      style={{ '--node-color': GRAPH_KIND_COLORS[displayKind] || '#8b949e' }}
      role={readOnly ? undefined : 'button'}
      tabIndex={readOnly ? undefined : 0}
      aria-label={`${node.label || node.id} ${isLoopStart ? 'Loop' : (GRAPH_KIND_LABELS[node.kind] || node.kind)} step${incomplete ? ', incomplete' : ''}`}
      onClick={readOnly ? undefined : (event) => selectNode(node.id, event)}
      onKeyDown={readOnly ? undefined : (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          selectNode(node.id, event);
        }
      }}
    >
      <div className="node-title-bar routine-step-card__title">
        <span className="node-type-icon"><KindIcon kind={displayKind} /></span>
        <span className="routine-step-card__copy">
          <strong className="routine-step-card__label">{node.label || node.id}</strong>
        </span>
        {incomplete ? <span className="routine-step-card__incomplete" title="Required fields are empty">!</span> : null}
      </div>
      <StepRunSummary nodeId={node.id} />
    </div>
  );
}

// Full configuration for the selected node. It floats inside the canvas and
// is deliberately separate from the chain so large prompts never resize it.
function NodeInspector({ node, routerEdge, guardEdge, onClose, onDismiss, isNew, style, panelRef }) {
  const { updateNode, deleteNode, updateLoopGuard, runMeta, runData, staleNodeIds, edges: graphEdges, nodes: graphNodes, readOnly, runStatus, startRun, hasInteractiveSession, resetInteractiveSession, loopMembership, loopIterations } = useContext(GraphEditContext);
  const [isEditingLabel, setIsEditingLabel] = useState(false);
  const [confirmRunOpen, setConfirmRunOpen] = useState(false);
  const [labelDraft, setLabelDraft] = useState('');
  const labelInputRef = useRef(null);
  const inspectedNodeIdRef = useRef(node?.id);

  // A different node was selected -- drop any in-flight edit on the old one
  // rather than let it leak into the newly opened step. Adjusted during
  // render (React's documented "reset state when a prop changes" pattern)
  // instead of an effect, since an effect would commit the stale label
  // input for one extra frame before catching up.
  if (inspectedNodeIdRef.current !== node?.id) {
    inspectedNodeIdRef.current = node?.id;
    if (isEditingLabel) setIsEditingLabel(false);
    if (confirmRunOpen) setConfirmRunOpen(false);
  }

  useEffect(() => {
    if (!isEditingLabel) return undefined;
    labelInputRef.current?.focus();
    labelInputRef.current?.select();
    return undefined;
  }, [isEditingLabel]);

  if (!node) return null;
  const runEntries = runMeta?.[node.id];
  // Follows the enclosing loop's own selected iteration, same as the
  // compact canvas badge (see StepRunSummary/pickEntryForIteration) --
  // clicking through a loop's prev/next nav re-points this panel at that
  // iteration's own duration/tokens/route/output, not always the latest.
  const inspectorGuardEdge = loopMembership?.get(node.id);
  const inspectorGuardEntries = inspectorGuardEdge ? runMeta?.[inspectorGuardEdge.from] : null;
  const inspectorSelectedIteration = inspectorGuardEdge ? (loopIterations?.[inspectorGuardEdge.from] ?? null) : null;
  const lastRunEntry = pickEntryForIteration(runEntries, inspectorSelectedIteration, inspectorGuardEntries);
  const isRunStale = !isNodeRunValid(node.id, staleNodeIds || EMPTY_MISSING);
  const outputKey = node.output_key || node.id;
  // Prefers this execution's own recorded write (see factory.py's
  // `entry.output`) over the cumulative `data` channel, which only ever
  // holds the latest write per key -- falls back to `runData` for older
  // runs recorded before that field existed, or a node with no data write
  // at all this execution.
  const runOutputValue = lastRunEntry?.output && outputKey in lastRunEntry.output
    ? lastRunEntry.output[outputKey]
    : runData?.[outputKey];
  const runOutputText = runOutputValue === undefined
    ? undefined
    : (typeof runOutputValue === 'string' ? runOutputValue : JSON.stringify(runOutputValue, null, 2));
  const handleConfig = (nodeId, patch) => updateNode(nodeId, { config: { ...node.config, ...patch } });
  // A loop's own guard expression lives on its tail's edge (`loop_guard`),
  // not on this node's own config -- there's no separate node kind for it
  // (see insertAt's `kind === 'loop'` comment), so `config`/`updateNodeConfig`
  // here are a shim over `updateLoopGuard` instead of `node.config`. Reuses
  // ExpressionField exactly as Eval/Condition do; the guard just isn't
  // state this node's own config actually holds.
  const guardConfig = guardEdge?.loop_guard;
  const handleGuardConfig = (_nodeId, patch) => updateLoopGuard(guardEdge.from, patch);
  const unmet = missingRequirements(node);
  const guardIncomplete = guardEdge && !guardConfig.expression?.trim();
  // One requirement, not one field: a one-of group ("a template or a file")
  // is a single unmet requirement offering two ways to satisfy it -- counting
  // every alternative name via a flattened list told the author "2 fields
  // are required" when only one alternative (the other is a legacy field
  // never shown in this UI) was even visible to fill in.
  const missingCount = unmet.length + (guardIncomplete ? 1 : 0);
  const missingSet = missingCount ? new Set([...unmet.flat(), ...(guardIncomplete ? ['expression'] : [])]) : EMPTY_MISSING;
  // Held open until the step can actually be saved. Closing here would strand
  // an unsaveable step somewhere in the chain with the whole graph withheld
  // behind it, so the only ways out are filling the fields or deleting it.
  const locked = unmet.length > 0 || guardIncomplete;
  const startLabelEdit = () => {
    setLabelDraft(node.label || '');
    setIsEditingLabel(true);
  };
  const cancelLabelEdit = () => setIsEditingLabel(false);
  const saveLabelEdit = () => {
    updateNode(node.id, { label: labelDraft.replace(/\s+/g, ' ').trim() });
    setIsEditingLabel(false);
  };
  return (
    <aside
      ref={panelRef}
      className={`routine-node-inspector${locked ? ' routine-node-inspector--locked' : ''}`}
      style={{ ...style, '--node-color': GRAPH_KIND_COLORS[guardEdge ? 'loop' : node.kind] || '#8b949e' }}
      aria-label={`${node.label || node.id} details`}
    >
      <header className="routine-node-inspector__head">
        <span className="node-type-icon"><KindIcon kind={guardEdge ? 'loop' : node.kind} /></span>
        <div>
          <span className="routine-node-inspector__title">
            {isEditingLabel ? (
              <input
                ref={labelInputRef}
                type="text"
                className="routine-node-inspector__label-input"
                value={labelDraft}
                placeholder={node.id}
                aria-label="Step label"
                onChange={(event) => setLabelDraft(event.target.value)}
                onBlur={cancelLabelEdit}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    saveLabelEdit();
                  } else if (event.key === 'Escape') {
                    event.preventDefault();
                    cancelLabelEdit();
                  }
                }}
              />
            ) : (
              <strong className="routine-node-inspector__title-text">{node.label || node.id}</strong>
            )}
            <button
              type="button"
              className="routine-node-inspector__rename"
              data-tooltip={isEditingLabel ? 'Save step label' : 'Rename step'}
              aria-label={isEditingLabel ? 'Save step label' : 'Rename step'}
              onMouseDown={isEditingLabel ? (event) => event.preventDefault() : undefined}
              onClick={isEditingLabel ? saveLabelEdit : startLabelEdit}
            >
              {isEditingLabel ? (
                <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
                  <path d="M3.25 8.25 6.5 11.5l6.25-6.75" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              ) : (
                <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
                  <path d="M2 14h3l8-8-3-3-8 8z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
                </svg>
              )}
            </button>
          </span>
          <span className="routine-node-inspector__kind">{guardEdge ? 'Loop' : (GRAPH_KIND_LABELS[node.kind] || node.kind)}</span>
        </div>
        <button
          type="button"
          className="routine-node-inspector__close"
          onClick={onClose}
          title={locked ? (isNew ? 'Close and discard this step' : 'Close and discard changes') : undefined}
          aria-label="Close node details"
        >
          ×
        </button>
      </header>
      <MissingFieldsContext.Provider value={missingSet}>
      <div className="routine-node-inspector__body">
        {missingCount ? (
          <p className="routine-node-inspector__required-hint" role="status">
            {missingCount === 1
              ? 'One field is required before this step can be saved.'
              : `${missingCount} fields are required before this step can be saved.`}
            {' '}
            {isNew ? 'Fill it in, or close to discard this step.' : 'Fill it in, or close to discard your changes.'}
          </p>
        ) : null}
        {guardEdge ? (
          <ExpressionField id={guardEdge.from} config={guardConfig} field="expression" label="Continue while" placeholder="e.g. attempt < 3" updateNodeConfig={handleGuardConfig} autocomplete />
        ) : null}
        <NodeConfigBody
          id={node.id}
          kind={node.kind}
          config={node.config}
          updateNodeConfig={handleConfig}
          replaceNodeConfig={(config) => updateNode(node.id, { config })}
        />
        <RoutingFooter id={node.id} kind={node.kind} routerEdge={routerEdge} />
        {runEntries?.length ? (
          <div className={`routine-node-inspector__run${isRunStale ? ' routine-node-inspector__run--stale' : ''}${lastRunEntry.error ? ' routine-node-inspector__run--error' : ''}`}>
            {isRunStale ? (
              <p className="routine-node-inspector__run-stale-notice" role="status">
                Graph changed since this ran -- details below may no longer be accurate.
              </p>
            ) : null}
            <div className="routine-node-inspector__run-head">
              <span>{inspectorSelectedIteration != null ? `Iteration ${inspectorSelectedIteration} run` : 'Last run'}</span>
              {runEntries.length > 1 ? (
                <span className="routine-node-inspector__run-count">{runEntries.length} executions this run</span>
              ) : null}
            </div>
            {lastRunEntry.error ? (
              <p className="routine-node-inspector__run-error" role="alert">{lastRunEntry.error}</p>
            ) : null}
            <dl className="routine-node-inspector__run-stats">
              <div>
                <dt>Duration</dt>
                <dd>{formatSeconds(lastRunEntry.duration_ms)}</dd>
              </div>
              {typeof lastRunEntry.step === 'number' ? (
                <div>
                  <dt>Step</dt>
                  <dd>{lastRunEntry.step}</dd>
                </div>
              ) : null}
              {lastRunEntry.tokens ? (
                <div>
                  <dt>Tokens</dt>
                  <dd>{lastRunEntry.tokens.total_tokens} ({lastRunEntry.tokens.input_tokens} in / {lastRunEntry.tokens.output_tokens} out)</dd>
                </div>
              ) : null}
              {lastRunEntry.route != null ? (
                <div>
                  <dt>Routed to</dt>
                  <dd>
                    {(Array.isArray(lastRunEntry.route) ? lastRunEntry.route : [lastRunEntry.route])
                      .map((targetId) => (graphNodes || []).find((n) => n.id === targetId)?.label || targetId)
                      .join(', ')}
                  </dd>
                </div>
              ) : null}
            </dl>
            {runOutputText !== undefined ? (
              <div className="routine-node-inspector__run-output">
                <span className="routine-node-inspector__run-output-label">Output written to &quot;{outputKey}&quot;</span>
                <pre>{runOutputText}</pre>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
      </MissingFieldsContext.Provider>
      <footer className="routine-node-inspector__foot">
        {readOnly ? null : (
          <div className="routine-node-inspector__run-actions">
            <button
              type="button"
              className="routine-node-inspector__run-to-here"
              title="Execute the routine from wherever this editor session last stopped, through this step"
              disabled={runStatus === 'discovering' || runStatus === 'running'}
              onClick={() => {
                if (nodeRequiresRunConfirmation(node.id, graphNodes, graphEdges, runMeta)) {
                  setConfirmRunOpen(true);
                } else {
                  startRun(node.id);
                }
              }}
            >
              Run to here
            </button>
            {hasInteractiveSession ? (
              <button
                type="button"
                className="routine-node-inspector__reset-run"
                title={'Abandon this editor session\u2019s progress -- the next "Run to here" click starts over from the beginning instead of resuming'}
                disabled={runStatus === 'discovering' || runStatus === 'running'}
                onClick={resetInteractiveSession}
              >
                Reset run
              </button>
            ) : null}
          </div>
        )}
        <button type="button" className="agents-graph-node__delete" onClick={() => { deleteNode(node.id); onDismiss(); }}>
          Delete step
        </button>
      </footer>
      {confirmRunOpen ? (
        <PopupModal
          eyebrow="Run to here"
          title="This will call a live agent"
          message="Reaching this step runs at least one step with a real side effect (a live agent/routine delegation, an A2A call, or a human approval gate) that hasn't run yet this session. Continue?"
          confirmLabel="Run"
          onClose={() => setConfirmRunOpen(false)}
          onConfirm={() => { setConfirmRunOpen(false); startRun(node.id); }}
        />
      ) : null}
    </aside>
  );
}

// Fixed Start/End chrome. Never a schema node -- Start is purely "the chain
// begins here" (points at `entry`), End is purely "this path terminates
// here" (a node with no outgoing edge/branch). Rendered once per terminus,
// so a branching routine can show multiple Ends -- that's real, not a bug.
// Pulses while the run has kicked off but nothing anywhere has completed
// yet (see `warmingUp` in GraphEditContext) -- covers the client discovery
// poll plus backend routine-compile/model-connect latency before the
// entry node's own real duration exists, so the entry's card itself stays
// quiet (see StepRunSummary) instead of showing a click-time-anchored
// guess. Stops the instant the first run_meta entry lands anywhere.
function ChainCap({ kind }) {
  const { warmingUp } = useContext(GraphEditContext);
  const pulsing = kind === 'start' && warmingUp;
  return (
    <div className={`routine-cap routine-cap--${kind}${pulsing ? ' routine-cap--warming' : ''}`}>
      {kind === 'start' ? 'Start' : 'End'}
    </div>
  );
}

// A cycle (retry loop) or a rejoin (two branches converging on the same
// node) both terminate the run they're rendered in rather than expanding
// forever or duplicating a subtree. Loops are real (retry patterns); rejoins
// are real (branches meeting back up) -- both are schema-legal edge targets.
function ChainChip({ kind, label }) {
  return <div className={`routine-chip routine-chip--${kind}`}>{label}</div>;
}

// The `+` on every connector: click to open a kind picker and insert a step
// at exactly that point in the chain.
function ConnectorInsert({ slot }) {
  const { insertAt, onJoinBranches, readOnly } = useContext(GraphEditContext);
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const handlePointer = (event) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) {
        setOpen(false);
      }
    };
    const handleKey = (event) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointer);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handlePointer);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  return (
    <div className="routine-connector" ref={wrapRef}>
      <span className="routine-connector__line" aria-hidden="true" />
      {readOnly ? null : (
        <>
          <button
            type="button"
            className="routine-connector__btn"
            onClick={() => setOpen((current) => !current)}
            aria-label="Insert step here"
            aria-expanded={open}
          >
            <span className="routine-connector__btn-glyph" aria-hidden="true">+</span>
          </button>
          {open ? (
            <div className="routine-connector__menu" role="menu">
              {GRAPH_NODE_KINDS.map((item) => (
                <button
                  key={item.kind}
                  type="button"
                  role="menuitem"
                  className="routine-connector__menu-item"
                  onClick={() => {
                    if (slot.kind === 'join') onJoinBranches(slot.sourceId, item.kind);
                    else insertAt(slot, item.kind);
                    setOpen(false);
                  }}
                >
                  <span className={`canvas-dock-icon ${item.kind}`}><KindIcon kind={item.kind} /></span>
                  {item.label}
                </button>
              ))}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

// Editable branch-case chip: the column header for one fork arm. Mirrors
// Power Automate's per-case label -- double-click (or just focus) to rename.
function BranchHeader({
  value,
  isDefault,
  onRename,
  onRemove,
  collapsed,
  onToggleCollapse,
  readOnly = false,
  hideLabel = false,
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  function commit() {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== value) onRename(trimmed);
    else setDraft(value);
  }
  return (
    <div className={`routine-fork__header ${isDefault ? 'routine-fork__header--default' : ''}`}>
      <button
        type="button"
        className="routine-fork__header-collapse"
        onClick={onToggleCollapse}
        aria-expanded={!collapsed}
        aria-label={collapsed ? 'Expand branch' : 'Collapse branch'}
        title={collapsed ? 'Expand branch' : 'Collapse branch'}
      >
        {collapsed ? '▸' : '▾'}
      </button>
      {hideLabel ? null : isDefault || readOnly ? (
        <span className="routine-fork__header-label">{isDefault ? 'Default' : value}</span>
      ) : editing ? (
        <input
          autoFocus
          className="routine-fork__header-input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => { if (event.key === 'Enter') commit(); }}
        />
      ) : (
        <button type="button" className="routine-fork__header-label" onClick={() => setEditing(true)} title="Rename branch">
          {value}
        </button>
      )}
      {!readOnly ? (
        <button type="button" className="routine-fork__header-remove" onClick={onRemove} title="Remove branch" aria-label="Remove branch">
          ×
        </button>
      ) : null}
    </div>
  );
}

// One edge record can encode a normal transition, a static fan-out, or a
// barrier fan-in. Indexing every listed source lets the same tree walk render
// a fan-in's shared destination once rather than pretending each source owns
// a separate normal edge.
function edgeSourceIds(edge) {
  return Array.isArray(edge?.from) ? edge.from : [edge?.from];
}

// Flow edges are the ones that shape the chain tree; error edges route
// retries on exhaustion and must NOT participate in chain layout.
export function isFlowEdge(edge) {
  return edge?.level !== 'error';
}

function edgeTargetIds(edge) {
  if (!edge?.to) return [];
  return Array.isArray(edge.to) ? edge.to : [edge.to];
}

function edgeBySourceMap(edges) {
  return new Map(
    edges
      .filter((edge) => edge && edge.from && isFlowEdge(edge))
      .flatMap((edge) => edgeSourceIds(edge).map((source) => [source, edge])),
  );
}

function routerTargets(edge) {
  return [...Object.values(edge?.branches || {}), ...(edge?.default ? [edge.default] : [])];
}

// nodeId -> the guard edge of its nearest enclosing guarded loop, for every
// node inside a loop's body box -- excludes the loop's own start card, the
// same boundary RoutineChain's own render walk uses (see
// `positions.get(bodyStartId) + 1` skipping past it below). Nested loops
// keep whichever guard's walk reaches a node last; nesting is rare and not
// disambiguated further here.
// eslint-disable-next-line react-refresh/only-export-components
export function buildLoopMembership(edges) {
  const bySource = edgeBySourceMap(edges || []);
  const membership = new Map();
  (edges || []).forEach((edge) => {
    if (!edge?.loop_guard) return;
    const startId = edgeTargetIds(edge)[0];
    const tailId = edge.from;
    if (!startId || !tailId) return;
    const seen = new Set([startId]);
    const stack = [startId];
    while (stack.length) {
      const id = stack.pop();
      if (id !== startId) membership.set(id, edge);
      if (id === tailId) continue;
      const next = bySource.get(id);
      if (!next) continue;
      const targets = next.router ? routerTargets(next) : edgeTargetIds(next);
      targets.forEach((target) => {
        if (target && !seen.has(target)) {
          seen.add(target);
          stack.push(target);
        }
      });
    }
  });
  return membership;
}

// Every step a join placed after this fork would fan in from: each lane walked
// to its end, descending through a nested fork so a lane that itself branches
// contributes all of its own ends instead of disqualifying the whole fork.
// Empty when there is nothing to bring together -- a lane that loops back,
// rejoins content already on the chain, or converges with a sibling leaves the
// fork without two distinct ends. The offer and the action both read this, so
// a fork that shows a `+` is exactly one `onJoinBranches` can wire up.
export function joinTerminals(edges, sourceId) {
  const router = edges.find((edge) => edge.from === sourceId && edge.router);
  if (!router) return [];
  const bySource = edgeBySourceMap(edges);
  const seen = new Set([sourceId]);
  const walk = (nodeId) => {
    if (!nodeId || seen.has(nodeId)) return null;
    seen.add(nodeId);
    const outgoing = bySource.get(nodeId);
    if (!outgoing) return [nodeId];
    if (outgoing.router) {
      const lanes = routerTargets(outgoing).map(walk);
      return lanes.some((lane) => lane === null) ? null : lanes.flat();
    }
    return walk(edgeTargetIds(outgoing)[0]);
  };
  const lanes = routerTargets(router).map(walk);
  if (lanes.some((lane) => lane === null)) return [];
  const terminals = [...new Set(lanes.flat())];
  return terminals.length > 1 ? terminals : [];
}

// Every node that all paths out of `startId` must pass through, nearest first.
// A plain chain is just its own steps. Where the chain forks, only what all of
// the fork's branches share survives -- which is precisely what makes a node a
// join for everything above it. Without descending, a lane that branches again
// contributed nothing and the outer fork could never find its join.
function dominatingPath(startId, edgeBySource, seen = new Set()) {
  const path = [];
  let current = startId;
  while (current && !seen.has(current)) {
    path.push(current);
    seen.add(current);
    const edge = edgeBySource.get(current);
    if (!edge) return path;
    if (edge.router) {
      // Each branch gets its own `seen`: sharing one would let the first
      // branch claim nodes the others also pass through, and those are exactly
      // the shared nodes the intersection is looking for.
      const branches = routerTargets(edge)
        .map((target) => dominatingPath(target, edgeBySource, new Set(seen)));
      if (!branches.length) return path;
      const [first, ...rest] = branches;
      return [...path, ...first.filter((id) => rest.every((branch) => branch.includes(id)))];
    }
    [current] = edgeTargetIds(edge);
  }
  return path;
}

function findCommonJoin(targetIds, edgeBySource) {
  if (targetIds.length < 2) return null;
  const paths = targetIds.map((targetId) => dominatingPath(targetId, edgeBySource));
  return paths[0].find((candidate) => paths.every((path) => path.includes(candidate))) || null;
}

// The fan_in a fork's lanes already converge on, if any. `findCommonJoin`
// only accepts a node every lane reaches, so adding or dropping a lane has to
// keep that edge in step: one unjoined lane drops the common node to null and
// the join stops rendering below the fork at all. At runtime it is worse than
// cosmetic -- the factory gives any node with no outgoing edge an edge
// straight to the terminal, so taking that branch ends the routine early
// instead of continuing past the fork.
// eslint-disable-next-line react-refresh/only-export-components
export function forkJoin(edges, router) {
  const edgeBySource = edgeBySourceMap(edges);
  const joinId = findCommonJoin(routerTargets(router), edgeBySource);
  if (!joinId) return null;
  const edge = edges.find((candidate) => candidate.level === 'fan_in' && candidate.to === joinId);
  return edge ? { joinId, edge, edgeBySource } : null;
}

// Every node the graph can still get to from `entry`. Shared by validation and
// by lane removal, which decides what a dropped lane owned by diffing this
// before and after the drop.
//
// This walks BOTH flow edges and error edges: a node reachable only as an
// error edge target must NOT be reported unreachable. Chain-shape walks
// (buildChainTree, dominatingPath) exclude error edges via edgeBySourceMap.
// eslint-disable-next-line react-refresh/only-export-components
export function reachableFrom(entry, edges) {
  const reachable = new Set();
  const visit = (id) => {
    if (!id || reachable.has(id)) return;
    reachable.add(id);
    edges.filter((edge) => edgeSourceIds(edge).includes(id)).forEach((edge) => {
      if (edge.router) {
        Object.values(edge.branches || {}).forEach(visit);
        visit(edge.default);
      } else {
        visit(edge.to);
        if (edge.loop_guard) visit(edge.loop_guard.on_false);
      }
    });
  };
  visit(entry);
  return reachable;
}

// The reverse of `reachableFrom`: every node whose output could have run
// before `nodeId` on SOME path through the graph -- used to offer Inputs
// autocomplete candidates. Deliberately excludes error edges (isFlowEdge)
// unlike `reachableFrom`: a node only reachable via a recovery path isn't a
// normal-path predecessor an author should be pointed at by an autocomplete
// menu. This is a superset, not a soundness proof -- graph reachability
// can't tell "always ran before you" from "reachable on some branch/loop
// path", so a branch sibling or a not-yet-taken loop pass can still show up
// even though it may not actually be populated at runtime. Cheaper to over-
// suggest a sometimes-absent name than to hide a valid one.
// eslint-disable-next-line react-refresh/only-export-components
export function predecessorsOf(nodeId, edges) {
  const flowEdges = (edges || []).filter(isFlowEdge);
  const predecessors = new Set();
  const visit = (id) => {
    flowEdges.forEach((edge) => {
      const targets = edge.router
        ? routerTargets(edge)
        : [...edgeTargetIds(edge), ...(edge.loop_guard?.on_false ? [edge.loop_guard.on_false] : [])];
      if (!targets.includes(id)) return;
      edgeSourceIds(edge).forEach((src) => {
        if (!src || src === nodeId || predecessors.has(src)) return;
        predecessors.add(src);
        visit(src);
      });
    });
  };
  visit(nodeId);
  return predecessors;
}

// Node kinds with a real external side effect: a live agent/routine
// delegation, an A2A call, or a human approval gate. Everything else
// (eval/condition/switch/split/noop) is pure/deterministic -- re-running
// it has no observable consequence outside this graph's own state.
const SIDE_EFFECTING_KINDS = new Set(['agent', 'routine', 'a2a', 'human']);

// Whether "run to here" for `targetNodeId` would, for the first time this
// interactive session, execute a node with a real side effect --
// `interrupt_after=[targetNodeId]` (see subagent.py) means everything
// UP TO AND INCLUDING the target runs, so both its predecessors and the
// clicked node itself matter here. A node that already carries a run_meta
// entry this session already had its side effect happen once and won't
// happen again on this click (LangGraph resumes past whatever's already
// checkpointed, see subagent.py's `_run`) -- only a genuinely new
// execution needs a confirmation.
// eslint-disable-next-line react-refresh/only-export-components
export function nodeRequiresRunConfirmation(targetNodeId, nodes, edges, runMeta) {
  const nodesById = new Map((nodes || []).map((node) => [node.id, node]));
  const completed = new Set(Object.keys(runMeta || {}).filter((id) => (runMeta[id] || []).length > 0));
  for (const id of [...predecessorsOf(targetNodeId, edges), targetNodeId]) {
    if (completed.has(id)) continue;
    if (SIDE_EFFECTING_KINDS.has(nodesById.get(id)?.kind)) return true;
  }
  return false;
}

// One step forward: every node `nodeId`'s own outgoing flow edges could
// hand off to next, router branches included. The reverse of
// `predecessorsOf`'s single-hop core, factored out because
// `deriveActiveNodeIds` below only ever needs one hop at a time (it starts
// from a whole *set* of completed nodes, not a single id to walk outward
// from).
function nodeSuccessors(nodeId, edges) {
  const targets = new Set();
  (edges || []).filter(isFlowEdge).forEach((edge) => {
    if (!edgeSourceIds(edge).includes(nodeId)) return;
    if (edge.router) {
      routerTargets(edge).forEach((target) => target && targets.add(target));
    } else {
      edgeTargetIds(edge).forEach((target) => target && targets.add(target));
      if (edge.loop_guard?.on_false) targets.add(edge.loop_guard.on_false);
    }
  });
  return targets;
}

// Best-effort "which node(s) are probably executing right now" purely from
// static graph shape plus which node ids already have a run_meta entry --
// see lib/routineRunPolling.js's module doc for why this has to be
// *inferred* client-side rather than read off a real backend signal.
// `completedNodeIds` empty means the run just started: only `entry` can be
// active. A branch/fan-out can legitimately put more than one node id in
// the returned set -- we genuinely don't know which branch fired yet, and
// ticking every candidate is more honest than guessing one. A node already
// in `completedNodeIds` is never re-offered as active even if a loop will
// run it again -- this is a known, documented limitation (see
// AgentGraph.jsx's useRoutineRunPolling), not a soundness claim.
// eslint-disable-next-line react-refresh/only-export-components
export function deriveActiveNodeIds(nodes, edges, entry, completedNodeIds) {
  const nodeIds = new Set((nodes || []).map((node) => node.id));
  if (!entry || !nodeIds.has(entry)) return new Set();
  if (!completedNodeIds || completedNodeIds.size === 0) return new Set([entry]);
  const active = new Set();
  completedNodeIds.forEach((id) => {
    if (!nodeIds.has(id)) return;
    nodeSuccessors(id, edges).forEach((targetId) => {
      if (nodeIds.has(targetId) && !completedNodeIds.has(targetId)) active.add(targetId);
    });
  });
  return active;
}

// The candidate state paths a node at `nodeId` could reference -- Expression
// (eval/condition/switch/loop_guard) resolves these as direct name/attribute
// access; Inputs (agent) still binds them under an author-chosen local alias.
// Either way: the routine's own declared invocation inputs, the two
// always-seeded keys, and every predecessor's output key -- plus, for a
// predecessor carrying a `response_schema` (Select/classify always; prompt/
// agent when authored with one), a `<output_key>.<property>` entry per
// schema field, since both consumers already walk nested dicts
// (`app/progenitor/routines/factory.py`'s `resolve_data_path`/
// `expression.py`'s `ast.Attribute` handling) with no synthetic flattening
// needed. `eval`/`switch`/`condition` predecessors offer only their bare key
// -- their result has no declared shape to drill into.
// eslint-disable-next-line react-refresh/only-export-components
export function buildStatePathSuggestions(nodeId, nodes, edges, inputSchema) {
  const nodesById = new Map((nodes || []).map((node) => [node.id, node]));
  const seen = new Set();
  const suggestions = [];
  const add = (path, entry) => {
    if (seen.has(path)) return;
    seen.add(path);
    suggestions.push({ path, name: path.includes('.') ? path.slice(path.lastIndexOf('.') + 1) : path, ...entry });
  };
  add('request', { source: 'Seeded', hint: 'the routine invocation request' });
  add('context', { source: 'Seeded', hint: 'accumulated context' });
  Object.entries(inputSchema?.properties || {}).forEach(([name, def]) => add(name, { source: 'Routine input', hint: def?.type }));
  predecessorsOf(nodeId, edges).forEach((id) => {
    const node = nodesById.get(id);
    if (!node) return;
    const source = node.label || node.id;
    add(node.id, { source, kind: node.kind });
    const properties = node.config?.response_schema?.properties;
    if (properties && typeof properties === 'object') {
      Object.keys(properties).forEach((prop) => add(`${node.id}.${prop}`, { source, kind: node.kind, hint: properties[prop]?.type }));
    }
  });
  return suggestions;
}

// Drop every trace of `removed`: edges into them go, edges out of them go, and
// a join that loses a source follows the same fan_in bookkeeping as anywhere
// else. A router whose own source is gone disappears with it.
function pruneRemovedNodes(edges, removed) {
  if (!removed.size) return edges;
  return edges.map((edge) => {
    const sources = edgeSourceIds(edge).filter((source) => !removed.has(source));
    if (!sources.length) return null;
    if (edge.router) return edge;
    if (removed.has(edge.to)) return null;
    return withoutJoinSources(edge, removed);
  }).filter(Boolean);
}

// Mirrors deleteNode's fan_in bookkeeping: a join stays a fan_in while two or
// more sources remain, collapses to a plain edge at one, and goes away at none.
function withoutJoinSources(edge, drop) {
  const sources = edgeSourceIds(edge).filter((source) => !drop.has(source));
  if (sources.length === edgeSourceIds(edge).length) return edge;
  if (sources.length >= 2) return { ...edge, from: sources };
  if (sources.length === 1) return { from: sources[0], to: edge.to, level: 'normal' };
  return null;
}

// A new lane joins wherever its siblings already join, so it comes out shaped
// like one that existed when the join was built. Routing it through the
// existing fan_in is also what earns the join `defer=True` at compile time
// (the factory derives that from being a fan_in target), so the join still
// fires on whichever single branch actually ran.
// eslint-disable-next-line react-refresh/only-export-components
export function addForkLane(edges, sourceId, laneId, isDefault = false) {
  const router = edges.find((edge) => edge.from === sourceId && edge.router);
  if (!router) return edges;
  const join = forkJoin(edges, router);
  return edges.map((edge) => {
    if (edge === router) {
      return isDefault
        ? { ...edge, default: laneId }
        : { ...edge, branches: { ...edge.branches, [uniqueBranchValue(edge.branches)]: laneId } };
    }
    if (join && edge === join.edge) return { ...edge, from: [...edgeSourceIds(edge), laneId] };
    return edge;
  });
}

// Dropping a lane takes the whole lane with it: the steps it owned are deleted
// outright rather than left floating in "Unreachable steps", and they come off
// the join's source list on the way out. Ownership is decided by reachability
// -- whatever the graph could reach before the branch was cut and cannot reach
// after -- so a lane holding a nested fork goes as a unit, while a step some
// other lane also points at correctly stays.
function dropForkLane(edges, entry, sourceId, strip) {
  const router = edges.find((edge) => edge.from === sourceId && edge.router);
  if (!router) return { edges, removedNodeIds: [] };
  const cut = edges.map((edge) => (edge === router ? strip(edge) : edge)).filter(Boolean);
  const before = reachableFrom(entry, edges);
  const after = reachableFrom(entry, cut);
  const removed = new Set([...before].filter((id) => !after.has(id)));
  return { edges: pruneRemovedNodes(cut, removed), removedNodeIds: [...removed] };
}

// eslint-disable-next-line react-refresh/only-export-components
export function removeForkBranch(edges, entry, sourceId, value) {
  return dropForkLane(edges, entry, sourceId, (edge) => {
    const branches = { ...edge.branches };
    delete branches[value];
    if (!Object.keys(branches).length && !edge.default) return null;
    if (!Object.keys(branches).length) return { from: edge.from, to: edge.default, level: 'normal' };
    return { ...edge, branches };
  });
}

// eslint-disable-next-line react-refresh/only-export-components
export function removeForkDefault(edges, entry, sourceId) {
  return dropForkLane(edges, entry, sourceId, (edge) => {
    const { default: _drop, ...rest } = edge;
    if (!Object.keys(rest.branches || {}).length) return null;
    return rest;
  });
}

// Builds the whole reachable chain as a plain data tree in one synchronous,
// side-effect-free pass -- BEFORE any JSX is produced. This has to happen up
// front rather than by mutating shared state from inside lazily-invoked
// React children (a fork's branches used to be rendered by calling this
// recursively from within <ForkBlock>): React may invoke a function
// component twice per commit (StrictMode), and a mutable `rendered`/
// `visiting` Set mutated as a side effect of rendering would leak state from
// a discarded first invocation into the real one, corrupting rejoin/cycle
// detection. Building the full tree as pure data first, then rendering that
// already-computed tree, sidesteps the whole class of bug.
function buildChainTree(nodeId, nodesById, edgeBySource, visiting, rendered, stopAt = null) {
  if (!nodeId) return null;
  const node = nodesById.get(nodeId);
  if (!node) return null;
  if (nodeId === stopAt) return { type: 'join', id: nodeId, label: node.label || nodeId };
  if (visiting.has(nodeId)) return { type: 'loop', id: nodeId, label: node.label || nodeId };
  if (rendered.has(nodeId)) return { type: 'rejoin', id: nodeId, label: node.label || nodeId };
  rendered.add(nodeId);
  visiting.add(nodeId);
  const edge = edgeBySource.get(nodeId);
  let next = null;
  let fork = null;
  let loop = null;
  if (edge && edge.router) {
    const targetIds = routerTargets(edge);
    // A nested fork whose lanes converge on an ancestor's join does not own
    // that join -- it belongs to the fork above. Claiming it here would render
    // the shared step tucked inside one lane of the outer fork instead of
    // below the fork it actually closes.
    const commonJoin = findCommonJoin(targetIds, edgeBySource);
    const joinId = commonJoin === stopAt ? null : commonJoin;
    const branchStop = joinId || stopAt;
    // A condition's two arms are fixed, so render them in a stable
    // true-then-false order rather than whatever key order the document
    // happens to carry.
    const fixedBranches = node.kind === 'condition';
    const branches = fixedBranches
      ? CONDITION_BRANCHES.filter((value) => value in (edge.branches || {}))
        .map((value) => [value, edge.branches[value]])
      : Object.entries(edge.branches || {});
    fork = {
      sourceId: nodeId,
      edge,
      editable: true,
      fixedBranches,
      // `split`'s branches don't route on a matched value -- they all fire
      // unconditionally -- so their lane headers have nothing meaningful to
      // label or rename (see ForkBlock/BranchHeader).
      unconditional: node.kind === 'split',
      branches: branches.map(([value, targetId]) => ({
        value,
        tree: buildChainTree(targetId, nodesById, edgeBySource, visiting, rendered, branchStop),
      })),
      defaultTree: edge.default
        ? buildChainTree(edge.default, nodesById, edgeBySource, visiting, rendered, branchStop)
        : null,
      joinTree: joinId
        ? buildChainTree(joinId, nodesById, edgeBySource, visiting, rendered)
        : null,
    };
  } else if (edge && edge.loop_guard) {
    // The reached-while-`visiting` ancestor check above is what actually
    // resolves the cycle -- `edge.to` always leads back to this loop's own
    // start, which is necessarily still on the stack here, so there is
    // nothing to compute for that half. What IS new: the guard's own
    // on_false, a second, structural way out of this exact position -- the
    // loop's ordinary forward continuation -- computed the same way any
    // other "next" is.
    loop = {
      sourceId: nodeId,
      bodyStartId: edge.to,
      edge,
      exitTree: edge.loop_guard.on_false
        ? buildChainTree(edge.loop_guard.on_false, nodesById, edgeBySource, visiting, rendered, stopAt)
        : null,
    };
  } else {
    next = buildChainTree(edgeTargetIds(edge)[0], nodesById, edgeBySource, visiting, rendered, stopAt);
  }
  visiting.delete(nodeId);
  return { type: 'node', id: nodeId, node, next, fork, loop };
}

// Where a branch's chain ends once followed all the way down: a genuine graph
// END ('end'), a loop back ('loop'), a rejoin onto already-rendered content
// ('rejoin'), or an existing join ('join'). Used to decide which lanes a
// rendered join draws its fan-in rail from.
//
// A lane that itself branches ends wherever its own branches end, so descend:
// past the nested fork's join if it has one, otherwise into its lanes, which
// agree on an answer or leave the outer lane genuinely forked.
function chainTerminus(tree) {
  let current = tree;
  while (current && current.type === 'node') {
    if (current.fork) {
      if (current.fork.joinTree) return chainTerminus(current.fork.joinTree);
      const inner = [
        ...current.fork.branches.map((branch) => branch.tree),
        ...(current.fork.defaultTree ? [current.fork.defaultTree] : []),
      ].map(chainTerminus);
      return inner.length && inner.every((terminus) => terminus === inner[0]) ? inner[0] : 'fork';
    }
    current = current.next;
  }
  return current === null ? 'end' : current.type;
}


// Renders an already-computed chain-tree run: a connector before each step,
// the step itself, and either the next connector, a fork, or a terminus.
// Pure -- safe to invoke from a React child any number of times.
function splitForeachBody(tree) {
  if (!tree || tree.type !== 'node') return [tree, null];
  if (tree.node.kind === 'noop') return [null, tree];
  const [bodyNext, after] = splitForeachBody(tree.next);
  return [{ ...tree, next: bodyNext }, after];
}

function renderRun(tree, initialSlot, suppressJoinChip = false) {
  const out = [];
  // nodeId -> index into `out` right before that node's own content (its
  // StepCard/ForeachBlock) was pushed, i.e. after its incoming connector.
  // A cycle back to an ancestor in *this same* linear pass can then splice
  // out its whole body and box it, mirroring how a foreach gets a scope --
  // a cycle reached through a fork branch or foreach body (a separate
  // renderRun call, separate `positions` map) just falls back to the plain
  // "loops back to" chip below.
  const positions = new Map();
  let current = tree;
  let slot = initialSlot;
  let guard = 0;
  while (guard++ < 2000) {
    out.push(<ConnectorInsert key={`c-${slot.sourceId ?? 'root'}-${slot.kind}-${slot.value ?? ''}`} slot={slot} />);
    if (current === null) {
      out.push(<ChainCap key="end" kind="end" />);
      break;
    }
    if (current.type === 'loop') {
      const bodyStart = positions.get(current.id);
      if (bodyStart !== undefined) {
        const body = out.splice(bodyStart);
        // The connector just pushed above (top of this same iteration) is
        // body's last element -- it's the one whose edge is the back-edge
        // itself. Render it after the box, not swept inside: same "+ after
        // the block" affordance every other construct (fork, foreach)
        // offers, and functionally unchanged -- inserting there still
        // splices a new step onto the tail before it loops back, which the
        // next render sweeps into a now-larger box.
        const trailing = body.pop();
        out.push(<LoopBlock key={`loop-${current.id}`} targetLabel={current.label} body={body} />);
        if (trailing) out.push(trailing);
      } else {
        out.push(<ChainChip key={`loop-${current.id}`} kind="loop" label={`↺ loops back to “${current.label}”`} />);
      }
      break;
    }
    if (current.type === 'rejoin') {
      out.push(<ChainChip key={`rejoin-${current.id}`} kind="rejoin" label={`↳ continues at “${current.label}”`} />);
      break;
    }
    if (current.type === 'join') {
      if (!suppressJoinChip) {
        out.push(<ChainChip key={`join-${current.id}`} kind="rejoin" label={`↳ joins at “${current.label}”`} />);
      }
      break;
    }
    positions.set(current.id, out.length);
    if (current.node.kind === 'foreach' && current.node.config?.mode === 'send') {
      const [bodyTree, afterTree] = splitForeachBody(current.next);
      out.push(
        <ForeachBlock
          key={`foreach-${current.id}`}
          node={current.node}
          body={renderRun(bodyTree, { sourceId: current.id, kind: 'foreach' })}
        />,
      );
      slot = { sourceId: current.id, kind: 'plain' };
      current = afterTree;
      continue;
    }

    if (current.fork) {
      out.push(<ForkBlock key={`fork-${current.id}`} fork={current.fork} node={current.node} />);
      break;
    }
    out.push(<StepCard key={current.id} node={current.node} />);
    if (current.loop) {
      // Reaching the tail: the body is everything pushed since the loop's
      // own start's card and connector -- the start itself stays outside
      // the box, rendered as a plain step above it (matching ForkBlock's
      // own router card, e.g. "Classify Item"), and is where its
      // properties are actually edited (NodeInspector, via `guardEdge` --
      // see there for why). `positions.get(bodyStartId) + 1` skips past
      // the start's own card, keeping only the connector onward. `exitTree`
      // continues the OUTER chain past the box -- the guard's own
      // on_false -- exactly like a foreach's stop marker hands off to
      // whatever comes after the fan-out.
      const bodyStart = positions.get(current.loop.bodyStartId);
      const body = bodyStart !== undefined ? out.splice(bodyStart + 1) : [];
      out.push(<LoopBlock key={`loop-${current.id}`} edge={current.loop.edge} body={body} />);
      slot = { sourceId: current.id, kind: 'loop-exit' };
      current = current.loop.exitTree;
      continue;
    }
    slot = { sourceId: current.id, kind: 'plain' };
    current = current.next;
  }
  return out;
}

// Retry loop: boxes the cycle's body -- everything after the node it loops
// back to, through the back-edge -- like ForeachBlock does for a fan-out.
// One box, not a box nested inside a box, and the loop-start's own card
// (e.g. "Loop") renders OUTSIDE/above it, exactly like a router's own card
// sits above ForkBlock's lanes -- this section is purely the body. `edge`
// is the guarded case (a real Loop construct's tail, `loop_guard` present);
// its properties are edited by clicking the loop-start card itself
// (NodeInspector), not here. `targetLabel` alone (no `edge`) is the
// fallback for a hand-authored, unguarded cycle the JSON editor can still
// produce. Either way there's no separate "back to X" line -- the box's
// own closing edge already says that.
function LoopBlock({ edge, targetLabel, body }) {
  const { settings, runMeta, loopIterations, setLoopIteration } = useContext(GraphEditContext);
  const recursionLimit = settings?.recursion_limit;
  const maxRetries = settings?.retry?.max_retries;
  const guard = edge?.loop_guard;
  // One entry per completed pass through the guard -- the count this
  // loop's own prev/next nav pages across (see pickEntryForIteration,
  // which buckets every other body node's entries against these same
  // steps).
  const guardEntries = guard ? (runMeta?.[edge.from] || []) : [];
  const iterationCount = guardEntries.length;
  const selectedIteration = loopIterations?.[edge?.from] ?? iterationCount;
  return (
    <div className="routine-loop-frame">
      {(recursionLimit || maxRetries || iterationCount > 0) ? (
        <div className="routine-loop__toolbar">
          {recursionLimit ? <span className="routine-loop__badge">Recursion limit {recursionLimit}</span> : null}
          {maxRetries ? <span className="routine-loop__badge">retries {maxRetries}x per step</span> : null}
          {iterationCount > 0 ? (
            <span className="routine-loop__iteration-nav">
              <button
                type="button"
                className="routine-loop__iteration-btn"
                aria-label="Previous iteration"
                title="Previous iteration"
                disabled={selectedIteration <= 1}
                onClick={() => setLoopIteration(edge.from, Math.max(1, selectedIteration - 1))}
              >
                <ChevronLeftSmall />
              </button>
              <span className="routine-loop__iteration-label">{selectedIteration} / {iterationCount}</span>
              <button
                type="button"
                className="routine-loop__iteration-btn"
                aria-label="Next iteration"
                title="Next iteration"
                disabled={selectedIteration >= iterationCount}
                onClick={() => setLoopIteration(edge.from, Math.min(iterationCount, selectedIteration + 1))}
              >
                <ChevronRightPointSmall />
              </button>
            </span>
          ) : null}
        </div>
      ) : null}
      <section className="routine-loop" aria-label={guard ? 'Loop' : `Retry loop back to ${targetLabel}`}>
        <div className="routine-loop__rail" title={guard?.expression || undefined}>
          <span className={`routine-loop__rail-text${guard ? ' routine-loop__rail-text--guarded' : ''}${guard && !guard.expression ? ' routine-loop__rail-text--unset' : ''}`}>
            {guard ? (guard.expression ? `↺ Loop while ${guard.expression}` : '↺ Loop · set condition') : '↺ Retry loop'}
          </span>
        </div>
        <div className="routine-loop__main">
          <div className="routine-loop__body">{body}</div>
        </div>
      </section>
    </div>
  );
}

function ForeachBlock({ node, body }) {
  const itemKey = node.config?.item_key || 'item';
  return (
    <section className="routine-foreach" aria-label={`For each ${itemKey}`}>
      <div className="routine-foreach__head">
        <StepCard node={node} />
      </div>
      <div className="routine-foreach__body">
        {body}
      </div>
    </section>
  );
}

// Which lane owns the left/right rail terminator (`--fanout-first` /
// `--fanout-last`, chrome-frame.css) that clips the horizontal connector
// stub to the lane's own centre instead of running off the fork's edge.
// The "+ Add branch" slot is appended after every real lane whenever the
// fork can grow one (see the block below the lanes.map call), so it -- not
// the last real lane -- owns the right terminator in that case; otherwise
// the last real lane is genuinely the rightmost rendered lane.
// Exported for unit coverage: a fork with N branches plus an "+ Add branch"
// slot must clip the rail at the slot, not one lane early.
// eslint-disable-next-line react-refresh/only-export-components
export function forkLaneEdgePosition(index, lanesLength, hasAddSlot) {
  return {
    isFirst: index === 0,
    isLast: index === lanesLength - 1 && !hasAddSlot,
  };
}

// A router node and its branch lanes form one CSS-only switch box. Connector
// rails are pseudo-elements, never DOM measurements: centring the box makes
// the router stem meet the lane rail even when lanes have different widths.
function ForkBlock({ fork, node }) {
  const {
    onAddBranch,
    onAddDefault,
    onRemoveBranch,
    onRemoveDefault,
    onRenameBranch,
    edges,
    readOnly,
  } = useContext(GraphEditContext);
  const [collapsed, setCollapsed] = useState(() => new Set());
  const { sourceId, edge, branches, defaultTree, joinTree, editable = true, fixedBranches = false, unconditional = false } = fork;
  // A fixed fork's arms are dictated by the node kind, so it grows no lanes
  // and its headers are labels rather than editable chips. It can still be
  // joined -- that's about what happens *after* the fork, not its shape.
  const canGrowLanes = editable && !fixedBranches && !readOnly;
  const lanes = [
    ...branches.map(({ value, tree }) => ({
      value,
      tree,
      isDefault: false,
      slot: { sourceId, kind: 'branch', value },
    })),
    ...(edge.default ? [{
      value: 'default',
      tree: defaultTree,
      isDefault: true,
      slot: { sourceId, kind: 'default' },
    }] : []),
  ];
  const joining = joinTree ? lanes.filter((lane) => chainTerminus(lane.tree) === 'join') : [];
  const joiningValues = new Set(joining.map(({ value }) => value));
  // Asks the wiring itself whether a join is possible rather than reading it
  // off the rendered tree, so the `+` appears exactly when the action behind
  // it would do something -- including on a fork whose lane branches again.
  const canOfferJoin = useMemo(() => joinTerminals(edges, sourceId).length > 0, [edges, sourceId]);
  const toggleCollapse = useCallback((value) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }, []);

  return (
    <section className={`routine-fork ${joining.length ? 'routine-fork--joined' : ''}`}>
      <div className="routine-fork__head">
        <StepCard node={node} />
      </div>
      <div className="routine-fork__branches">
        <div className="routine-fork__lanes">
          {lanes.map((lane, index) => {
            const isCollapsed = collapsed.has(lane.value);
            const { isFirst, isLast } = forkLaneEdgePosition(index, lanes.length, canGrowLanes);
            const classes = [
              'routine-fork__lane',
              isCollapsed && 'routine-fork__lane--collapsed',
              lane.isDefault && 'routine-fork__lane--default',
              isFirst && 'routine-fork__lane--fanout-first',
              isLast && 'routine-fork__lane--fanout-last',
              joiningValues.has(lane.value) && 'routine-fork__lane--fanin',
              joiningValues.has(lane.value) && lane.value === joining[0]?.value && 'routine-fork__lane--fanin-first',
              joiningValues.has(lane.value) && lane.value === joining.at(-1)?.value && 'routine-fork__lane--fanin-last',
            ].filter(Boolean).join(' ');
            return (
              <div key={lane.value} className={classes}>
                <span className="routine-fork__wire" aria-hidden="true" />
                <BranchHeader
                  value={lane.value}
                  isDefault={lane.isDefault}
                  onRename={(next) => onRenameBranch(sourceId, lane.value, next)}
                  onRemove={() => (lane.isDefault ? onRemoveDefault(sourceId) : onRemoveBranch(sourceId, lane.value))}
                  collapsed={isCollapsed}
                  readOnly={readOnly || !editable || fixedBranches}
                  hideLabel={unconditional}
                  onToggleCollapse={() => toggleCollapse(lane.value)}
                />
                {isCollapsed ? null : renderRun(lane.tree, lane.slot, Boolean(joinTree))}
              </div>
            );
          })}
          {canGrowLanes && !edge.default ? (
            <div className="routine-fork__lane routine-fork__lane--add routine-fork__lane--collapsed">
              <span className="routine-fork__wire" aria-hidden="true" />
              <div className="routine-fork__add-slot">
                <button type="button" className="routine-fork__add-slot-action" onClick={() => onAddDefault(sourceId)}>
                  + Add default
                </button>
              </div>
            </div>
          ) : null}
          {canGrowLanes ? (
            <div className="routine-fork__lane routine-fork__lane--add routine-fork__lane--collapsed routine-fork__lane--fanout-last">
              <span className="routine-fork__wire" aria-hidden="true" />
              <div className="routine-fork__add-slot">
                <button type="button" className="routine-fork__add-slot-action" onClick={() => onAddBranch(sourceId)}>
                  + Add branch
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>
      {joinTree ? (
        <div className="routine-fork__join">
          {renderRun(joinTree, { sourceId, kind: 'join' })}
        </div>
      ) : null}
      <div className="routine-fork__actions">
        {editable && !joinTree && canOfferJoin ? (
          <ConnectorInsert slot={{ sourceId, kind: 'join' }} />
        ) : null}
      </div>
    </section>
  );
}

// Nodes not reachable from `entry` (e.g. left behind after deleting whatever
// pointed at them) never silently vanish -- they surface here so the author
// can rewire or delete them explicitly instead of losing data invisibly.
function UnreachableTray({ nodes }) {
  const { deleteNode, readOnly } = useContext(GraphEditContext);
  if (!nodes.length) return null;
  return (
    <div className="routine-unreachable">
      <div className="routine-unreachable__head">Unreachable steps ({nodes.length})</div>
      <div className="routine-unreachable__list">
        {nodes.map((node) => (
          <div key={node.id} className="routine-unreachable__item">
            <span className={`canvas-dock-icon ${node.kind}`}><KindIcon kind={node.kind} /></span>
            <span>{node.label || node.id}</span>
            {readOnly ? null : (
              <button type="button" onClick={() => deleteNode(node.id)} title="Delete" aria-label="Delete">×</button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function RoutineChain({ nodes, edges, entry }) {
  const nodesById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const edgeBySource = useMemo(() => edgeBySourceMap(edges), [edges]);
  // Build the whole reachable tree (forks included) fully, up front, in one
  // pure pass -- see buildChainTree's comment for why this can't be deferred
  // into lazily-invoked React children. Memoized: without this, every
  // re-render of the page (e.g. an unrelated realtime/SSE update bubbling
  // through a parent selector) walked and re-built the whole tree from
  // scratch, even though nodes/edges/entry hadn't changed.
  const { tree, unreachable } = useMemo(() => {
    const rendered = new Set();
    const builtTree = buildChainTree(entry, nodesById, edgeBySource, new Set(), rendered);
    return { tree: builtTree, unreachable: nodes.filter((node) => !rendered.has(node.id)) };
  }, [entry, nodesById, edgeBySource, nodes]);
  const body = useMemo(() => renderRun(tree, { sourceId: null, kind: 'entry' }), [tree]);
  return (
    <div className="routine-chain">
      <ChainCap kind="start" />
      {body}
      <UnreachableTray nodes={unreachable} />
    </div>
  );
}
// Exported for unit coverage: regression guard for the fan_in-edge bug
// (edge.from is an array for fan_in, but this validator used to compare/look
// it up as a plain string, so every graph with a router-fork "Join branches"
// join permanently failed validation and could never enable Run).
// eslint-disable-next-line react-refresh/only-export-components
export function validateWorkingGraph(nodes, edges, entry) {
  const errors = [];
  const ids = new Set(nodes.map((node) => node.id).filter(Boolean));
  if (!entry || !ids.has(entry)) errors.push('Entry must reference an existing node.');
  nodes.forEach((node) => {
    if (!node.id) errors.push('Every node needs an id.');
    if (node.id === '__seed__') errors.push('__seed__ is reserved.');
  });
  edges.forEach((edge) => {
    const targets = edge.router ? routerTargets(edge) : [edge.to];
    edgeSourceIds(edge).forEach((source) => { if (!ids.has(source)) errors.push(`Edge source "${source}" is missing.`); });
    targets.forEach((target) => {
      if (!ids.has(target)) errors.push(`Edge target "${target}" is missing.`);
    });
  });
  // Mirrors the backend's one-outgoing-edge-declaration rule. A step that
  // feeds a join is named as a source *by* the join, so anything that also
  // hands it a plain edge (splicing a step in after it, say) leaves it with
  // two ways out. Checking it here surfaces the break in the editor instead
  // of only as a rejected save. Error edges do NOT count -- a node may have
  // both one flow edge and one error edge.
  const outgoingCount = new Map();
  edges.filter(isFlowEdge).forEach((edge) => edgeSourceIds(edge).forEach((source) => {
    outgoingCount.set(source, (outgoingCount.get(source) || 0) + 1);
  }));
  outgoingCount.forEach((count, source) => {
    if (count > 1) errors.push(`"${source}" has more than one outgoing edge.`);
  });
  // Error edge rules: at most one per node, no self-loops, target must exist.
  const errorEdgesBySource = new Map();
  edges.forEach((edge) => {
    if (edge.level === 'error') {
      const source = edge.from;
      if (errorEdgesBySource.has(source)) {
        errors.push(`"${source}" has more than one error edge.`);
      } else {
        errorEdgesBySource.set(source, edge);
      }
      if (edge.to === source) {
        errors.push(`"${source}" cannot route its own errors to itself.`);
      }
    }
  });
  const reachable = reachableFrom(entry, edges);
  nodes.filter((node) => !reachable.has(node.id)).forEach((node) => errors.push(`"${node.id}" is unreachable.`));
  return errors;
}


// Serialize the working nodes/edges/meta into the backend graph document
// shape. `output_key` always mirrors `id` -- it isn't independently editable.
function serializeGraph(nodes, edges, meta) {
  return {
    status: meta.status || 'Draft',
    version: meta.version || '2.0.0',
    summary: meta.summary || '',
    entry: meta.entry || nodes[0]?.id || null,
    nodes: nodes.map((node) => ({
      id: node.id,
      kind: node.kind,
      label: node.label,
      description: node.description || '',
      output_key: node.id,
      config: node.config || {},
    })),
    edges,
    settings: meta.settings || {},
    input_schema: meta.input_schema || null,
    output_schema: meta.output_schema || null,
    $defs: meta.$defs || {},
  };
}

// Neither a rejected save nor a withheld one advances `lastSavedRef`, so
// undoing the edit behind either -- fixing the change the server refused, or
// deleting the incomplete step that was never sent -- puts the canvas back in
// step with the server. No save is queued from there to clear the chip, so
// both states have to collapse here, or the warning outlives the change that
// caused it and accuses a graph that is fine.
const UNSETTLED_SAVE_STATES = new Set(['error', 'incomplete']);
export function saveStatusWhenInSync(current) {
  return UNSETTLED_SAVE_STATES.has(current) ? 'idle' : current;
}

const EMPTY_RUN_STATE = { run_meta: {}, data: {}, found: false };

// Drives the poll loop behind the routine canvas's live run feedback --
// see lib/routineRunPolling.js's module doc for the two-phase discovery
// design and why this has to poll rather than react to a push event.
// `agentId` scopes everything: switching routines drops any in-flight poll
// for the previous one immediately, so a stale sub-header never keeps
// ticking for a routine the author has navigated away from.
function useRoutineRunPolling(agentId, parameters) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const agentsUrl = getApiUrl('agentProfiles');
  const [sessionId, setSessionId] = useState(null);
  const [threadId, setThreadId] = useState(null);
  // idle -> discovering (found the run's thread_id yet?) -> running (live
  // run_meta polling) -> settled (stopped polling, last state stays shown).
  const [status, setStatus] = useState('idle');
  const startedAtMsRef = useRef(null);
  // Mirrors `startedAtMsRef` for render -- refs can't be read during render
  // (React rule), so the value the hook actually returns to callers comes
  // from this state instead; the ref stays for the effects' own timeout
  // math above, where reading a ref is fine.
  const [runStartedAtMs, setRunStartedAtMs] = useState(null);
  // Set the instant discovery resolves (the thread_id turns up in the
  // checkpoints table) -- a real signal the graph is now actually live,
  // not just a click-time guess. Anchors the entry node's own ticking
  // timer once warm-up (see ChainCap's pulse, gated on 'discovering'
  // alone) ends, well before that node's own completion exists.
  const [threadFoundAtMs, setThreadFoundAtMs] = useState(null);
  const runs = useSelector((state) => selectAgentRoutineRuns(state, agentId));
  const runState = useSelector((state) => (threadId ? selectAgentRunState(state, threadId) : undefined)) || EMPTY_RUN_STATE;

  const interactiveSessionIdRef = useRef(null);
  // Mirrors `interactiveSessionIdRef` for render -- whether a "Reset run"
  // affordance has anything to reset. Set true the first time startRun()
  // mints/reuses an interactive session (see below); cleared by an
  // explicit reset or by switching routines.
  const [hasInteractiveSession, setHasInteractiveSession] = useState(false);
  useEffect(() => {
    setSessionId(null);
    setThreadId(null);
    setStatus('idle');
    setThreadFoundAtMs(null);
    interactiveSessionIdRef.current = null;
    setHasInteractiveSession(false);
  }, [agentId]);

  // Phase 1: poll /runs, watch for the thread_id this session's Run click
  // minted -- see findRunBySessionId.
  useEffect(() => {
    if (status !== 'discovering' || !sessionId) return undefined;
    const found = findRunBySessionId(runs, sessionId);
    if (found) {
      setThreadId(found.thread_id);
      setThreadFoundAtMs(Date.now());
      setStatus('running');
      return undefined;
    }
    if (Date.now() - startedAtMsRef.current > DISCOVERY_TIMEOUT_MS) {
      setStatus('settled');
      return undefined;
    }
    const timer = setTimeout(() => dispatch(fetchRoutineRuns({ agentsUrl, agentId })), DISCOVERY_POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
    // Re-runs on every `runs` change too, so a fetch already in flight from
    // the previous tick's dispatch gets checked the moment it lands instead
    // of waiting for the next timer tick.
  }, [status, sessionId, runs, agentsUrl, agentId, dispatch]);

  // Phase 2: poll /run-state on the now-known thread_id; settle once the
  // total executed-node count stops growing for SETTLE_STABLE_POLLS polls
  // in a row (the only "is it done" signal available -- see module doc).
  const stablePollsRef = useRef(0);
  const lastTotalRef = useRef(-1);
  useEffect(() => {
    if (status !== 'running' || !threadId) return undefined;
    const total = totalRunMetaEntries(runState.run_meta);
    if (total === lastTotalRef.current) {
      stablePollsRef.current += 1;
    } else {
      stablePollsRef.current = 0;
      lastTotalRef.current = total;
    }
    if (stablePollsRef.current >= SETTLE_STABLE_POLLS && total > 0) {
      setStatus('settled');
      return undefined;
    }
    if (Date.now() - startedAtMsRef.current > STATE_POLL_TIMEOUT_MS) {
      setStatus('settled');
      return undefined;
    }
    const timer = setTimeout(() => dispatch(fetchRunState({ agentsUrl, agentId, threadId })), STATE_POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [status, threadId, runState.run_meta, agentsUrl, agentId, dispatch]);

  // `targetNode` set means "run through this node (inclusive)", reusing a
  // STABLE session_id across repeated clicks within this editor session --
  // the backend's `_interactive_session_id` resume mechanism (see
  // subagent.py's `_run`) needs the same session_id back on every call to
  // continue the SAME checkpointed thread rather than restarting. Omitted
  // (the top-level Run button) always mints a fresh session -- a full run
  // is a distinct, complete execution every time, never a resume.
  const startRun = useCallback((targetNode) => {
    const newSessionId = targetNode
      ? (interactiveSessionIdRef.current ??= randomSessionId())
      : randomSessionId();
    if (targetNode) setHasInteractiveSession(true);
    stablePollsRef.current = 0;
    lastTotalRef.current = -1;
    startedAtMsRef.current = Date.now();
    setRunStartedAtMs(startedAtMsRef.current);
    setThreadFoundAtMs(null);
    setThreadId(null);
    setSessionId(newSessionId);
    setStatus('discovering');
    fetch(`${agentsUrl}/${encodeURIComponent(agentId)}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: newSessionId,
        ...(targetNode ? { target_node: targetNode } : {}),
        ...(parameters && Object.keys(parameters).length ? { parameters } : {}),
      }),
    }).catch(() => {});
    dispatch(fetchRoutineRuns({ agentsUrl, agentId }));
    return newSessionId;
  }, [agentsUrl, agentId, dispatch, parameters]);

  // Abandons the current interactive ("Run to here") session -- the next
  // "Run to here" click mints a brand new session_id/thread_id instead of
  // resuming the abandoned one. Does not touch the top-level Run button's
  // own (always-fresh) sessions, and is not itself a side effect -- it
  // only forgets a client-side pointer, the old thread's checkpoints stay
  // exactly where they are (still visible in the Runs panel).
  const resetInteractiveSession = useCallback(() => {
    interactiveSessionIdRef.current = null;
    setHasInteractiveSession(false);
  }, []);

  // The revision this run's cached data was executed against -- null for a
  // run against the live/unpinned document, or before the thread_id is
  // even known yet. Used by GraphCanvas to fetch that revision's content
  // and diff it against the current draft graph (see diffRoutineGraphs),
  // so an edit made after this run doesn't leave its stale timing/tokens/
  // output looking current.
  const runRevisionId = useMemo(() => parseRunThreadId(threadId)?.revisionId ?? null, [threadId]);

  return {
    status,
    threadId,
    runMeta: runState.run_meta,
    runData: runState.data,
    // Once nothing has completed yet, the best estimate for "when did the
    // active node start" is the moment the thread was actually found
    // (real signal the graph is live), or the run's own click time before
    // even that -- after the first completion, the latest known finish
    // across every node (see latestCompletionMs) is exact, not estimated.
    estimatedActiveStartMs: latestCompletionMs(runState.run_meta) ?? threadFoundAtMs ?? runStartedAtMs,
    runRevisionId,
    startRun,
    hasInteractiveSession,
    resetInteractiveSession,
  };
}

// Drives the canvas's top-right run-history pill: back/forward paging
// through every past execution of this routine (see agent_store's
// `list_routine_runs`, newest-first). Position 0 ("the front") always
// tracks whatever is most current -- the live in-session poll straight
// from useRoutineRunPolling while one is in flight or has ever run this
// session (ticking updates, no extra fetch), falling back to the newest
// list entry on a fresh mount that has not run anything yet. Any other
// position is purely historical: fetched once (never polled -- it already
// finished) into the same runStateByThreadId cache the live poll itself
// uses, so re-visiting a position already fetched this session is free.
function useRunHistoryNav(agentId, live) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const agentsUrl = getApiUrl('agentProfiles');
  const runsList = useSelector((state) => selectAgentRoutineRuns(state, agentId)) || [];

  useEffect(() => {
    dispatch(fetchRoutineRuns({ agentsUrl, agentId }));
    // Runs once per agent so back/forward works even if the Runs side
    // panel is never opened.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  const [viewedIndex, setViewedIndex] = useState(0);
  useEffect(() => setViewedIndex(0), [agentId]);

  const isLiveActive = live.status === 'discovering' || live.status === 'running';
  const atFront = viewedIndex <= 0;
  const usesLive = atFront && (isLiveActive || Boolean(live.threadId));

  const historicalThreadId = usesLive ? null : (runsList[viewedIndex]?.thread_id ?? null);
  const historicalState = useSelector((state) => (
    historicalThreadId ? selectAgentRunState(state, historicalThreadId) : undefined
  ));
  useEffect(() => {
    if (!historicalThreadId || historicalState) return;
    dispatch(fetchRunState({ agentsUrl, agentId, threadId: historicalThreadId }));
  }, [historicalThreadId, historicalState, agentsUrl, agentId, dispatch]);

  const displayEntry = usesLive
    ? (live.threadId ? runsList.find((run) => run.thread_id === live.threadId) : runsList[0])
    : runsList[viewedIndex];
  const isBrowsingHistory = !atFront;

  // Every run is assumed pinned to a revision (see agent_store's
  // list_routine_runs, which bakes revision_id into the thread_id) -- an
  // 'unpinned'/missing one is legacy data from before runs were tagged and
  // is simply treated as having nothing to show, not specially handled.
  // Fetched into the same revisionContentByKey cache the JSON/restore flow
  // already shares, so re-visiting an already-fetched run is free.
  const browsedRevisionId = isBrowsingHistory ? (displayEntry?.revision_id || null) : null;
  const browsedGraph = useSelector((state) => (
    browsedRevisionId ? selectAgentRoutineRevisionContent(state, agentId, browsedRevisionId) : undefined
  ));
  useEffect(() => {
    if (!browsedRevisionId || browsedGraph) return;
    dispatch(fetchRoutineRevision({ agentsUrl, agentId, revisionId: browsedRevisionId }));
  }, [browsedRevisionId, browsedGraph, agentsUrl, agentId, dispatch]);

  const goBack = useCallback(() => {
    setViewedIndex((current) => Math.min(current + 1, Math.max(runsList.length - 1, 0)));
  }, [runsList.length]);
  const goForward = useCallback(() => setViewedIndex((current) => Math.max(current - 1, 0)), []);
  const resetToLatest = useCallback(() => setViewedIndex(0), []);
  // Lets the Runs panel jump straight to any row -- clamped the same way
  // goBack/goForward are, so an out-of-range index (a stale click after the
  // list shrank) can't leave viewedIndex pointing past the end.
  const jumpTo = useCallback((index) => {
    setViewedIndex(Math.max(0, Math.min(index, Math.max(runsList.length - 1, 0))));
  }, [runsList.length]);

  return {
    runsList,
    viewedIndex,
    runMeta: usesLive ? live.runMeta : (historicalState?.run_meta || {}),
    runData: usesLive ? live.runData : (historicalState?.data || {}),
    runRevisionId: usesLive ? live.runRevisionId : (displayEntry?.revision_id || null),
    hasRuns: runsList.length > 0 || isLiveActive,
    canGoBack: runsList.length > 0 && viewedIndex < runsList.length - 1,
    canGoForward: viewedIndex > 0,
    label: displayEntry ? (formatAge(displayEntry.last_checkpoint_at) || 'now') : (isLiveActive ? 'Running…' : null),
    title: displayEntry?.last_checkpoint_at || '',
    // Everything about a browsed run is locked (see the toolbar's Restore
    // button): the graph shown is that run's own pinned snapshot, not the
    // editable draft, so there is nothing sound to edit until either
    // stepping back to the live front position or explicitly Restoring it.
    isBrowsingHistory,
    browsedGraph: isBrowsingHistory ? browsedGraph : null,
    canRestore: isBrowsingHistory && Boolean(browsedGraph),
    goBack,
    goForward,
    jumpTo,
    resetToLatest,
  };
}

// Which node ids' cached run details (timing/tokens/output) are stale
// relative to the CURRENT draft graph -- the run executed against
// `runRevisionId`'s pinned snapshot, and the draft may have been edited
// since (see lib/routineGraphDiff.js). `runRevisionId` is null for a run
// against the live/unpinned document (no revision reader configured) --
// there is no earlier snapshot to diff against in that case, so nothing
// is ever flagged stale for it; a documented limitation, not a soundness
// claim. Only meaningful at the run-nav's front (live draft) position --
// GraphCanvas discards this result while browsing history, since the
// canvas there already shows that run's own exact snapshot (see
// useRunHistoryNav's browsedGraph), which can never diverge from itself.
function useRunStaleness(agentId, runRevisionId, currentGraph) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const agentsUrl = getApiUrl('agentProfiles');
  const baselineGraph = useSelector((state) => (
    runRevisionId ? selectAgentRoutineRevisionContent(state, agentId, runRevisionId) : undefined
  ));
  useEffect(() => {
    if (!runRevisionId || baselineGraph) return;
    dispatch(fetchRoutineRevision({ agentsUrl, agentId, revisionId: runRevisionId }));
  }, [runRevisionId, baselineGraph, agentsUrl, agentId, dispatch]);
  return useMemo(
    () => (runRevisionId && baselineGraph ? diffRoutineGraphs(baselineGraph, currentGraph) : new Set()),
    [runRevisionId, baselineGraph, currentGraph],
  );
}

function GraphCanvas({ agent, graph, confirmedGraph, onPersist, onShowProfile, onDelete, jsonOpen, onOpenJson, onCloseJson, runsOpen, onOpenRuns, onCloseRuns, parametersOpen, onOpenParameters, onCloseParameters }) {
  const persistRef = useRef(onPersist);
  const lastSavedRef = useRef(null);
  // Values the user fills in for the routine's declared input_schema.properties
  // (see RoutineParametersPanel) -- session-local only, never persisted to the
  // graph document; startRun sends the current snapshot with every POST.
  const [parameterValues, setParameterValues] = useState({});
  const {
    status: runStatus,
    threadId: liveThreadId,
    runMeta: liveRunMeta,
    runData: liveRunData,
    estimatedActiveStartMs,
    runRevisionId: liveRunRevisionId,
    startRun,
    hasInteractiveSession,
    resetInteractiveSession,
  } = useRoutineRunPolling(agent.id, parameterValues);
  const runNav = useRunHistoryNav(agent.id, {
    status: runStatus,
    threadId: liveThreadId,
    runMeta: liveRunMeta,
    runData: liveRunData,
    runRevisionId: liveRunRevisionId,
  });
  const runMeta = runNav.runMeta;
  const runData = runNav.runData;
  const runRevisionId = runNav.runRevisionId;
  const { resetToLatest: resetRunNavToLatest } = runNav;
  const startRunAndTrackLatest = useCallback((targetNode) => {
    resetRunNavToLatest();
    return startRun(targetNode);
  }, [resetRunNavToLatest, startRun]);
  const { isBrowsingHistory } = runNav;
  useEffect(() => {
    if (!isBrowsingHistory) return;
    onCloseJson();
    onCloseParameters();
    // Only reacts to entering browsing itself -- onCloseJson/onCloseParameters
    // are fresh closures every render (AgentsPage inlines them), and neither
    // needs to re-fire this on its own identity change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isBrowsingHistory]);
  // Which iteration a loop's own prev/next nav has explicitly selected,
  // keyed by the guard node's id (see LoopBlock/pickEntryForIteration) --
  // absent means "latest" (today's default). Reset the instant a fresh
  // live run starts so a past browse doesn't strand the canvas on a stale
  // iteration from before this run existed.
  const [loopIterations, setLoopIterations] = useState({});
  const setLoopIteration = useCallback((guardId, iteration) => {
    setLoopIterations((current) => ({ ...current, [guardId]: iteration }));
  }, []);
  useEffect(() => {
    setLoopIterations({});
  }, [liveThreadId]);
  // Plain CSS `zoom` on the scrolling chain content only -- not a canvas-
  // style pan/zoom viewport (this isn't React Flow, see the module doc).
  // Deliberately NOT wired into positionInspector's math: CSS `zoom`
  // reflows layout, so getBoundingClientRect() on a zoomed node already
  // reports its real post-zoom screen position, and that function reads
  // real rects off the DOM rather than any canvas-model coordinates --
  // it keeps working unmodified at any zoom level.
  const [zoom, setZoom] = useState(1);
  useEffect(() => setZoom(1), [agent.id]);
  const zoomOut = useCallback(() => setZoom((z) => Math.max(ZOOM_MIN, Math.round((z - ZOOM_STEP) * 100) / 100)), []);
  const zoomIn = useCallback(() => setZoom((z) => Math.min(ZOOM_MAX, Math.round((z + ZOOM_STEP) * 100) / 100)), []);
  const resetZoom = useCallback(() => setZoom(1), []);
  const [nodes, setNodes] = useState(() => (graph.nodes || []).map((node) => ({ ...node })));
  const [edges, setEdges] = useState(() => (graph.edges || []).map((edge) => ({ ...edge })));
  const [meta, setMeta] = useState({
    status: graph.status || 'Draft',
    version: graph.version || '2.0.0',
    summary: graph.summary || '',
    entry: graph.entry || (graph.nodes || [])[0]?.id || null,
    settings: graph.settings || {},
    input_schema: graph.input_schema || null,
    output_schema: graph.output_schema || null,
    $defs: graph.$defs || {},
  });
  const [saveState, setSaveState] = useState('idle');
  const [saveError, setSaveError] = useState(null);
  // Snapshotted alongside `saveState` (never read live from the `incomplete`
  // memo at render time) -- `saveState` only updates once the autosave
  // effect below runs, one render behind the `incomplete` count itself, so
  // rendering the live count next to a stale 'incomplete' saveState could
  // briefly show a self-contradictory "Not saved · 0 steps incomplete" the
  // instant the last field is fixed, before the effect catches up. Both are
  // set in the same effect run now, so they can never disagree.
  const [incompleteCount, setIncompleteCount] = useState(0);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [inspectorPosition, setInspectorPosition] = useState(null);
  // Mirrors confirmedNodeIdsRef for the currently open node only, captured
  // once at open time (see positionInspector) -- render can't read a ref
  // directly, and this doesn't need to react to anything else.
  const [selectedNodeIsNew, setSelectedNodeIsNew] = useState(false);
  const canvasRef = useRef(null);
  const panelRef = useRef(null);
  // Which node ids have a legitimate prior state to fall back to -- either
  // loaded from the saved graph, or previously closed here while complete.
  // A freshly inserted node starts outside this set: closing it invalid has
  // nothing sane to revert to, so it deletes instead (see closeInspector).
  const confirmedNodeIdsRef = useRef(new Set((graph.nodes || []).map((node) => node.id)));
  // The selected node's own fields as they stood the moment it was opened,
  // so an invalid close on an already-confirmed node can discard just this
  // edit rather than the whole step.
  const openSnapshotRef = useRef(null);
  const selectedNodeIdRef = useRef(null);
  useEffect(() => { selectedNodeIdRef.current = selectedNodeId; }, [selectedNodeId]);

  useEffect(() => { persistRef.current = onPersist; }, [onPersist]);

  // Refs mirror the latest state synchronously so insertAt and friends
  // can compute a consistent next state without nesting setState calls
  // inside other setState updaters.
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const metaRef = useRef(meta);
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  const positionInspector = useCallback((id, target) => {
    const canvas = canvasRef.current;
    if (!canvas || !target) return;
    // Switching straight from one card to another (no explicit close in
    // between) is still a "leave it as-is" for whatever was open, provided
    // it was actually complete -- selectNode already refuses to switch away
    // from an incomplete one, so reaching here means it graduates too.
    const previousId = selectedNodeIdRef.current;
    if (previousId && previousId !== id) {
      const previousNode = nodesRef.current.find((node) => node.id === previousId);
      if (previousNode && isNodeComplete(previousNode)) {
        confirmedNodeIdsRef.current.add(previousId);
      }
    }
    const openingNode = nodesRef.current.find((node) => node.id === id);
    openSnapshotRef.current = openingNode ? { ...openingNode } : null;
    const canvasRect = canvas.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const panelWidth = 340;
    const left = Math.max(16, Math.min(
      targetRect.right - canvasRect.left + 14,
      canvasRect.width - panelWidth - 16,
    ));
    const top = Math.max(72, targetRect.top - canvasRect.top);
    // Placed right next to the clicked node first; a node's config fields
    // vary a lot by kind (a bare noop vs. a prompt's two textareas), so
    // there's no single safe height to assume up front. The layout effect
    // below measures the panel's real content once it's mounted and, ONLY
    // if that overflows the canvas, slides the panel up just enough to
    // fit -- rather than guessing a generic budget that either strands the
    // panel far from a small node (too generous) or clips a tall node's
    // fields out of the clickable area (too tight, see prior regressions).
    setSelectedNodeId(id);
    setSelectedNodeIsNew(!confirmedNodeIdsRef.current.has(id));
    setInspectorPosition({ left, top, maxHeight: canvasRect.height - top - 16 });
  }, []);
  // A held-open step refuses selection of any other card, or "held open" would
  // only mean "held open until you click the next one". Insertion deliberately
  // still works and carries the lock to the new step -- the one it leaves
  // behind keeps its badge, and the save stays withheld for both.
  const lockedNodeIdRef = useRef(null);
  useEffect(() => {
    const selected = nodes.find((node) => node.id === selectedNodeId) || null;
    lockedNodeIdRef.current = selected && !isNodeComplete(selected) ? selected.id : null;
  }, [nodes, selectedNodeId]);
  useEffect(() => {
    if (!selectedNodeId) openSnapshotRef.current = null;
  }, [selectedNodeId]);
  const selectNode = useCallback((id, event) => {
    const locked = lockedNodeIdRef.current;
    if (locked && locked !== id) return;
    positionInspector(id, event.currentTarget);
  }, [positionInspector]);
  // A newly inserted node opens its own inspector. Every kind except `noop`
  // lands with its required config empty, so the fields have to be put in
  // front of the author rather than left to be discovered after a rejected
  // save. Positioning needs the card's real geometry, which only exists once
  // React has committed it -- hence the ref handoff instead of selecting
  // inline in insertAt.
  const pendingSelectRef = useRef(null);
  useLayoutEffect(() => {
    const id = pendingSelectRef.current;
    if (!id) return;
    const escaped = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(id) : id;
    const target = canvasRef.current?.querySelector(`[data-node-id="${escaped}"]`);
    if (!target) return;
    pendingSelectRef.current = null;
    positionInspector(id, target);
  }, [nodes, positionInspector]);
  // One-shot correction per selection: measures the panel's actual
  // (unclamped) content height once it's mounted, and only then decides
  // whether it needs to slide up to fit -- see selectNode above for why
  // this can't be guessed ahead of render.
  useLayoutEffect(() => {
    const panel = panelRef.current;
    const canvas = canvasRef.current;
    if (!selectedNodeId || !panel || !canvas) return;
    const head = panel.querySelector('.routine-node-inspector__head');
    const body = panel.querySelector('.routine-node-inspector__body');
    const foot = panel.querySelector('.routine-node-inspector__foot');
    if (!head || !body || !foot) return;
    const canvasRect = canvas.getBoundingClientRect();
    const bottomLimit = canvasRect.height - 16;
    // body has its own overflow:auto, so its scrollHeight is the field
    // content's true extent regardless of how clamped the panel currently
    // renders it -- unlike the outer panel, which is overflow:hidden and
    // would just report back whatever height it's already been clamped to.
    const naturalHeight = head.offsetHeight + body.scrollHeight + foot.offsetHeight;
    setInspectorPosition((current) => {
      if (!current) return current;
      if (current.top + naturalHeight <= bottomLimit) {
        // Fits as placed -- just stop clamping height below what it needs.
        return current.maxHeight >= naturalHeight ? current : { ...current, maxHeight: naturalHeight };
      }
      const top = Math.max(16, bottomLimit - naturalHeight);
      const maxHeight = Math.min(naturalHeight, bottomLimit - top);
      if (top === current.top && maxHeight === current.maxHeight) return current;
      return { ...current, top, maxHeight };
    });
  }, [selectedNodeId]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);
  useEffect(() => { metaRef.current = meta; }, [meta]);

  const updateNode = useCallback((id, patch) => {
    setNodes((current) => current.map((node) => (node.id === id ? { ...node, ...patch } : node)));
  }, []);

  const deleteNode = useCallback((id) => {
    confirmedNodeIdsRef.current.delete(id);
    // A loop's start and tail are its structural boundary: the guard edge
    // is `to` the start (found from the tail) or `from` the tail (found
    // from the start) -- either way it's the same edge, and it never
    // survives either boundary node going. Reattaching through it (its
    // primary `to` is always the loop's own start) would silently turn it
    // into a self-loop; reattaching *as* it (keeping `loop_guard` on a
    // rewritten edge) would leave a guard with nothing left to guard. The
    // loop's actual exit -- its `on_false`, if any -- is what a deleted
    // boundary node's own predecessor should land on instead.
    const guardEdge = edgesRef.current.find((edge) => edge.loop_guard && (edge.to === id || edge.from === id));
    const outgoing = edgesRef.current.find((edge) => isFlowEdge(edge) && edgeSourceIds(edge).includes(id));
    // A router's own edge has no `to` -- its nearest thing to "what happens
    // next" is its `default` arm, the one place a router already names
    // "nothing more specific applied". No default -> no single answer
    // (it had several branches, not one), same as the plain-edge case
    // below when there's nothing to fall back to.
    const singleTarget = guardEdge
      ? (typeof guardEdge.loop_guard.on_false === 'string' ? guardEdge.loop_guard.on_false : null)
      : outgoing?.router
        ? (typeof outgoing.default === 'string' ? outgoing.default : null)
        : (outgoing?.to && typeof outgoing.to === 'string' ? outgoing.to : null);
    // A condition's two arms are structural, not author-defined, so deleting
    // the step sitting on one has to leave a placeholder behind. Dropping the
    // arm instead would leave a one-armed condition the editor offers no
    // "+ Add branch" to repair. Computed up front so the state updaters below
    // stay pure (see buildChainTree's comment on StrictMode double-invokes).
    const armPlaceholders = [];
    if (!singleTarget) {
      const conditionIds = new Set(
        nodesRef.current.filter((node) => node.kind === 'condition').map((node) => node.id),
      );
      edgesRef.current.forEach((edge) => {
        if (!edge.router || !conditionIds.has(edge.from)) return;
        Object.entries(edge.branches || {}).forEach(([value, target]) => {
          if (target !== id) return;
          const armId = uniqueNodeId('noop', [...nodesRef.current, ...armPlaceholders.map((arm) => arm.node)]);
          armPlaceholders.push({
            from: edge.from,
            value,
            node: { ...makeNode('noop'), id: armId, label: titleCase(value) },
          });
        });
      });
    }
    setNodes((current) => [
      ...current.filter((node) => node.id !== id),
      ...armPlaceholders.map((arm) => arm.node),
    ]);
    setEdges((current) => {
      const next = [];
      current.forEach((edge) => {
        // This loop's own guard edge never survives either boundary node
        // going -- see the comment above `guardEdge`.
        if (edge.loop_guard && (edge.to === id || edge.from === id)) return;
        // The deleted node's own outgoing edge -- plain, fan_in-target, or
        // (a router node deleted outright) its own route edge -- never
        // survives it. What was built inside its branches isn't cascade-
        // deleted; losing this edge just makes it unreachable, exactly
        // like any other content UnreachableTray already exists to catch.
        if (edgeSourceIds(edge).includes(id) && edge.level !== 'fan_in') return;
        if (edge.router) {
          const branches = { ...edge.branches };
          let changed = false;
          Object.entries(branches).forEach(([value, target]) => {
            if (target !== id) return;
            changed = true;
            const placeholder = armPlaceholders.find(
              (arm) => arm.from === edge.from && arm.value === value,
            );
            if (singleTarget) branches[value] = singleTarget;
            else if (placeholder) branches[value] = placeholder.node.id;
            else delete branches[value];
          });
          let defaultTarget = edge.default;
          if (defaultTarget === id) {
            changed = true;
            defaultTarget = singleTarget || undefined;
          }
          if (!changed) { next.push(edge); return; }
          if (!Object.keys(branches).length && !defaultTarget) return; // router now empty, drop it
          if (!Object.keys(branches).length) {
            next.push({ from: edge.from, to: defaultTarget, level: 'normal' });
            return;
          }
          next.push({ ...edge, branches, ...(defaultTarget ? { default: defaultTarget } : { default: undefined } ) });
          return;
        }
        if (edge.level === 'fan_in') {
          if (edge.to === id) {
            if (singleTarget) next.push({ ...edge, to: singleTarget });
            return;
          }
          const sources = edgeSourceIds(edge).filter((source) => source !== id);
          if (sources.length === edgeSourceIds(edge).length) { next.push(edge); return; } // id wasn't a source; unchanged
          if (sources.length >= 2) { next.push({ ...edge, from: sources }); return; }
          if (sources.length === 1) { next.push({ from: sources[0], to: edge.to, level: 'normal' }); return; }
          return; // no sources left, drop the edge
        }
        if (edge.to === id) {
          if (singleTarget) next.push({ ...edge, to: singleTarget });
          return;
        }
        next.push(edge);
      });
      return next;
    });
    // The virtual Start pointing at `entry` is itself an incoming reference
    // to the deleted node -- splice it through to singleTarget exactly like
    // every other incoming edge above, rather than leaving `entry` null.
    setMeta((current) => (current.entry === id ? { ...current, entry: singleTarget } : current));
  }, []);

  // The × button's close: an invalid step behaves like the Delete step
  // button if it never had a valid prior state (a freshly inserted node
  // whose required field was never filled in), or discards just this edit
  // and reverts to the open-time snapshot if it did. A complete step just
  // closes normally, and graduates into "has a valid prior state" for next
  // time.
  const closeInspector = useCallback((node) => {
    if (node) {
      if (isNodeComplete(node)) {
        confirmedNodeIdsRef.current.add(node.id);
      } else if (confirmedNodeIdsRef.current.has(node.id)) {
        if (openSnapshotRef.current) updateNode(node.id, openSnapshotRef.current);
      } else {
        deleteNode(node.id);
      }
    }
    setSelectedNodeId(null);
    setInspectorPosition(null);
  }, [updateNode, deleteNode]);

  // Two kinds arrive pre-wired because their shape is intrinsic, not authored.
  // A Send foreach owns a scoped body: its one normal outgoing edge names the
  // first dynamic target, and the companion noop marks the body boundary so
  // the rigid chain renderer can still show the continuation after the
  // fan-out. A condition is a two-way gate: it is *always* a router over
  // exactly `true`/`false` (see CONDITION_BRANCH_VALUES server-side), so it
  // lands as a fork rather than being authored as one after the fact.
  const insertAt = useCallback((slot, kind) => {
    // `loop` has no backend node kind of its own -- the body-start node it
    // creates is a plain `noop`, exactly like a foreach's "Join each" stop
    // marker is. Looping is entirely a property of the tail's own edge
    // (`loop_guard`), never of a node, so nothing here ever writes a
    // `kind: 'loop'` onto the wire.
    const nodeKind = kind === 'loop' ? 'noop' : kind;
    const id = uniqueNodeId(kind, nodesRef.current);
    const added = [{ ...makeNode(nodeKind), id, ...(kind === 'loop' ? { label: 'Loop' } : {}) }];
    let targetId = null;
    let joinEdge = null;
    const nextEdges = edgesRef.current.map((edge) => (
      edge.branches ? { ...edge, branches: { ...edge.branches } } : { ...edge }
    ));
    if (slot.kind === 'entry') {
      targetId = metaRef.current.entry;
      setMeta((current) => ({ ...current, entry: id }));
    } else if (slot.kind === 'plain' || slot.kind === 'foreach') {
      // `foreach` is the slot inside a fan-out's body. The edge leaving the
      // fan-out node is that body's entry, so it splices like a plain one.
      const existing = nextEdges.find((edge) => isFlowEdge(edge) && edgeSourceIds(edge).includes(slot.sourceId));
      if (existing?.level === 'fan_in') {
        // The step feeds a join, and a join names its sources rather than
        // being pointed at, so there is no `to` here to steal. Splice by
        // handing this lane's slot in the join over to the new step: pushing
        // a second outgoing edge instead leaves the old step with two edge
        // declarations, which the backend rejects outright.
        joinEdge = existing;
        nextEdges.push({ from: slot.sourceId, to: id, level: 'normal' });
      } else if (existing) {
        targetId = existing.to;
        existing.to = id;
      } else {
        nextEdges.push({ from: slot.sourceId, to: id, level: 'normal' });
      }
    } else if (slot.kind === 'branch') {
      const router = nextEdges.find((edge) => edge.from === slot.sourceId);
      if (router) {
        targetId = router.branches[slot.value];
        router.branches[slot.value] = id;
      }
    } else if (slot.kind === 'default') {
      const router = nextEdges.find((edge) => edge.from === slot.sourceId);
      if (router) {
        targetId = router.default;
        router.default = id;
      }
    } else if (slot.kind === 'loop-exit') {
      // The connector after a loop's box: its source is the tail, whose
      // own outgoing edge is the guard itself, so this must not touch
      // `to` (always the loop's own start) the way a plain slot would --
      // only the guard's on_false, its actual "next".
      const tail = nextEdges.find((edge) => edge.from === slot.sourceId && edge.loop_guard);
      if (tail) {
        targetId = tail.loop_guard.on_false;
        tail.loop_guard = { ...tail.loop_guard, on_false: id };
      }
    }
    // Whatever the new step ends on is the lane's terminal from here: a
    // fan-out's companion stop, a condition's two arms, or the step itself.
    let terminalIds = [id];
    if (kind === 'loop') {
      // A stable tail marker the body always flows back through -- never a
      // moving target -- so extending the body later (inserting at the
      // connector just before the tail) is an ordinary splice: body
      // extension only ever touches edges INTO the tail, never the tail's
      // own outgoing (guarded) edge. Mirrors foreach's stop-node shape
      // exactly, just with a back-edge instead of a forward one.
      //
      // There's no dedicated counter node kind: `eval` already IS the
      // general "evaluate an expression, write the result" primitive, and
      // an increment is just an expression. LangGraph's conditional-edge
      // routing function is typed to return only a path name, never also
      // update state (checked against the installed langgraph's own
      // add_conditional_edges signature) -- a plain edge genuinely cannot
      // count itself -- but a node executing right before that edge fires,
      // every single time, is the closest honest equivalent. Its own
      // increment commits before its outgoing (guarded) edge evaluates, so
      // the guard always sees the fresh count.
      //
      // `tailId` is the node's own state key -- expressions resolve names
      // directly against state now (no separate `inputs` binding step), so
      // the expression just references it by name. The only thing `eval`
      // needed to cover this case was a fallback for its own not-yet-
      // written prior output on the loop's first visit -- `config.defaults`
      // supplies that (see EvalNodeConfig.defaults).
      const tailId = uniqueNodeId('count', [...nodesRef.current, ...added]);
      added.push({
        ...makeNode('eval'),
        id: tailId,
        label: '↺ Continue',
        config: { expression: `${tailId} + 1`, defaults: { [tailId]: 0 } },
      });
      nextEdges.push({ from: id, to: tailId, level: 'normal' });
      // A loop spliced in front of a shared join reaches it directly
      // through its own guard's on_false, not by joining the declared
      // fan_in sources -- the tail already has its own outgoing edge
      // (this one), and a node can only declare one.
      const exitTarget = joinEdge ? joinEdge.to : targetId;
      nextEdges.push({
        from: tailId,
        to: id,
        level: 'normal',
        loop_guard: {
          expression: `${tailId} < 3`,
          ...(exitTarget ? { on_false: exitTarget } : {}),
        },
      });
      terminalIds = [];
    } else if (kind === 'foreach') {
      const stopId = uniqueNodeId('noop', [...nodesRef.current, ...added]);
      added.push({ ...makeNode('noop'), id: stopId, label: 'Join each' });
      nextEdges.push({ from: id, to: stopId, level: 'normal' });
      if (targetId) nextEdges.push({ from: stopId, to: targetId, level: 'normal' });
      terminalIds = [stopId];
    } else if (kind === 'condition') {
      // Both arms get their own placeholder step. Whatever the condition was
      // spliced in front of stays reachable as a fan_in join below the fork
      // rather than being stranded on one arbitrary arm.
      const arms = CONDITION_BRANCHES.map((value) => {
        const armId = uniqueNodeId('noop', [...nodesRef.current, ...added]);
        added.push({ ...makeNode('noop'), id: armId, label: titleCase(value) });
        return [value, armId];
      });
      nextEdges.push({
        from: id,
        level: 'route',
        router: 'state_equals',
        state_key: id,
        branches: Object.fromEntries(arms),
      });
      terminalIds = arms.map(([, armId]) => armId);
      if (targetId) {
        nextEdges.push({ from: terminalIds, to: targetId, level: 'fan_in' });
      }
    } else if (kind === 'switch' || kind === 'split') {
      // One growable arm, not two fixed ones: `switch`/`split` are
      // open-ended (author adds more via "+ Add branch"), unlike
      // `condition`'s fixed true/false pair. Whatever the connector was
      // pointing at before insertion carries straight through as the
      // router's `default` rather than needing a separate fan_in join --
      // exactly the shape the old (removed) "Split into branches" action
      // used to produce by hand.
      const armId = uniqueNodeId('noop', [...nodesRef.current, ...added]);
      added.push({ ...makeNode('noop'), id: armId, label: kind === 'switch' ? 'Case 1' : 'Path 1' });
      nextEdges.push({
        from: id,
        level: 'route',
        router: 'state_equals',
        state_key: id,
        branches: { 'case-1': armId },
        ...(targetId ? { default: targetId } : {}),
      });
      terminalIds = [armId];
    } else if (targetId) {
      nextEdges.push({ from: id, to: targetId, level: 'normal' });
    }
    if (joinEdge) {
      joinEdge.from = [
        ...edgeSourceIds(joinEdge).filter((source) => source !== slot.sourceId),
        ...terminalIds,
      ];
    }
    // The loop-start node has nothing of its own to configure (it's a bare
    // `noop`; its badge would misleadingly read "Noop" too) -- the
    // author's next move is the tail's own expression popover, not this
    // inspector, so don't steal focus with it.
    if (kind !== 'loop') pendingSelectRef.current = id;
    setNodes((current) => [...current, ...added]);
    setEdges(nextEdges);
  }, []);

  const onChangeStateKey = useCallback((sourceId, value) => {
    setEdges((current) => current.map((edge) => (
      edge.from === sourceId && edge.router ? { ...edge, state_key: value } : edge
    )));
  }, []);

  const updateLoopGuard = useCallback((fromId, patch) => {
    setEdges((current) => current.map((edge) => (
      edge.from === fromId && edge.loop_guard ? { ...edge, loop_guard: { ...edge.loop_guard, ...patch } } : edge
    )));
  }, []);

  const removeBranching = useCallback((sourceId) => {
    setEdges((current) => current.map((edge) => {
      if (edge.from !== sourceId || !edge.router) return edge;
      return edge.default ? { from: sourceId, to: edge.default, level: 'normal' } : null;
    }).filter(Boolean));
  }, []);

  const addLane = useCallback((sourceId, isDefault) => {
    const laneId = uniqueNodeId('noop', nodesRef.current);
    setEdges((current) => addForkLane(current, sourceId, laneId, isDefault));
    setNodes((current) => [...current, { ...makeNode('noop'), id: laneId }]);
  }, []);

  const onAddBranch = useCallback((sourceId) => addLane(sourceId, false), [addLane]);

  const onAddDefault = useCallback((sourceId) => addLane(sourceId, true), [addLane]);


  const onJoinBranches = useCallback((sourceId, kind = 'noop') => {
    const terminals = joinTerminals(edgesRef.current, sourceId);
    if (!terminals.length) return;
    const joinId = uniqueNodeId(kind, nodesRef.current);
    const nextEdges = [
      ...edgesRef.current,
      { from: terminals, to: joinId, level: 'fan_in' },
    ];
    setNodes((current) => [...current, {
      ...makeNode(kind),
      id: joinId,
      ...(kind === 'noop' ? { label: 'Join branches' } : {}),
    }]);
    setEdges(nextEdges);
  }, []);

  // Both halves of a lane drop have to land together: the edge rewrite decides
  // which steps the lane owned, and those steps are then deleted rather than
  // left behind as unreachable clutter.
  const dropLane = useCallback((drop) => {
    const { edges: nextEdges, removedNodeIds } = drop(edgesRef.current, metaRef.current.entry);
    setEdges(nextEdges);
    if (removedNodeIds.length) {
      const removed = new Set(removedNodeIds);
      setNodes((current) => current.filter((node) => !removed.has(node.id)));
    }
  }, []);

  const onRemoveBranch = useCallback((sourceId, value) => {
    dropLane((edges, entry) => removeForkBranch(edges, entry, sourceId, value));
  }, [dropLane]);

  const onRemoveDefault = useCallback((sourceId) => {
    dropLane((edges, entry) => removeForkDefault(edges, entry, sourceId));
  }, [dropLane]);

  const onRenameBranch = useCallback((sourceId, oldValue, newValue) => {
    setEdges((current) => current.map((edge) => {
      if (edge.from !== sourceId || !edge.router || !(oldValue in (edge.branches || {}))) return edge;
      const branches = {};
      Object.entries(edge.branches).forEach(([key, val]) => {
        branches[key === oldValue ? newValue : key] = val;
      });
      return { ...edge, branches };
    }));
  }, []);

  // Restoring pushes the browsed run's revision straight into the live
  // draft AND persists it immediately (unlike a normal edit, which only
  // autosaves on its usual debounce) -- Restore is the one explicit way
  // out of read-only history browsing, so the moment it's confirmed the
  // canvas has to already be showing, and be editable against, that exact
  // content, not a dirty draft still catching up to it.
  const [restoreConfirmOpen, setRestoreConfirmOpen] = useState(false);
  const handleRestoreRun = useCallback(async () => {
    const browsed = runNav.browsedGraph;
    if (!browsed) return;
    const nextMeta = {
      status: browsed.status || 'Draft',
      version: browsed.version || '2.0.0',
      summary: browsed.summary || '',
      entry: browsed.entry || (browsed.nodes || [])[0]?.id || null,
      settings: browsed.settings || {},
      input_schema: browsed.input_schema || null,
      output_schema: browsed.output_schema || null,
      $defs: browsed.$defs || {},
    };
    const nextNodes = (browsed.nodes || []).map((node) => ({ ...node }));
    const nextEdges = (browsed.edges || []).map((edge) => ({ ...edge }));
    setMeta(nextMeta);
    setNodes(nextNodes);
    setEdges(nextEdges);
    const payload = serializeGraph(nextNodes, nextEdges, nextMeta);
    await persistRef.current(payload);
    lastSavedRef.current = JSON.stringify(payload);
    setSaveError(null);
    setSaveState('saved');
    runNav.resetToLatest();
    setRestoreConfirmOpen(false);
  }, [runNav]);

  const displayNodes = useMemo(
    () => (runNav.isBrowsingHistory ? (runNav.browsedGraph?.nodes || []) : nodes),
    [runNav.isBrowsingHistory, runNav.browsedGraph, nodes],
  );
  const displayEdges = useMemo(
    () => (runNav.isBrowsingHistory ? (runNav.browsedGraph?.edges || []) : edges),
    [runNav.isBrowsingHistory, runNav.browsedGraph, edges],
  );
  const displayEntry = runNav.isBrowsingHistory ? (runNav.browsedGraph?.entry ?? null) : meta.entry;

  // Which node ids have at least one recorded execution this run, and --
  // derived from that -- which node(s) are probably executing right now
  // (see deriveActiveNodeIds). Recomputed off `runMeta` itself, not
  // `runStatus`, so the sub-headers keep updating on every poll tick while
  // `status` stays 'running'.
  const completedNodeIds = useMemo(
    () => new Set(Object.keys(runMeta || {}).filter((id) => (runMeta[id] || []).length > 0)),
    [runMeta],
  );
  const activeNodeIds = useMemo(
    () => (runStatus === 'running' || runStatus === 'discovering'
      ? deriveActiveNodeIds(displayNodes, displayEdges, displayEntry, completedNodeIds)
      : new Set()),
    [runStatus, displayNodes, displayEdges, displayEntry, completedNodeIds],
  );
  const currentGraphForStaleness = useMemo(
    () => ({ entry: meta.entry, nodes, edges }),
    [meta.entry, nodes, edges],
  );
  const rawStaleNodeIds = useRunStaleness(agent.id, runRevisionId, currentGraphForStaleness);
  const staleNodeIds = runNav.isBrowsingHistory ? EMPTY_MISSING : rawStaleNodeIds;
  // True only during the client discovery poll (finding this run's real
  // thread_id) -- the one window with no reliable timestamp to count from
  // at all. Once the thread turns up, `estimatedActiveStartMs` anchors on
  // that real moment instead of the run's click time (see
  // useRoutineRunPolling), so counting can start honestly even before the
  // entry node's own completion exists (see ChainCap/StepRunSummary).
  const warmingUp = !runNav.isBrowsingHistory && runStatus === 'discovering';
  const loopMembership = useMemo(() => buildLoopMembership(displayEdges), [displayEdges]);
  const editContext = useMemo(() => ({
    updateNode,
    deleteNode,
    insertAt,
    onChangeStateKey,
    updateLoopGuard,
    onRemoveBranching: removeBranching,
    onAddBranch,
    onJoinBranches,
    selectNode,
    selectedNodeId,
    onAddDefault,
    onRemoveBranch,
    onRemoveDefault,
    onRenameBranch,
    settings: meta.settings,
    edges: displayEdges,
    nodes: displayNodes,
    inputSchema: meta.input_schema,
    readOnly: runNav.isBrowsingHistory,
    runMeta,
    runData,
    activeNodeIds,
    estimatedActiveStartMs,
    staleNodeIds,
    runStatus,
    warmingUp,
    loopMembership,
    loopIterations,
    setLoopIteration,
    startRun: startRunAndTrackLatest,
    hasInteractiveSession,
    resetInteractiveSession,
  }), [updateNode, deleteNode, insertAt, onChangeStateKey, updateLoopGuard, removeBranching, onAddBranch, onJoinBranches, selectNode, selectedNodeId, onAddDefault, onRemoveBranch, onRemoveDefault, onRenameBranch, meta.settings, displayEdges, displayNodes, meta.input_schema, runNav.isBrowsingHistory, runMeta, runData, activeNodeIds, estimatedActiveStartMs, staleNodeIds, runStatus, warmingUp, loopMembership, loopIterations, setLoopIteration, startRunAndTrackLatest, hasInteractiveSession, resetInteractiveSession]);

  // Debounced autosave: serialize the working graph and PATCH it. The first
  // run records the baseline so simply opening the tab does not trigger a save.
  // A graph carrying a node with unfilled required config is withheld rather
  // than sent: the backend would reject it, and an author midway through
  // filling in a step they just inserted has not made a mistake worth a red
  // banner. `lastSavedRef` is deliberately left alone so the moment the last
  // field lands the signature still differs and the save goes out on its own.
  const incomplete = useMemo(
    () => incompleteNodes(nodes).length + incompleteLoopEdges(edges).length,
    [nodes, edges],
  );
  // Declared input_schema.required names with no (or blank) value filled in
  // yet -- gates the Run button the same way `incomplete` gates it for node
  // config, so a routine that needs parameters can't be run silently empty.
  const missingRequiredParams = useMemo(
    () => (meta.input_schema?.required || []).filter((name) => {
      const value = parameterValues[name];
      return value === undefined || value === null || value === '';
    }),
    [meta.input_schema, parameterValues],
  );
  useEffect(() => {
    const payload = serializeGraph(nodes, edges, meta);
    const signature = JSON.stringify(payload);
    if (lastSavedRef.current === null) {
      lastSavedRef.current = signature;
      return undefined;
    }
    if (signature === lastSavedRef.current) {
      setSaveError(null);
      setSaveState(saveStatusWhenInSync);
      return undefined;
    }
    if (incomplete) {
      setSaveError(null);
      setIncompleteCount(incomplete);
      setSaveState('incomplete');
      return undefined;
    }
    const timer = setTimeout(async () => {
      setSaveState('saving');
      try {
        await persistRef.current(payload);
        lastSavedRef.current = signature;
        setSaveError(null);
        setSaveState('saved');
      } catch (error) {
        setSaveError(error.message || 'Save failed');
        setSaveState('error');
      }
    }, 700);
    return () => clearTimeout(timer);
  }, [nodes, edges, meta, incomplete]);

  const workingGraph = useMemo(() => serializeGraph(nodes, edges, meta), [nodes, edges, meta]);
  // Run must match what the server actually has, not just what this tab
  // optimistically believes it sent -- `confirmedGraph` is the live
  // Redux-tracked agent graph, updated only by the `agent.graph.updated`
  // SSE round-trip (see AgentsPage's `selectedGraph`), so comparing its
  // signature to the working copy's is the same "did the save round-trip
  // land" check the autosave chip already implies, made explicit for Run.
  const isServerConfirmed = useMemo(
    () => JSON.stringify(workingGraph)
      === JSON.stringify(serializeGraph(confirmedGraph?.nodes || [], confirmedGraph?.edges || [], confirmedGraph || {})),
    [workingGraph, confirmedGraph],
  );
  const selectedNode = nodes.find((node) => node.id === selectedNodeId) || null;
  const selectedRouterEdge = selectedNode
    ? edges.find((edge) => edge.from === selectedNode.id && edge.router)
    : null;
  // A loop's own guard expression lives on its tail's edge, not on this
  // node's config -- this is how the currently-selected node is recognized
  // as a loop's start: some edge's `loop_guard` targets it.
  const selectedGuardEdge = selectedNode
    ? edges.find((edge) => edge.loop_guard && edge.to === selectedNode.id)
    : null;
  const validationErrors = useMemo(
    () => validateWorkingGraph(nodes, edges, meta.entry),
    [nodes, edges, meta.entry],
  );

  const handleJsonApply = useCallback(async (parsed) => {
    const nextMeta = {
      status: parsed.status || 'Draft',
      version: parsed.version || '2.0.0',
      summary: parsed.summary || '',
      entry: parsed.entry || (parsed.nodes || [])[0]?.id || null,
      settings: parsed.settings || {},
      input_schema: parsed.input_schema || null,
      output_schema: parsed.output_schema || null,
      $defs: parsed.$defs || {},
    };
    setMeta(nextMeta);
    setNodes((parsed.nodes || []).map((node) => ({ ...node })));
    setEdges((parsed.edges || []).map((edge) => ({ ...edge })));
    const payload = serializeGraph(parsed.nodes || [], parsed.edges || [], nextMeta);
    await persistRef.current(payload);
    lastSavedRef.current = JSON.stringify(payload);
    setSaveError(null);
    setSaveState('saved');
  }, []);

  return (
    <>
      <section className="agents-graph-panel" style={{ '--agent-color': agent.color }}>
        <div className="agents-graph-shell">
          <div ref={canvasRef} className="canvas-surface agents-graph-canvas routine-canvas">
            <div className="agents-graph-floating">
              <DetailHeaderActions menuLabel="Routine actions">
                {onShowProfile ? (
                  <DetailHeaderAction
                    icon={<OpenActionIcon />}
                    label="Details"
                    tooltip="View agent details"
                    ariaLabel="View agent details"
                    onClick={onShowProfile}
                  />
                ) : null}
                <DetailHeaderAction
                  icon={<PlayActionIcon />}
                  label="Run"
                  tooltip={
                    runNav.isBrowsingHistory ? 'Step back to the latest run (or Restore this one) before running'
                      : !isServerConfirmed ? 'Waiting for save to be confirmed…'
                      : missingRequiredParams.length > 0 ? `Fill in ${missingRequiredParams.length} required parameter${missingRequiredParams.length === 1 ? '' : 's'} first`
                      : 'Run routine'
                  }
                  ariaLabel="Run routine"
                  onClick={() => startRunAndTrackLatest()}
                  disabled={runNav.isBrowsingHistory || validationErrors.length > 0 || incomplete > 0 || !isServerConfirmed || missingRequiredParams.length > 0}
                />
                <DetailHeaderAction
                  icon={<RestoreActionIcon />}
                  label="Restore"
                  tooltip={
                    !runNav.isBrowsingHistory ? 'Step back through a previous run to restore it'
                      : runNav.canRestore ? 'Restore this run\u2019s graph as the live draft'
                      : 'Loading this run\u2019s graph…'
                  }
                  ariaLabel="Restore this run's graph"
                  onClick={() => setRestoreConfirmOpen(true)}
                  disabled={!runNav.isBrowsingHistory || !runNav.canRestore}
                />
                {onDelete ? (
                  <DetailHeaderAction
                    icon={<DeleteActionIcon />}
                    label="Delete"
                    tooltip="Delete routine"
                    ariaLabel="Delete routine"
                    variant="danger"
                    onClick={onDelete}
                    disabled={runNav.isBrowsingHistory}
                  />
                ) : null}
              </DetailHeaderActions>
            </div>
            {runNav.hasRuns ? (
              <div className="agents-graph-floating agents-graph-floating--history">
                <DetailHeaderAction
                  icon={<ChevronLeftSmall />}
                  label="Older run"
                  tooltip="View the previous run"
                  ariaLabel="View the previous run"
                  onClick={runNav.goBack}
                  disabled={!runNav.canGoBack}
                />
                <span className="agents-graph-run-nav__label" title={runNav.title}>
                  {runNav.label}
                </span>
                <DetailHeaderAction
                  icon={<ChevronRightPointSmall />}
                  label="Newer run"
                  tooltip="View the next run"
                  ariaLabel="View the next run"
                  onClick={runNav.goForward}
                  disabled={!runNav.canGoForward}
                />
              </div>
            ) : null}
            {runNav.isBrowsingHistory ? null : (
              <div className="agents-graph-floating agents-graph-floating--status">
                {saveState !== 'idle' ? (
                  <span className={`agents-graph-save agents-graph-save--${saveState}`} role="status">
                    {saveState === 'saving' ? 'Saving…'
                      : saveState === 'saved' ? 'Saved'
                      : saveState === 'incomplete' ? `Not saved · ${incompleteCount} step${incompleteCount === 1 ? '' : 's'} incomplete`
                          : 'Save failed'}
                  </span>
                ) : null}
                <span
                  className={`agents-graph-validation ${validationErrors.length ? 'agents-graph-validation--error' : 'agents-graph-validation--ok'}`}
                  role="status"
                  aria-label={validationErrors.length ? `Graph invalid: ${validationErrors.join(' ')}` : 'Graph valid'}
                >
                  {validationErrors.length ? `${validationErrors.length} validation issue${validationErrors.length === 1 ? '' : 's'}` : 'Valid graph'}
                </span>
              </div>
            )}
            <div className="agents-graph-zoom-controls">
              <button
                type="button"
                className="agents-graph-zoom-controls__btn"
                onClick={zoomIn}
                disabled={zoom >= ZOOM_MAX}
                title="Zoom in"
                aria-label="Zoom in"
              >
                <ZoomPlusIcon />
              </button>
              <button
                type="button"
                className="agents-graph-zoom-controls__btn"
                onClick={zoomOut}
                disabled={zoom <= ZOOM_MIN}
                title="Zoom out"
                aria-label="Zoom out"
              >
                <ZoomMinusIcon />
              </button>
              <button
                type="button"
                className="agents-graph-zoom-controls__btn"
                onClick={resetZoom}
                title="Reset zoom"
                aria-label="Reset zoom"
              >
                <ZoomFitIcon />
              </button>
            </div>
              <GraphEditContext.Provider value={editContext}>
              <div
                className="routine-canvas__scroll"
                style={{ zoom }}
                onClick={(event) => {
                  // A locked inspector still ignores the background: an
                  // accidental outside click discarding an in-progress edit
                  // (or deleting a brand-new step) would be a nasty surprise
                  // -- that only happens via the explicit × or Delete step.
                  if (selectedNode && !isNodeComplete(selectedNode)) return;
                  if (!event.target.closest('button, input, textarea, [role="button"], .routine-node-inspector')) {
                    closeInspector(selectedNode);
                  }
                }}
              >
                <RoutineChain nodes={displayNodes} edges={displayEdges} entry={displayEntry} />
              </div>
              {selectedNode && inspectorPosition ? (
                <NodeInspector
                  node={selectedNode}
                  routerEdge={selectedRouterEdge}
                  guardEdge={selectedGuardEdge}
                  onClose={() => closeInspector(selectedNode)}
                  onDismiss={() => { setSelectedNodeId(null); setInspectorPosition(null); }}
                  isNew={selectedNodeIsNew}
                  style={inspectorPosition}
                  panelRef={panelRef}
                />
              ) : null}
            </GraphEditContext.Provider>
          </div>
          {saveError ? <div className="agents-graph-save-error">{saveError}</div> : null}
        </div>
      </section>
      {jsonOpen ? (
        <AgentGraphEditor
          key={agent.id}
          graph={workingGraph}
          onSave={handleJsonApply}
          onClose={onCloseJson}
        />
      ) : parametersOpen ? (
        <RoutineParametersPanel
          key={agent.id}
          inputSchema={meta.input_schema}
          values={parameterValues}
          onChange={setParameterValues}
          onClose={onCloseParameters}
        />
      ) : runsOpen ? (
        <RoutineRunsPanel
          key={agent.id}
          agentId={agent.id}
          onClose={onCloseRuns}
          runsList={runNav.runsList}
          viewedIndex={runNav.viewedIndex}
          onSelectIndex={runNav.jumpTo}
        />
      ) : (
        <GraphSideDock
          disabledIds={runNav.isBrowsingHistory ? EDIT_SIDE_ITEM_IDS : EMPTY_MISSING}
          onSelect={(id) => { if (id === 'json') onOpenJson(); if (id === 'parameters') onOpenParameters(); if (id === 'runs') onOpenRuns(); }}
        />
      )}
      {restoreConfirmOpen ? (
        <PopupModal
          eyebrow="Restore run"
          title={`Restore the run from ${runNav.label || 'this position'}?`}
          message="This replaces your current draft with the graph this run actually executed against, and saves it immediately as the new live revision. Any unsaved changes to the current draft will be lost."
          confirmLabel="Restore"
          onClose={() => setRestoreConfirmOpen(false)}
          onConfirm={handleRestoreRun}
        />
      ) : null}
    </>
  );
}

// Chevrons for the right tools dock / collapse seam (point toward the action:
// left = expand the panel leftward, right = collapse it away).
// Chevrons for the right tools dock / collapse seam.
function ChevronLeftSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m9.8 4.2-3.6 3.8 3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

function ChevronRightSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m6.2 4.2 3.6 3.8 3.6-3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

// Mirrors ChevronLeftSmall's literal "<" shape -- ChevronRightSmall above
// is actually a down chevron (used for panel-collapse affordances, not
// direction), so the run-history nav's forward arrow needs its own icon.
function ChevronRightPointSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m6.2 4.2 3.6 3.8-3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

// A clock face with a counter-clockwise rewind arrow -- "restore to an
// earlier point in time", not ReconnectActionIcon's plain circular refresh
// loop (that's reconnect/retry, a different concept) and not a download/
// tray glyph either (that reads as "bring a file in", not "wind the clock
// back"). Always rendered in the toolbar (see the Restore button), just
// disabled outside history browsing, so its width never toggles in and out
// and shifts the rest of the toolbar/side dock around.
function RestoreActionIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M4 4.6A5.2 5.2 0 1 1 2.9 9.6" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M4.6 2.4 4 4.6 6.3 5.3" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8 6v2.6l1.8 1" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Plain +/-/corner-brackets, matching Workflows' React Flow Controls
// look for the same concept -- not a canvas-viewport zoom here though
// (this isn't React Flow): see the `zoom` state comment in GraphCanvas.
function ZoomPlusIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M8 3v10M3 8h10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function ZoomMinusIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M3 8h10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

// Four open corner brackets -- "reset/fit" (React Flow's fitView glyph);
// here it resets the CSS zoom level back to 100% rather than fitting a
// canvas viewport to content, since there is no viewport to fit.
function ZoomFitIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M2.5 5.5V3.5a1 1 0 0 1 1-1h2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13.5 5.5V3.5a1 1 0 0 0-1-1h-2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M2.5 10.5v2a1 1 0 0 0 1 1h2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13.5 10.5v2a1 1 0 0 1-1 1h-2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const GRAPH_SIDE_ITEMS = [
  { id: 'json', label: 'JSON', title: 'Edit as JSON' },
  { id: 'runs', label: 'Runs', title: 'Routine run history' },
  { id: 'parameters', label: 'Params', title: 'Routine parameters' },
];

// Locked while browsing history (see GraphSideDock's disabledIds): editing
// surfaces only. 'runs' stays enabled -- it's pure navigation, the same
// category as the toolbar's own back/forward pill, not an edit surface.
const EDIT_SIDE_ITEM_IDS = new Set(['json', 'parameters']);

function GraphSideDock({ onSelect, disabledIds }) {
  return (
    <aside className="agents-graph-side" aria-label="Graph tools">
      {GRAPH_SIDE_ITEMS.map((item) => {
        const disabled = disabledIds?.has(item.id) ?? false;
        return (
          <button
            key={item.id}
            type="button"
            className="agents-graph-side__item"
            title={disabled ? 'Step back to the latest run (or Restore this one) to edit' : item.title}
            aria-label={item.title}
            onClick={() => onSelect(item.id)}
            disabled={disabled}
          >
            <ChevronLeftSmall />
            <span className="agents-graph-side__label">{item.label}</span>
          </button>
        );
      })}
    </aside>
  );
}

export function AgentGraphTab({ agent, graph, confirmedGraph, onPersist, onShowProfile, onDelete, jsonOpen, onOpenJson, onCloseJson, runsOpen, onOpenRuns, onCloseRuns, parametersOpen, onOpenParameters, onCloseParameters }) {
  return (
    <GraphCanvas
      agent={agent}
      graph={graph}
      confirmedGraph={confirmedGraph}
      onPersist={onPersist}
      onShowProfile={onShowProfile}
      onDelete={onDelete}
      jsonOpen={jsonOpen}
      onOpenJson={onOpenJson}
      onCloseJson={onCloseJson}
      runsOpen={runsOpen}
      onOpenRuns={onOpenRuns}
      onCloseRuns={onCloseRuns}
      parametersOpen={parametersOpen}
      onOpenParameters={onOpenParameters}
      onCloseParameters={onCloseParameters}
    />
  );
}
function graphToJson(graph) {
  return JSON.stringify(
    {
      status: graph.status || 'Draft',
      version: graph.version || '2.0.0',
      summary: graph.summary || '',
      entry: graph.entry ?? null,
      nodes: graph.nodes || [],
      edges: graph.edges || [],
      settings: graph.settings || {},
      input_schema: graph.input_schema || null,
      output_schema: graph.output_schema || null,
      $defs: graph.$defs || {},
    },
    null,
    2,
  );
}

// A lightweight JSON editor: an editable textarea overlaid on a highlight.js
// rendered <pre> so the user gets JSON syntax highlighting while typing. The two
// layers share identical typography so the caret/text line up; the textarea text
// is transparent and the highlighted layer shows through.
function JsonCodeEditor({ value, onChange }) {
  const highlightRef = useRef(null);
  const highlighted = useMemo(() => {
    try {
      return hljs.highlight(value, { language: 'json' }).value;
    } catch {
      return value;
    }
  }, [value]);

  function syncScroll(event) {
    const target = highlightRef.current;
    if (target) {
      target.scrollTop = event.target.scrollTop;
      target.scrollLeft = event.target.scrollLeft;
    }
  }

  return (
    <div className="agents-graph-editor__code">
      <pre ref={highlightRef} className="agents-graph-editor__highlight" aria-hidden="true">
        <code
          className="hljs language-json"
          dangerouslySetInnerHTML={{ __html: `${highlighted}\n` }}
        />
      </pre>
      <textarea
        className="agents-graph-editor__textarea"
        spellCheck={false}
        value={value}
        onChange={onChange}
        onScroll={syncScroll}
      />
    </div>
  );
}

// Edit the agent graph by describing it as JSON conforming to the published
// schema. The PATCH endpoint validates and persists; the broadcast updates the
// canvas. Keyed by agent id so switching agents resets the draft.
function AgentGraphEditor({ graph, onSave, onClose }) {
  const [draft, setDraft] = useState(() => graphToJson(graph));
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function handleApply() {
    let parsed;
    try {
      parsed = JSON.parse(draft);
    } catch (parseError) {
      setError(`Invalid JSON: ${parseError.message}`);
      return;
    }
    if (!Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) {
      setError('Graph must include "nodes" and "edges" arrays.');
      return;
    }
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await onSave(parsed);
      setSaved(true);
    } catch (saveError) {
      setError(saveError.message || 'Failed to save graph');
    } finally {
      setSaving(false);
    }
  }

  return (
    <aside className="agents-graph-editor">
      <button
        type="button"
        className="agents-graph-editor__collapse"
        onClick={onClose}
        title="Collapse"
        aria-label="Collapse JSON editor"
      >
        <ChevronRightSmall />
      </button>
      <header className="agents-graph-editor__head">
        <h2>Edit graph as JSON</h2>
      </header>
      <JsonCodeEditor
        value={draft}
        onChange={(event) => { setDraft(event.target.value); setSaved(false); }}
      />
      {error ? <p className="agents-graph-editor__error">{error}</p> : null}
      {saved ? <p className="agents-graph-editor__saved">Graph saved.</p> : null}
      <div className="agents-graph-editor__actions">
        <button type="button" className="repo-btn repo-btn--ghost" onClick={() => { setDraft(graphToJson(graph)); setError(null); setSaved(false); }}>Reset</button>
        <button type="button" className="repo-btn" onClick={handleApply} disabled={saving}>
          {saving ? 'Saving…' : 'Apply changes'}
        </button>
      </div>
    </aside>
  );
}

// Values the user fills in for the routine's declared input_schema.properties
// before hitting Run -- session-local (GraphCanvas's parameterValues state),
// never persisted to the graph document. The schema itself (properties/
// required) is authored the same way node-level response_schema is: via the
// JSON editor, not a bespoke properties-builder UI here -- this panel only
// renders what's already declared and collects values for it.
function parameterControlValue(value) {
  return value === undefined || value === null ? '' : value;
}

// array/object-typed properties (e.g. a foreach node's items_from source)
// need a real parsed value, not a string -- the seed node copies whatever
// JS value this sends straight into state.data, and the foreach/eval nodes
// that read it expect an actual list/dict. Buffers its own text so invalid
// JSON mid-edit doesn't clobber the last-valid parsed value (same lesson as
// useBufferedFieldText elsewhere in this file, kept local since this is the
// only text<->JSON round trip in the panel).
function JsonParamControl({ name, value, onChange }) {
  const [text, setText] = useState(() => (value === undefined ? '' : JSON.stringify(value, null, 2)));
  const [error, setError] = useState(null);
  return (
    <>
      <textarea
        className="agents-graph-parameters__json"
        aria-label={name}
        rows={4}
        placeholder='e.g. ["a", "b"]'
        value={text}
        onChange={(event) => {
          const nextText = event.target.value;
          setText(nextText);
          if (nextText.trim() === '') {
            setError(null);
            onChange(undefined);
            return;
          }
          try {
            const parsed = JSON.parse(nextText);
            setError(null);
            onChange(parsed);
          } catch {
            setError('Invalid JSON -- last valid value is still what Run will send.');
          }
        }}
      />
      {error ? <p className="agents-graph-editor__error">{error}</p> : null}
    </>
  );
}

function ParameterControl({ name, def, value, onChange }) {
  if (Array.isArray(def?.enum) && def.enum.length) {
    return (
      <Select
        value={value === undefined ? '' : String(value)}
        onChange={(next) => onChange(next === '' ? undefined : next)}
        options={def.enum.map((option) => ({ value: String(option), label: String(option) }))}
        placeholder="Select…"
        ariaLabel={name}
        className="agents-graph-parameters__input"
      />
    );
  }
  if (def?.type === 'array' || def?.type === 'object') {
    return <JsonParamControl name={name} value={value} onChange={onChange} />;
  }
  if (def?.type === 'boolean') {
    return (
      <input
        type="checkbox"
        className="agents-graph-parameters__checkbox"
        checked={value === true}
        onChange={(event) => onChange(event.target.checked)}
      />
    );
  }
  if (def?.type === 'number' || def?.type === 'integer') {
    return (
      <input
        type="number"
        className="agents-graph-parameters__input"
        value={parameterControlValue(value)}
        step={def.type === 'integer' ? 1 : 'any'}
        onChange={(event) => onChange(event.target.value === '' ? undefined : Number(event.target.value))}
      />
    );
  }
  return (
    <input
      type="text"
      className="agents-graph-parameters__input"
      value={parameterControlValue(value)}
      placeholder={def?.type ? `(${def.type})` : undefined}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

// Every recorded execution of this routine, each row showing what actually
// happened -- success/failed (from that run's own run_meta, the same
// per-node duration/tokens/error data the canvas cards and NodeInspector
// read), wall-clock duration (from the run summary's own started_at/
// last_checkpoint_at -- no extra fetch needed for that part), total tokens,
// and when it ran. `viewedIndex`/`onSelectIndex` are owned by GraphCanvas's
// run-nav (the same state the toolbar's back/forward pill drives), so
// clicking a row here, clicking back/forward, and this list's highlighted
// row all stay in lockstep -- one selection, three ways to move it.
function RoutineRunsPanel({ agentId, onClose, runsList, viewedIndex, onSelectIndex }) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const agentsUrl = getApiUrl('agentProfiles');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    dispatch(fetchRoutineRuns({ agentsUrl, agentId }))
      .unwrap()
      .catch((fetchError) => setError(fetchError.message || 'Failed to load runs'))
      .finally(() => setLoading(false));
  }, [agentId, agentsUrl, dispatch]);

  useEffect(() => {
    if (!runsList) load();
    // Only auto-load the first time this agent's runs are uncached; a
    // manual refresh re-fetches explicitly via `load`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  // Status/duration/tokens need each run's own run_meta, not just the list
  // summary -- backfilled into the exact same runStateByThreadId cache the
  // back/forward pill itself reads, so a run already browsed to needs no
  // second fetch here, and jumping to a row this panel already resolved
  // needs none there either.
  const runStateByThreadId = useSelector((state) => state.uiData.agents.runStateByThreadId);
  useEffect(() => {
    (runsList || []).forEach((run) => {
      if (!runStateByThreadId[run.thread_id]) {
        dispatch(fetchRunState({ agentsUrl, agentId, threadId: run.thread_id }));
      }
    });
  }, [runsList, runStateByThreadId, agentsUrl, agentId, dispatch]);

  return (
    <aside className="agents-graph-editor agents-graph-runs">
      <button
        type="button"
        className="agents-graph-editor__collapse"
        onClick={onClose}
        title="Collapse"
        aria-label="Collapse run history"
      >
        <ChevronRightSmall />
      </button>
      <header className="agents-graph-editor__head">
        <h2>Runs</h2>
        <button type="button" className="repo-btn repo-btn--ghost" onClick={load} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </header>
      {error ? <p className="agents-graph-editor__error">{error}</p> : null}
      {!error && runsList && runsList.length === 0 && !loading ? (
        <p className="agents-graph-runs__empty">No runs recorded yet.</p>
      ) : null}
      <ul className="agents-graph-runs__list">
        {(runsList || []).map((run, index) => {
          const runState = runStateByThreadId[run.thread_id];
          const entries = runState?.run_meta ? Object.values(runState.run_meta).flat() : null;
          const failed = entries?.some((entry) => entry.error);
          const tokens = entries?.reduce((sum, entry) => sum + (entry.tokens?.total_tokens || 0), 0);
          const status = !entries ? 'pending' : failed ? 'failed' : 'success';
          const durationSec = run.started_at && run.last_checkpoint_at
            ? Math.max(0, (new Date(run.last_checkpoint_at) - new Date(run.started_at)) / 1000)
            : null;
          const datetime = run.last_checkpoint_at ? new Date(run.last_checkpoint_at).toLocaleString() : null;
          const isSelected = index === viewedIndex;
          return (
            <li key={run.thread_id}>
              <button
                type="button"
                className={`agents-graph-runs__item${isSelected ? ' is-selected' : ''}`}
                onClick={() => onSelectIndex(index)}
              >
                <div className="agents-graph-runs__item-head">
                  <span className={`agents-graph-runs__status agents-graph-runs__status--${status}`}>
                    {status === 'pending' ? 'Loading…' : status}
                  </span>
                  <span className="agents-graph-runs__age">{formatAge(run.last_checkpoint_at) || '—'}</span>
                </div>
                {datetime ? <div className="agents-graph-runs__datetime">{datetime}</div> : null}
                <div className="agents-graph-runs__item-meta">
                  {durationSec !== null ? <span>{durationSec.toFixed(1)}s</span> : null}
                  {typeof tokens === 'number' ? <span>{tokens} tok</span> : null}
                  <span>{run.checkpoint_count} step{run.checkpoint_count === 1 ? '' : 's'}</span>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}

function RoutineParametersPanel({ inputSchema, values, onChange, onClose }) {
  const properties = inputSchema?.properties || {};
  const required = new Set(inputSchema?.required || []);
  const names = Object.keys(properties);

  const setValue = (name, raw) => {
    onChange((current) => {
      if (raw === undefined || raw === '') {
        if (!(name in (current || {}))) return current;
        const next = { ...current };
        delete next[name];
        return next;
      }
      return { ...current, [name]: raw };
    });
  };

  return (
    <aside className="agents-graph-editor agents-graph-parameters">
      <button
        type="button"
        className="agents-graph-editor__collapse"
        onClick={onClose}
        title="Collapse"
        aria-label="Collapse parameters"
      >
        <ChevronRightSmall />
      </button>
      <header className="agents-graph-editor__head">
        <h2>Parameters</h2>
        {names.length ? (
          <button type="button" className="repo-btn repo-btn--ghost" onClick={() => onChange({})}>Clear</button>
        ) : null}
      </header>
      {names.length === 0 ? (
        <p className="agents-graph-parameters__empty">
          This routine has no declared input_schema. Add one via the JSON editor -- a JSON Schema object with `properties` (and optionally `required`) -- to collect values here before Run.
        </p>
      ) : (
        <div className="agents-graph-parameters__list">
          {names.map((name) => {
            const def = properties[name] || {};
            const isRequired = required.has(name);
            return (
              <label key={name} className={`agents-graph-node__field${isRequired ? ' agents-graph-node__field--required' : ''}`}>
                <span className="agents-graph-node__field-label">
                  {name}
                  {isRequired ? <span className="agents-graph-node__required" title="Required to run this routine">Required</span> : null}
                </span>
                {def.description ? <span className="agents-graph-parameters__hint">{def.description}</span> : null}
                <ParameterControl name={name} def={def} value={values?.[name]} onChange={(next) => setValue(name, next)} />
              </label>
            );
          })}
        </div>
      )}
    </aside>
  );
}

