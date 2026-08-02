import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
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
  { kind: 'prompt', label: 'Prompt', icon: '?' },
  { kind: 'code', label: 'Code', icon: '⚙' },
  { kind: 'eval', label: 'Eval', icon: '✓' },
  { kind: 'subagent', label: 'Subagent', icon: '⇢' },
  { kind: 'agent', label: 'Agent', icon: '◆' },
  { kind: 'noop', label: 'Noop', icon: '·' },
];

// Accent per kind (kept in sync with the .agents-graph-node--* CSS).
const GRAPH_KIND_COLORS = {
  prompt: '#2f6ff6',
  code: '#b06ef6',
  eval: '#e0a23a',
  subagent: '#2f9e6f',
  agent: '#ef6c2a',
  noop: '#6b7280',
};

const GRAPH_KIND_ICONS = Object.fromEntries(GRAPH_NODE_KINDS.map((item) => [item.kind, item.icon]));

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
  return { id: '', kind, label: titleCase(kind), description: '', config: {} };
}

// Edit callbacks shared with the card/branch renderers (avoids threading
// mutable refs through node data, which React forbids reading during render).
const GraphEditContext = createContext(null);

// One config field bound to `data.config[field]` (a plain string value).
function ConfigField({ id, config, field, label, placeholder, multiline, updateNodeConfig }) {
  const value = config?.[field] || '';
  const onChange = (event) => updateNodeConfig(id, { [field]: event.target.value });
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">{label}</span>
      {multiline ? (
        <textarea className="agents-graph-node__prompt" value={value} placeholder={placeholder} onChange={onChange} />
      ) : (
        <input className="agents-graph-node__toolinput" value={value} placeholder={placeholder} onChange={onChange} />
      )}
    </label>
  );
}

// `code`/`eval` nodes resolve named inputs from dotted `data` paths before
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

