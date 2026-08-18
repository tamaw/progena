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
import { useSelector } from 'react-redux';
import Select from '../common/Select';
import { selectAgentProfiles } from '../../store/uiDataSlice';
import { DeleteActionIcon, DetailHeaderAction, DetailHeaderActions, OpenActionIcon, PlayActionIcon } from '../layout/DetailHeader';
// Reuse the shared canvas chrome (dock icons, JSON-editor panel/side-dock) so
// this still shares CSS with the Workflows canvas where the visuals overlap.
// The Routines graph itself is NOT a free canvas though (see rationale below),
// so it does not use React Flow at all.
import '../../styles/canvas.css';
import '../../styles/nodes.css';
import hljs from 'highlight.js/lib/common';

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
    eval: { expression: '', inputs: {} },
    condition: { expression: '', inputs: {} },
    switch: { expression: '', inputs: {} },
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

// Edit callbacks shared with the card/branch renderers (avoids threading
// mutable refs through node data, which React forbids reading during render).
const GraphEditContext = createContext(null);

// The required fields the inspected node is still missing. A context rather
// than a prop so the marker reaches every ConfigField without rethreading each
// of NodeConfigBody's per-kind call sites.
const EMPTY_MISSING = new Set();
const MissingFieldsContext = createContext(EMPTY_MISSING);

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

function InputsField({ id, config, updateNodeConfig }) {
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">Inputs (name: data.path)</span>
      <textarea
        className="agents-graph-node__prompt"
        value={inputsToText(config?.inputs)}
        placeholder="host: parse_json.host"
        onChange={(event) => updateNodeConfig(id, { inputs: textToInputs(event.target.value) })}
      />
    </label>
  );
}

// `classify`/`prompt`/`agent` nodes' response_schema is plain JSON Schema, but
// authoring one by hand for a flat set of output fields is needless ceremony.
// Edited as one "name: type[, description]" per line -- the same "name: value"
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
    return def?.description ? `${name}: ${typeText}, ${def.description}` : `${name}: ${typeText}`;
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
    // enum(...) may itself contain commas, so its closing paren -- not the
    // first comma in the line -- is what ends the type token.
    const enumMatch = rest.match(/^(enum\([^)]*\))\s*,?\s*(.*)$/i);
    const commaIndex = enumMatch ? -1 : rest.indexOf(',');
    const typeRaw = enumMatch ? enumMatch[1] : (commaIndex === -1 ? rest : rest.slice(0, commaIndex));
    const description = enumMatch ? enumMatch[2].trim() : (commaIndex === -1 ? '' : rest.slice(commaIndex + 1).trim());
    const def = parseSchemaFieldType(typeRaw || 'string');
    properties[name] = description ? { ...def, description } : def;
    required.push(name);
  });
  return { type: 'object', properties, required, additionalProperties: false };
}

function ResponseSchemaField({ id, config, field, label, updateNodeConfig }) {
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">{label}</span>
      <textarea
        className="agents-graph-node__prompt"
        value={schemaFieldsToText(config?.[field])}
        placeholder={'category: enum(billing, technical, other)\nisUrgent: boolean\nsummary: string, one sentence explaining why'}
        onChange={(event) => updateNodeConfig(id, { [field]: textToSchemaFields(event.target.value) })}
      />
    </label>
  );
}


function KeyValueMapField({ id, config, field, label, placeholder, updateNodeConfig }) {
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">{label}</span>
      <textarea
        className="agents-graph-node__prompt"
        value={inputsToText(config?.[field])}
        placeholder={placeholder}
        onChange={(event) => updateNodeConfig(id, { [field]: textToInputs(event.target.value) })}
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
// config shape (prompt_template/system_prompt, expression/inputs, rubric_prompt/
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
      <>
        <ConfigField id={id} config={config} field="expression" label="Expression" placeholder="Restricted Python expression…" multiline updateNodeConfig={updateNodeConfig} />
        <InputsField id={id} config={config} updateNodeConfig={updateNodeConfig} />
      </>
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
        placeholder="route on data.<key>"
        onChange={(event) => onChangeStateKey(id, event.target.value)}
      />
      <button type="button" className="agents-graph-node__unsplit" onClick={() => onRemoveBranching(id)} title="Remove branching">
        Remove branching
      </button>
    </div>
  );
}

// Compact map card. Editing lives in NodeInspector so the chain stays readable.
function StepCard({ node }) {
  const { selectNode, selectedNodeId } = useContext(GraphEditContext);
  // A step can arrive incomplete from the JSON editor or the runtime, not just
  // from the picker, so the chain has to say which one is holding the save.
  const incomplete = !isNodeComplete(node);
  return (
    <div
      className={`canvas-node agents-graph-node agents-graph-node--${node.kind} routine-step-card${selectedNodeId === node.id ? ' selected' : ''}${incomplete ? ' routine-step-card--incomplete' : ''}`}
      data-node-id={node.id}
      style={{ '--node-color': GRAPH_KIND_COLORS[node.kind] || '#8b949e' }}
      role="button"
      tabIndex={0}
      aria-label={`${node.label || node.id} ${GRAPH_KIND_LABELS[node.kind] || node.kind} step${incomplete ? ', incomplete' : ''}`}
      onClick={(event) => selectNode(node.id, event)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          selectNode(node.id, event);
        }
      }}
    >
      <div className="node-title-bar routine-step-card__title">
        <span className="node-type-icon"><KindIcon kind={node.kind} /></span>
        <span className="routine-step-card__copy">
          <strong className="routine-step-card__label">{node.label || node.id}</strong>
        </span>
        {incomplete ? <span className="routine-step-card__incomplete" title="Required fields are empty">!</span> : null}
      </div>
    </div>
  );
}

// Full configuration for the selected node. It floats inside the canvas and
// is deliberately separate from the chain so large prompts never resize it.
function NodeInspector({ node, routerEdge, onClose, onDismiss, isNew, style, panelRef }) {
  const { updateNode, deleteNode } = useContext(GraphEditContext);
  const [isEditingLabel, setIsEditingLabel] = useState(false);
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
  }

  useEffect(() => {
    if (!isEditingLabel) return undefined;
    labelInputRef.current?.focus();
    labelInputRef.current?.select();
    return undefined;
  }, [isEditingLabel]);

  if (!node) return null;
  const handleConfig = (nodeId, patch) => updateNode(nodeId, { config: { ...node.config, ...patch } });
  const unmet = missingRequirements(node);
  const missing = unmet.flat();
  const missingSet = missing.length ? new Set(missing) : EMPTY_MISSING;
  // Held open until the step can actually be saved. Closing here would strand
  // an unsaveable step somewhere in the chain with the whole graph withheld
  // behind it, so the only ways out are filling the fields or deleting it.
  const locked = unmet.length > 0;
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
      style={{ ...style, '--node-color': GRAPH_KIND_COLORS[node.kind] || '#8b949e' }}
      aria-label={`${node.label || node.id} details`}
    >
      <header className="routine-node-inspector__head">
        <span className="node-type-icon"><KindIcon kind={node.kind} /></span>
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
          <span className="routine-node-inspector__kind">{GRAPH_KIND_LABELS[node.kind] || node.kind}</span>
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
        {unmet.length ? (
          <p className="routine-node-inspector__required-hint" role="status">
            {unmet.length === 1
              ? 'One field is required before this step can be saved.'
              : `${unmet.length} fields are required before this step can be saved.`}
            {' '}
            {isNew ? 'Fill it in, or close to discard this step.' : 'Fill it in, or close to discard your changes.'}
          </p>
        ) : null}
        <NodeConfigBody
          id={node.id}
          kind={node.kind}
          config={node.config}
          updateNodeConfig={handleConfig}
          replaceNodeConfig={(config) => updateNode(node.id, { config })}
        />
        <RoutingFooter id={node.id} kind={node.kind} routerEdge={routerEdge} />
      </div>
      </MissingFieldsContext.Provider>
      <footer className="routine-node-inspector__foot">
        <button type="button" className="agents-graph-node__delete" onClick={() => { deleteNode(node.id); onDismiss(); }}>
          Delete step
        </button>
      </footer>
    </aside>
  );
}