// Kind-specific config fields, mirroring app.progenitor.routines.graph's per-kind
// config shape (prompt_template/system_prompt, expression/inputs/rubric_prompt,
// subagent_name/agent_name + prompt_template). `noop` has no config.
function NodeConfigBody({ id, kind, config, updateNodeConfig }) {
  if (kind === 'prompt') {
    return (
      <>
        <ConfigField id={id} config={config} field="prompt_template" label="Prompt" placeholder="Template… use {request} / {other_node} to reference state" multiline updateNodeConfig={updateNodeConfig} />
        <ConfigField id={id} config={config} field="system_prompt" label="System prompt (optional)" placeholder="Node-specific system prompt…" multiline updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'code' || kind === 'eval') {
    return (
      <>
        <ConfigField id={id} config={config} field="expression" label="Expression" placeholder="Restricted Python expression…" multiline updateNodeConfig={updateNodeConfig} />
        {kind === 'eval' ? (
          <ConfigField id={id} config={config} field="rubric_prompt" label="Rubric prompt (used if no expression)" placeholder="Ask the model a yes/no question…" multiline updateNodeConfig={updateNodeConfig} />
        ) : null}
        <InputsField id={id} config={config} updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'subagent') {
    return (
      <>
        <ConfigField id={id} config={config} field="subagent_name" label="Subagent name" placeholder="repo-setup-worker" updateNodeConfig={updateNodeConfig} />
        <ConfigField id={id} config={config} field="prompt_template" label="Prompt" placeholder="Template sent to the subagent…" multiline updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  if (kind === 'agent') {
    return (
      <>
        <ConfigField id={id} config={config} field="agent_name" label="Agent name" placeholder="repo-manager" updateNodeConfig={updateNodeConfig} />
        <ConfigField id={id} config={config} field="prompt_template" label="Prompt" placeholder="Template sent to the agent…" multiline updateNodeConfig={updateNodeConfig} />
      </>
    );
  }
  return null;
}

// A node becomes a router the moment it has branches; before that it's just a
// "+ Split into branches" footer action. Once it is one, the state key it
// routes on lives here (applies to all of that node's branches at once).
function RoutingFooter({ id, routerEdge }) {
  const { onChangeStateKey, onSplitIntoBranches, onRemoveBranching } = useContext(GraphEditContext);
  if (!routerEdge) {
    return (
      <button type="button" className="agents-graph-node__split" onClick={() => onSplitIntoBranches(id)}>
        + Split into branches
      </button>
    );
  }
  return (
    <div className="agents-graph-node__routing">
      <input
        className="agents-graph-node__toolinput"
        value={routerEdge.state_key || ''}
        placeholder="route on data.<key>"
        onChange={(event) => onChangeStateKey(id, event.target.value)}
      />
      <button type="button" className="agents-graph-node__unsplit" onClick={() => onRemoveBranching(id)} title="Remove branching">
        Remove branching
      </button>
    </div>
  );
}

// One rigid step card: title bar (kind icon + editable label + kind badge)
// over an editable body, plus the routing footer when it forks.
function StepCard({ node, routerEdge }) {
  const { updateNode, deleteNode } = useContext(GraphEditContext);
  const updateNodeConfig = useCallback((nodeId, patch) => {
    updateNode(nodeId, { config: { ...node.config, ...patch } });
  }, [updateNode, node.config]);
  return (
    <div
      className={`canvas-node agents-graph-node agents-graph-node--${node.kind} routine-step-card`}
      style={{ '--node-color': GRAPH_KIND_COLORS[node.kind] || '#8b949e' }}
    >
      <div className="node-title-bar">
        <span className="node-type-icon">{GRAPH_KIND_ICONS[node.kind] || '•'}</span>
        <input
          className="node-title agents-node-title"
          value={node.label || ''}
          placeholder="Label"
          onChange={(event) => updateNode(node.id, { label: event.target.value })}
        />
        <span className="node-status-badge agents-node-kind-badge">{node.kind}</span>
        <button
          type="button"
          className="agents-graph-node__delete"
          onClick={() => deleteNode(node.id)}
          title="Delete step"
          aria-label="Delete step"
        >
          ×
        </button>
      </div>
      <div className="node-body node-body-compact">
        <NodeConfigBody id={node.id} kind={node.kind} config={node.config} updateNodeConfig={updateNodeConfig} />
      </div>
      <RoutingFooter id={node.id} routerEdge={routerEdge} />
    </div>
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
  const { insertAt } = useContext(GraphEditContext);
  const [open, setOpen] = useState(false);
  return (
    <div className="routine-connector">
      <span className="routine-connector__line" aria-hidden="true" />
      <button
        type="button"
        className="routine-connector__btn"
        onClick={() => setOpen((current) => !current)}
        aria-label="Insert step here"
        aria-expanded={open}
      >
        +
      </button>
      {open ? (
        <div className="routine-connector__menu" role="menu">
          {GRAPH_NODE_KINDS.map((item) => (
            <button
              key={item.kind}
              type="button"
              role="menuitem"
              className="routine-connector__menu-item"
              onClick={() => { insertAt(slot, item.kind); setOpen(false); }}
            >
              <span className={`canvas-dock-icon ${item.kind}`}>{item.icon}</span>
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
function BranchHeader({ value, isDefault, onRename, onRemove }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  function commit() {
    setEditing(false);
    const trimmed = draft.trim();
    if (trimmed && trimmed !== value) onRename(trimmed);
    else setDraft(value);
  }
  return (
    <div className={`routine-fork__header ${isDefault ? 'routine-fork__header--default' : ''}`}>
      {isDefault ? (
        <span className="routine-fork__header-label">Default</span>
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
      <button type="button" className="routine-fork__header-remove" onClick={onRemove} title="Remove branch" aria-label="Remove branch">
        ×
      </button>
    </div>
  );
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
function buildChainTree(nodeId, nodesById, edgeBySource, visiting, rendered) {
  if (!nodeId) return null;
  const node = nodesById.get(nodeId);
  if (!node) return null;
  if (visiting.has(nodeId)) return { type: 'loop', id: nodeId, label: node.label || nodeId };
  if (rendered.has(nodeId)) return { type: 'rejoin', id: nodeId, label: node.label || nodeId };
  rendered.add(nodeId);
  visiting.add(nodeId);
  const edge = edgeBySource.get(nodeId);
  let next = null;
  let fork = null;
  if (edge && edge.router) {
    fork = {
      sourceId: nodeId,
      edge,
      branches: Object.entries(edge.branches || {}).map(([value, targetId]) => ({
        value,
        tree: buildChainTree(targetId, nodesById, edgeBySource, visiting, rendered),
      })),
      defaultTree: edge.default
        ? buildChainTree(edge.default, nodesById, edgeBySource, visiting, rendered)
        : null,
    };
  } else {
    next = buildChainTree(edge ? edge.to : null, nodesById, edgeBySource, visiting, rendered);
  }
  visiting.delete(nodeId);
  return { type: 'node', id: nodeId, node, next, fork };
}

// Renders an already-computed chain-tree run: a connector before each step,
// the step itself, and either the next connector, a fork, or a terminus.
// Pure -- safe to invoke from a React child any number of times.
function renderRun(tree, initialSlot) {
  const out = [];
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
      out.push(<ChainChip key={`loop-${current.id}`} kind="loop" label={`↺ loops back to “${current.label}”`} />);
      break;
    }
    if (current.type === 'rejoin') {
      out.push(<ChainChip key={`rejoin-${current.id}`} kind="rejoin" label={`↳ continues at “${current.label}”`} />);
      break;
    }
    out.push(<StepCard key={current.id} node={current.node} routerEdge={current.fork ? current.fork.edge : null} />);
    if (current.fork) {
      out.push(<ForkBlock key={`fork-${current.id}`} fork={current.fork} />);
      break;
    }
    slot = { sourceId: current.id, kind: 'plain' };
    current = current.next;
  }
  return out;
}

// A router node forks into one column per branch (+ optional Default), each
// an independently rigid chain -- no forced merge node, exactly like Azure
// Logic Apps' Condition/Switch actions.
function ForkBlock({ fork }) {
  const { onAddBranch, onAddDefault, onRemoveBranch, onRemoveDefault, onRenameBranch } = useContext(GraphEditContext);
  const { sourceId, edge, branches, defaultTree } = fork;
  return (
    <div className="routine-fork">
      <div className="routine-fork__row">
        {branches.map(({ value, tree }) => (
          <div key={value} className="routine-fork__column">
            <BranchHeader
              key={value}
              value={value}
              onRename={(next) => onRenameBranch(sourceId, value, next)}
              onRemove={() => onRemoveBranch(sourceId, value)}
            />
            {renderRun(tree, { sourceId, kind: 'branch', value })}
          </div>
        ))}
        {edge.default ? (
          <div className="routine-fork__column routine-fork__column--default">
            <BranchHeader value="default" isDefault onRemove={() => onRemoveDefault(sourceId)} />
            {renderRun(defaultTree, { sourceId, kind: 'default' })}
          </div>
        ) : (
          <div className="routine-fork__column routine-fork__column--empty">
            <button type="button" className="routine-fork__add-default" onClick={() => onAddDefault(sourceId)}>
              + Add default
            </button>
          </div>
        )}
        <div className="routine-fork__column routine-fork__column--add">
          <button type="button" className="routine-fork__add-branch" onClick={() => onAddBranch(sourceId)}>
            + Add branch
          </button>
        </div>
      </div>
    </div>
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
            <span className={`canvas-dock-icon ${node.kind}`}>{GRAPH_KIND_ICONS[node.kind] || '•'}</span>
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
  const edgeBySource = useMemo(
    () => new Map(edges.filter((edge) => edge && edge.from).map((edge) => [edge.from, edge])),
    [edges],
  );
  // Build the whole reachable tree (forks included) fully, up front, in one
  // pure pass -- see buildChainTree's comment for why this can't be deferred
  // into lazily-invoked React children.
  const rendered = new Set();
  const tree = buildChainTree(entry, nodesById, edgeBySource, new Set(), rendered);
  const body = renderRun(tree, { sourceId: null, kind: 'entry' });
  const unreachable = nodes.filter((node) => !rendered.has(node.id));
  return (
    <div className="routine-chain">
      <ChainCap kind="start" />
      {body}
      <UnreachableTray nodes={unreachable} />
    </div>
  );
}

// Serialize the working nodes/edges/meta into the backend graph document
// shape. `output_key` always mirrors `id` -- it isn't independently editable.
function serializeGraph(nodes, edges, meta) {
  return {
    status: meta.status || 'Draft',
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
  };
}

// The routine canvas: a rigid top-down chain of steps with a floating status
// bar and the same JSON-editor side panel the previous canvas had. Edits
// autosave (debounced) to the backend.
function GraphCanvas({ agent, graph, onPersist, onShowProfile, jsonOpen, onOpenJson, onCloseJson }) {
  const persistRef = useRef(onPersist);
  const [nodes, setNodes] = useState(() => (graph.nodes || []).map((node) => ({ ...node })));
  const [edges, setEdges] = useState(() => (graph.edges || []).map((edge) => ({ ...edge })));
  const [meta, setMeta] = useState({
    status: graph.status || 'Draft',
    summary: graph.summary || '',
    entry: graph.entry || (graph.nodes || [])[0]?.id || null,
  });
  const [saveState, setSaveState] = useState('idle');
  const [saveError, setSaveError] = useState(null);

  useEffect(() => { persistRef.current = onPersist; }, [onPersist]);

  // Refs mirror the latest state synchronously so insertAt/splitIntoBranches
  // can compute a consistent next state without nesting setState calls
  // inside other setState updaters.
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const metaRef = useRef(meta);
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);
  useEffect(() => { metaRef.current = meta; }, [meta]);

  const updateNode = useCallback((id, patch) => {
    setNodes((current) => current.map((node) => (node.id === id ? { ...node, ...patch } : node)));
  }, []);

  const deleteNode = useCallback((id) => {
    setNodes((current) => current.filter((node) => node.id !== id));
    const outgoing = edgesRef.current.find((edge) => edge.from === id);
    const singleTarget = outgoing && !outgoing.router ? outgoing.to : null;
    setEdges((current) => {
      const next = [];
      current.forEach((edge) => {
        if (edge.from === id) return; // the deleted node's own outgoing edge
        if (edge.router) {
          const branches = { ...edge.branches };
          let changed = false;
          Object.entries(branches).forEach(([value, target]) => {
            if (target !== id) return;
            changed = true;
            if (singleTarget) branches[value] = singleTarget;
            else delete branches[value];
          });
          let defaultTarget = edge.default;
          if (defaultTarget === id) {
            changed = true;
            defaultTarget = singleTarget || undefined;
          }
          if (!changed) { next.push(edge); return; }
          if (!Object.keys(branches).length && !defaultTarget) return; // router now empty, drop it
          next.push({ ...edge, branches, ...(defaultTarget ? { default: defaultTarget } : { default: undefined }) });
          return;
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

  const insertAt = useCallback((slot, kind) => {
    const id = uniqueNodeId(kind, nodesRef.current);
    const newNode = { ...makeNode(kind), id };
    let targetId = null;
    const nextEdges = edgesRef.current.map((edge) => (
      edge.branches ? { ...edge, branches: { ...edge.branches } } : { ...edge }
    ));
    if (slot.kind === 'entry') {
      targetId = metaRef.current.entry;
      setMeta((current) => ({ ...current, entry: id }));
    } else if (slot.kind === 'plain') {
      const existing = nextEdges.find((edge) => edge.from === slot.sourceId);
      if (existing) {
        targetId = existing.to;
        existing.to = id;
      } else {
        nextEdges.push({ from: slot.sourceId, to: id });
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
    if (targetId) nextEdges.push({ from: id, to: targetId });
    setNodes((current) => [...current, newNode]);
    setEdges(nextEdges);
  }, []);

  const onChangeStateKey = useCallback((sourceId, value) => {
    setEdges((current) => current.map((edge) => (
      edge.from === sourceId && edge.router ? { ...edge, state_key: value } : edge
    )));
  }, []);

  const splitIntoBranches = useCallback((sourceId) => {
    const stateKey = window.prompt('Route on which data key? (e.g. "gate" for {{ data.gate }})', sourceId);
    if (!stateKey) return;
    const existing = edgesRef.current.find((edge) => edge.from === sourceId);
    const carryOverTarget = existing && !existing.router ? existing.to : null;
    const branchId = uniqueNodeId('noop', nodesRef.current);
    const routerEdge = {
      from: sourceId,
      router: 'state_equals',
      state_key: stateKey,
      branches: { 'case-1': branchId },
      ...(carryOverTarget ? { default: carryOverTarget } : {}),
    };
    setEdges((current) => [...current.filter((edge) => edge.from !== sourceId), routerEdge]);
    setNodes((current) => [...current, { ...makeNode('noop'), id: branchId }]);
  }, []);

  const removeBranching = useCallback((sourceId) => {
    setEdges((current) => current.map((edge) => {
      if (edge.from !== sourceId || !edge.router) return edge;
      return edge.default ? { from: sourceId, to: edge.default } : null;
    }).filter(Boolean));
  }, []);

  const onAddBranch = useCallback((sourceId) => {
    const branchId = uniqueNodeId('noop', nodesRef.current);
    setEdges((current) => current.map((edge) => {
      if (edge.from !== sourceId || !edge.router) return edge;
      const value = uniqueBranchValue(edge.branches);
      return { ...edge, branches: { ...edge.branches, [value]: branchId } };
    }));
    setNodes((current) => [...current, { ...makeNode('noop'), id: branchId }]);
  }, []);

  const onAddDefault = useCallback((sourceId) => {
    const branchId = uniqueNodeId('noop', nodesRef.current);
    setEdges((current) => current.map((edge) => (
      edge.from === sourceId && edge.router ? { ...edge, default: branchId } : edge
    )));
    setNodes((current) => [...current, { ...makeNode('noop'), id: branchId }]);
  }, []);

  const onRemoveBranch = useCallback((sourceId, value) => {
    setEdges((current) => current.map((edge) => {
      if (edge.from !== sourceId || !edge.router) return edge;
      const branches = { ...edge.branches };
      delete branches[value];
      if (!Object.keys(branches).length && !edge.default) return null;
      return { ...edge, branches };
    }).filter(Boolean));
  }, []);

  const onRemoveDefault = useCallback((sourceId) => {
    setEdges((current) => current.map((edge) => {
      if (edge.from !== sourceId || !edge.router) return edge;
      const { default: _drop, ...rest } = edge;
      return rest;
    }));
  }, []);

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
    onSplitIntoBranches: splitIntoBranches,
    onRemoveBranching: removeBranching,
    onAddBranch,
    onAddDefault,
    onRemoveBranch,
    onRemoveDefault,
    onRenameBranch,
  }), [updateNode, deleteNode, insertAt, onChangeStateKey, splitIntoBranches, removeBranching, onAddBranch, onAddDefault, onRemoveBranch, onRemoveDefault, onRenameBranch]);

  // Debounced autosave: serialize the working graph and PATCH it. The first
  // run records the baseline so simply opening the tab does not trigger a save.
  const lastSavedRef = useRef(null);
  useEffect(() => {
    const payload = serializeGraph(nodes, edges, meta);
    const signature = JSON.stringify(payload);
    if (lastSavedRef.current === null) {
      lastSavedRef.current = signature;
      return undefined;
    }
    if (signature === lastSavedRef.current) return undefined;
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
  }, [nodes, edges, meta]);

  const workingGraph = useMemo(() => serializeGraph(nodes, edges, meta), [nodes, edges, meta]);

  const handleJsonApply = useCallback(async (parsed) => {
    const nextMeta = {
      status: parsed.status || 'Draft',
      summary: parsed.summary || '',
      entry: parsed.entry || (parsed.nodes || [])[0]?.id || null,
    };
    setMeta(nextMeta);
    setNodes((parsed.nodes || []).map((node) => ({ ...node })));
    setEdges((parsed.edges || []).map((edge) => ({ ...edge })));
    const payload = {
      status: nextMeta.status,
      summary: nextMeta.summary,
      entry: nextMeta.entry,
      nodes: parsed.nodes,
      edges: parsed.edges,
    };
    await persistRef.current(payload);
    lastSavedRef.current = JSON.stringify(payload);
    setSaveError(null);
    setSaveState('saved');
  }, []);

  return (
    <>
      <section className="agents-graph-panel" style={{ '--agent-color': agent.color }}>
        <div className="agents-graph-shell">
          <div className="canvas-surface agents-graph-canvas routine-canvas">
            <div className="agents-graph-floating">
              <span>{agent.name}</span>
              <span>{nodes.length} step{nodes.length === 1 ? '' : 's'}</span>
              {saveState !== 'idle' ? (
                <span className={`agents-graph-save agents-graph-save--${saveState}`}>
                  {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : 'Save failed'}
                </span>
              ) : null}
              <button type="button" onClick={onShowProfile}>Details</button>
            </div>
            <GraphEditContext.Provider value={editContext}>
              <div className="routine-canvas__scroll">
                <RoutineChain nodes={nodes} edges={edges} entry={meta.entry} />
              </div>
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
      <path d="m6.2 4.2 3.6 3.8-3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

// Collapsed right-edge tools dock for the routines canvas. Mirrors the left
// pane's collapse rail but on the right; each item expands its own panel. Add
// future tools by extending GRAPH_SIDE_ITEMS and handling their id in onSelect.
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

export function AgentGraphTab({ agent, graph, onPersist, onShowProfile, jsonOpen, onOpenJson, onCloseJson }) {
  return (
    <GraphCanvas
      agent={agent}
      graph={graph}
      onPersist={onPersist}
      onShowProfile={onShowProfile}
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
      summary: graph.summary || '',
      entry: graph.entry ?? null,
      nodes: graph.nodes || [],
      edges: graph.edges || [],
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