// Fixed Start/End chrome. Never a schema node -- Start is purely "the chain
// begins here" (points at `entry`), End is purely "this path terminates
// here" (a node with no outgoing edge/branch). Rendered once per terminus,
// so a branching routine can show multiple Ends -- that's real, not a bug.
function ChainCap({ kind }) {
  return <div className={`routine-cap routine-cap--${kind}`}>{kind === 'start' ? 'Start' : 'End'}</div>;
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
  const { insertAt, onJoinBranches } = useContext(GraphEditContext);
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
      } else visit(edge.to);
    });
  };
  visit(entry);
  return reachable;
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
  } else {
    next = buildChainTree(edgeTargetIds(edge)[0], nodesById, edgeBySource, visiting, rendered, stopAt);
  }
  visiting.delete(nodeId);
  return { type: 'node', id: nodeId, node, next, fork };
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
        out.push(<LoopBlock key={`loop-${current.id}`} targetLabel={current.label} body={body} />);
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
    slot = { sourceId: current.id, kind: 'plain' };
    current = current.next;
  }
  return out;
}

// Retry loop: boxes the cycle's body -- from the node it loops back to,
// through everything up to the back-edge -- like ForeachBlock does for a
// fan-out, instead of leaving just a floating "loops back to" chip.
function LoopBlock({ targetLabel, body }) {
  const { settings } = useContext(GraphEditContext);
  const recursionLimit = settings?.recursion_limit;
  const maxRetries = settings?.retry?.max_retries;
  return (
    <section className="routine-loop" aria-label={`Retry loop back to ${targetLabel}`}>
      <div className="routine-loop__head">
        <span className="routine-loop__eyebrow">↺ Retry loop</span>
        {recursionLimit ? <span className="routine-loop__badge">Recursion limit {recursionLimit}</span> : null}
        {maxRetries ? <span className="routine-loop__badge">retries {maxRetries}x per step</span> : null}
      </div>
      <div className="routine-loop__body">{body}</div>
      <div className="routine-loop__back">↺ back to “{targetLabel}”</div>
    </section>
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
  } = useContext(GraphEditContext);
  const [collapsed, setCollapsed] = useState(() => new Set());
  const { sourceId, edge, branches, defaultTree, joinTree, editable = true, fixedBranches = false, unconditional = false } = fork;
  // A fixed fork's arms are dictated by the node kind, so it grows no lanes
  // and its headers are labels rather than editable chips. It can still be
  // joined -- that's about what happens *after* the fork, not its shape.
  const canGrowLanes = editable && !fixedBranches;
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
                  readOnly={!editable || fixedBranches}
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
  const { deleteNode } = useContext(GraphEditContext);
  if (!nodes.length) return null;
  return (
    <div className="routine-unreachable">
      <div className="routine-unreachable__head">Unreachable steps ({nodes.length})</div>
      <div className="routine-unreachable__list">
        {nodes.map((node) => (
          <div key={node.id} className="routine-unreachable__item">
            <span className={`canvas-dock-icon ${node.kind}`}><KindIcon kind={node.kind} /></span>
            <span>{node.label || node.id}</span>
            <button type="button" onClick={() => deleteNode(node.id)} title="Delete" aria-label="Delete">×</button>
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

function GraphCanvas({ agent, graph, onPersist, onRun, runResult, onShowProfile, onDelete, jsonOpen, onOpenJson, onCloseJson }) {
  const persistRef = useRef(onPersist);
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
    const outgoing = edgesRef.current.find((edge) => isFlowEdge(edge) && edgeSourceIds(edge).includes(id));
    const singleTarget = outgoing?.to && typeof outgoing.to === 'string'
      ? outgoing.to
      : null;
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
        if (edgeSourceIds(edge).includes(id) && !edge.router && edge.level !== 'fan_in') return; // the deleted node's own outgoing edge
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
    const id = uniqueNodeId(kind, nodesRef.current);
    const added = [{ ...makeNode(kind), id }];
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
    }
    // Whatever the new step ends on is the lane's terminal from here: a
    // fan-out's companion stop, a condition's two arms, or the step itself.
    let terminalIds = [id];
    if (kind === 'foreach') {
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
    pendingSelectRef.current = id;
    setNodes((current) => [...current, ...added]);
    setEdges(nextEdges);
  }, []);

  const onChangeStateKey = useCallback((sourceId, value) => {
    setEdges((current) => current.map((edge) => (
      edge.from === sourceId && edge.router ? { ...edge, state_key: value } : edge
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

  const editContext = useMemo(() => ({
    updateNode,
    deleteNode,
    insertAt,
    onChangeStateKey,
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
    edges,
  }), [updateNode, deleteNode, insertAt, onChangeStateKey, removeBranching, onAddBranch, onJoinBranches, selectNode, selectedNodeId, onAddDefault, onRemoveBranch, onRemoveDefault, onRenameBranch, meta.settings, edges]);

  // Debounced autosave: serialize the working graph and PATCH it. The first
  // run records the baseline so simply opening the tab does not trigger a save.
  // A graph carrying a node with unfilled required config is withheld rather
  // than sent: the backend would reject it, and an author midway through
  // filling in a step they just inserted has not made a mistake worth a red
  // banner. `lastSavedRef` is deliberately left alone so the moment the last
  // field lands the signature still differs and the save goes out on its own.
  const incomplete = useMemo(() => incompleteNodes(nodes), [nodes]);
  const lastSavedRef = useRef(null);
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
    if (incomplete.length) {
      setSaveError(null);
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
  const selectedNode = nodes.find((node) => node.id === selectedNodeId) || null;
  const selectedRouterEdge = selectedNode
    ? edges.find((edge) => edge.from === selectedNode.id && edge.router)
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
              {saveState !== 'idle' ? (
                <span className={`agents-graph-save agents-graph-save--${saveState}`} role="status">
                  {saveState === 'saving' ? 'Saving…'
                    : saveState === 'saved' ? 'Saved'
                      : saveState === 'incomplete' ? `Not saved · ${incomplete.length} step${incomplete.length === 1 ? '' : 's'} incomplete`
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
              {runResult ? (
                <span
                  className={`agents-graph-run-result agents-graph-run-result--${runResult.status}`}
                  role="status"
                  aria-label={`Routine result: ${runResult.status}`}
                >
                  {runResult.status === 'error' ? `Simulated error after ${runResult.events} events` : `Result valid · ${runResult.events} events`}
                </span>
              ) : null}
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
                  tooltip="Run routine"
                  ariaLabel="Run routine"
                  onClick={() => onRun(workingGraph)}
                  disabled={validationErrors.length > 0}
                />
                {onDelete ? (
                  <DetailHeaderAction
                    icon={<DeleteActionIcon />}
                    label="Delete"
                    tooltip="Delete routine"
                    ariaLabel="Delete routine"
                    variant="danger"
                    onClick={onDelete}
                  />
                ) : null}
              </DetailHeaderActions>
            </div>
              <GraphEditContext.Provider value={editContext}>
              <div
                className="routine-canvas__scroll"
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
                <RoutineChain nodes={nodes} edges={edges} entry={meta.entry} />
              </div>
              {selectedNode && inspectorPosition ? (
                <NodeInspector
                  node={selectedNode}
                  routerEdge={selectedRouterEdge}
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
      ) : (
        <GraphSideDock onSelect={(id) => { if (id === 'json') onOpenJson(); }} />
      )}
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

const GRAPH_SIDE_ITEMS = [
  { id: 'json', label: 'JSON', title: 'Edit as JSON' },
];

function GraphSideDock({ onSelect }) {
  return (
    <aside className="agents-graph-side" aria-label="Graph tools">
      {GRAPH_SIDE_ITEMS.map((item) => (
        <button
          key={item.id}
          type="button"
          className="agents-graph-side__item"
          title={item.title}
          aria-label={item.title}
          onClick={() => onSelect(item.id)}
        >
          <ChevronLeftSmall />
          <span className="agents-graph-side__label">{item.label}</span>
        </button>
      ))}
    </aside>
  );
}

export function AgentGraphTab({ agent, graph, onPersist, onRun, runResult, onShowProfile, onDelete, jsonOpen, onOpenJson, onCloseJson }) {
  return (
    <GraphCanvas
      agent={agent}
      graph={graph}
      onPersist={onPersist}
      onRun={onRun}
      runResult={runResult}
      onShowProfile={onShowProfile}
      onDelete={onDelete}
      jsonOpen={jsonOpen}
      onOpenJson={onOpenJson}
      onCloseJson={onCloseJson}
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
