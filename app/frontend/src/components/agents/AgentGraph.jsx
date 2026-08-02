import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  addEdge,
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  getSmoothStepPath,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
// Reuse the shared canvas chrome (dock, minimap, controls, node card) so both
// canvases share one source of truth. Agent-domain deltas live in chrome-frame.css.
import '../../styles/canvas.css';
import '../../styles/nodes.css';
import hljs from 'highlight.js/lib/common';

const graphFitViewOptions = { padding: 0.18 };
const graphDefaultEdgeOptions = {
  type: 'editable',
  animated: true,
};

// Routine step kinds offered in the canvas dock (drag onto the canvas or click
// to add). Mirrors app.progenitor.routines.graph.VALID_ROUTINE_NODE_KINDS.
const GRAPH_NODE_KINDS = [
  { kind: 'prompt', label: 'Prompt', icon: '?' },
  { kind: 'code', label: 'Code', icon: '⚙' },
  { kind: 'eval', label: 'Eval', icon: '✓' },
  { kind: 'subagent', label: 'Subagent', icon: '⇢' },
  { kind: 'agent', label: 'Agent', icon: '◆' },
  { kind: 'noop', label: 'Noop', icon: '·' },
];

// Accent per kind (kept in sync with the .agents-graph-node--* CSS and used by
// the minimap so steps read at a glance).
const GRAPH_KIND_COLORS = {
  prompt: '#2f6ff6',
  code: '#b06ef6',
  eval: '#e0a23a',
  subagent: '#2f9e6f',
  agent: '#ef6c2a',
  noop: '#6b7280',
};

const GRAPH_KIND_ICONS = Object.fromEntries(GRAPH_NODE_KINDS.map((item) => [item.kind, item.icon]));

function minimapNodeColor(node) {
  return GRAPH_KIND_COLORS[node?.data?.kind] || '#8b949e';
}

// Edit callbacks shared with the custom node/edge renderers (avoids threading
// mutable refs through node data, which React forbids reading during render).
const GraphEditContext = createContext({ updateNodeData: () => {}, updateEdgeLabel: () => {} });

// One config field bound to `data.config[field]` (a plain string value).
function ConfigField({ id, config, field, label, placeholder, multiline, updateNodeConfig }) {
  const value = config?.[field] || '';
  const onChange = (event) => updateNodeConfig(id, { [field]: event.target.value });
  return (
    <label className="agents-graph-node__field">
      <span className="agents-graph-node__field-label">{label}</span>
      {multiline ? (
        <textarea className="nodrag nowheel agents-graph-node__prompt" value={value} placeholder={placeholder} onChange={onChange} />
      ) : (
        <input className="nodrag agents-graph-node__toolinput" value={value} placeholder={placeholder} onChange={onChange} />
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
        className="nodrag nowheel agents-graph-node__prompt"
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

// Optional per-node `state_equals` routing: giving a node a state key turns its
// outgoing edges into named branches (edge label = branch value) instead of a
// single plain edge. Kept on the node (not the edge) since one router config
// covers all of a node's outgoing edges — mirrors the compiled graph's edge
// shape, see app.progenitor.routines.graph.RoutineEdge.
function RoutingFields({ id, data, updateNodeData }) {
  return (
    <div className="agents-graph-node__routing">
      <input
        className="nodrag agents-graph-node__toolinput"
        value={data.routerStateKey || ''}
        placeholder="route on data.<key> (optional)"
        onChange={(event) => updateNodeData(id, { routerStateKey: event.target.value })}
      />
      {data.routerStateKey ? (
        <input
          className="nodrag agents-graph-node__toolinput"
          value={data.routerDefault || ''}
          placeholder="default target node id"
          onChange={(event) => updateNodeData(id, { routerDefault: event.target.value })}
        />
      ) : null}
    </div>
  );
}

// Inline-editable routine step, styled like the Workspace canvas nodes: a title
// bar (type-icon chip + editable label + kind badge) over a body whose content is
// editable per kind, plus an optional routing footer. Edits are autosaved.
function EditableGraphNode({ id, data, selected }) {
  const { updateNodeData } = useContext(GraphEditContext);
  const updateNodeConfig = useCallback((nodeId, patch) => {
    updateNodeData(nodeId, { config: { ...data.config, ...patch } });
  }, [updateNodeData, data.config]);
  return (
    <div
      className={`canvas-node agents-graph-node agents-graph-node--${data.kind} ${selected ? 'selected' : ''}`}
      style={{ '--node-color': GRAPH_KIND_COLORS[data.kind] || data.color }}
    >
      <Handle type="target" position={Position.Left} className="canvas-handle" />
      <div className="node-title-bar drag-handle">
        <span className="node-type-icon">{GRAPH_KIND_ICONS[data.kind] || '•'}</span>
        <input
          className="nodrag node-title agents-node-title"
          value={data.label || ''}
          placeholder="Label"
          onChange={(event) => updateNodeData(id, { label: event.target.value })}
        />
        <span className="node-status-badge agents-node-kind-badge">{data.kind}</span>
      </div>
      <div className="node-body node-body-compact">
        <NodeConfigBody id={id} kind={data.kind} config={data.config} updateNodeConfig={updateNodeConfig} />
        <RoutingFields id={id} data={data} updateNodeData={updateNodeData} />
      </div>
      <Handle type="source" position={Position.Right} className="canvas-handle" />
    </div>
  );
}

// Edge with a double-click-to-edit label. Decision branches and retry loops use
// the label to name the route.
function EditableGraphEdge({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, style, label,
}) {
  const { updateEdgeLabel } = useContext(GraphEditContext);
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  });
  const [editing, setEditing] = useState(false);
  const value = label ?? '';
  return (
    <>
      <BaseEdge id={id} path={edgePath} markerEnd={markerEnd} style={style} />
      <EdgeLabelRenderer>
        <div
          className="agents-graph-edge-label nodrag nopan"
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          onDoubleClick={() => setEditing(true)}
        >
          {editing ? (
            <input
              autoFocus
              className="nodrag"
              value={value}
              onChange={(event) => updateEdgeLabel(id, event.target.value)}
              onBlur={() => setEditing(false)}
              onKeyDown={(event) => { if (event.key === 'Enter') setEditing(false); }}
            />
          ) : (
            value ? <span>{value}</span> : <span className="agents-graph-edge-label__add">+ label</span>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

const agentGraphNodeTypes = { agentGraph: EditableGraphNode };
const agentGraphEdgeTypes = { editable: EditableGraphEdge };

// A router edge (`{from, router: 'state_equals', state_key, branches, default}`)
// configures all of one node's outgoing edges at once; pull that config out per
// source node id so seedNodes/seedEdges can each apply their half of it.
function deriveRouterConfig(graph) {
  const routerByNode = {};
  (graph.edges || []).forEach((edge) => {
    if (edge && edge.router) {
      routerByNode[edge.from] = { stateKey: edge.state_key || '', default: edge.default || '' };
    }
  });
  return routerByNode;
}

function seedNodes(graph, agent) {
  const routerByNode = deriveRouterConfig(graph);
  return (graph.nodes || []).map((node, index) => ({
    id: node.id,
    type: 'agentGraph',
    position: { x: node.x ?? index * 260, y: node.y ?? 80 },
    data: {
      kind: node.kind,
      label: node.label ?? node.id,
      config: node.config || {},
      routerStateKey: routerByNode[node.id]?.stateKey || '',
      routerDefault: routerByNode[node.id]?.default || '',
      color: agent.color,
    },
  }));
}

function seedEdges(graph, agent) {
  const edges = [];
  (graph.edges || []).forEach((edge, index) => {
    if (!edge) return;
    const style = { stroke: agent.color };
    const markerEnd = { type: MarkerType.ArrowClosed, color: agent.color };
    if (edge.router) {
      Object.entries(edge.branches || {}).forEach(([branchValue, target], branchIndex) => {
        edges.push({
          id: `${edge.from}-${target}-${index}-${branchIndex}`,
          source: edge.from,
          target,
          label: branchValue,
          type: 'editable',
          animated: true,
          style,
          markerEnd,
        });
      });
      return;
    }
    edges.push({
      id: `${edge.from}-${edge.to}-${index}`,
      source: edge.from,
      target: edge.to,
      type: 'editable',
      animated: true,
      style,
      markerEnd,
    });
  });
  return edges;
}

// Serialize the live React Flow state back into the backend graph schema,
// stripping the injected callbacks and keeping positions for round-tripping. A
// node with a `routerStateKey` collapses its outgoing edges into one router
// edge (branches keyed by edge label); everything else stays a plain edge.
function serializeGraph(nodes, edges, meta) {
  const outgoingBySource = new Map();
  edges.forEach((edge) => {
    const list = outgoingBySource.get(edge.source) || [];
    list.push(edge);
    outgoingBySource.set(edge.source, list);
  });

  const serializedEdges = [];
  nodes.forEach((node) => {
    const data = node.data || {};
    const outgoing = outgoingBySource.get(node.id) || [];
    if (!outgoing.length) return;
    if (data.routerStateKey) {
      const branches = {};
      outgoing.forEach((edge) => {
        const label = (edge.label || '').toString().trim();
        if (label) branches[label] = edge.target;
      });
      const routerEdge = { from: node.id, router: 'state_equals', state_key: data.routerStateKey, branches };
      if (data.routerDefault) routerEdge.default = data.routerDefault;
      serializedEdges.push(routerEdge);
    } else {
      outgoing.forEach((edge) => {
        serializedEdges.push({ from: node.id, to: edge.target });
      });
    }
  });

  return {
    status: meta.status || 'Draft',
    summary: meta.summary || '',
    entry: meta.entry || nodes[0]?.id || null,
    nodes: nodes.map((node) => {
      const data = node.data || {};
      return {
        id: node.id,
        kind: data.kind,
        label: data.label,
        output_key: node.id,
        config: data.config || {},
        x: Math.round(node.position?.x ?? 0),
        y: Math.round(node.position?.y ?? 0),
      };
    }),
    edges: serializedEdges,
  };
}

function uniqueNodeId(kind, nodes) {
  const ids = new Set(nodes.map((node) => node.id));
  if (!ids.has(kind)) return kind;
  let i = 1;
  while (ids.has(`${kind}-${i}`)) i += 1;
  return `${kind}-${i}`;
}

// The Sessions/Workspace canvas: an interactive React Flow editor with a bottom
// dock of step kinds, a minimap (right), zoom controls (bottom-left), and inline
// editable nodes/edges. Edits autosave (debounced) to the backend.
function GraphCanvas({ agent, graph, onPersist, onShowProfile, jsonOpen, onOpenJson, onCloseJson }) {
  const reactFlow = useReactFlow();
  const wrapperRef = useRef(null);
  const persistRef = useRef(onPersist);

  const [nodes, setNodes, onNodesChange] = useNodesState(seedNodes(graph, agent));
  const [edges, setEdges, onEdgesChange] = useEdgesState(seedEdges(graph, agent));
  const [meta, setMeta] = useState({
    status: graph.status || 'Draft',
    summary: graph.summary || '',
    entry: graph.entry || (graph.nodes || [])[0]?.id || null,
  });
  const [saveState, setSaveState] = useState('idle');
  const [saveError, setSaveError] = useState(null);

  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);

  const updateNodeData = useCallback((id, patch) => {
    setNodes((current) => current.map((node) => (node.id === id ? { ...node, data: { ...node.data, ...patch } } : node)));
  }, [setNodes]);

  const updateEdgeLabel = useCallback((id, label) => {
    setEdges((current) => current.map((edge) => (edge.id === id ? { ...edge, label } : edge)));
  }, [setEdges]);

  const editContext = useMemo(() => ({ updateNodeData, updateEdgeLabel }), [updateNodeData, updateEdgeLabel]);

  // Keep refs in sync outside of render so callbacks/effects see the latest
  // values (React forbids writing refs during render).
  useEffect(() => { persistRef.current = onPersist; }, [onPersist]);
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);

  const addNode = useCallback((kind, position) => {
    const id = uniqueNodeId(kind, nodesRef.current);
    const point = position || (() => {
      const rect = wrapperRef.current?.getBoundingClientRect();
      if (!rect) return { x: 0, y: 0 };
      return reactFlow.screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
    })();
    const data = {
      kind,
      label: kind.charAt(0).toUpperCase() + kind.slice(1),
      config: {},
      routerStateKey: '',
      routerDefault: '',
      color: agent.color,
    };
    setNodes((current) => [...current, { id, type: 'agentGraph', position: point, data }]);
  }, [agent.color, reactFlow, setNodes]);

  const onConnect = useCallback((params) => {
    setEdges((current) => addEdge({
      ...params,
      type: 'editable',
      animated: true,
      style: { stroke: agent.color },
      markerEnd: { type: MarkerType.ArrowClosed, color: agent.color },
    }, current));
  }, [agent.color, setEdges]);

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback((event) => {
    event.preventDefault();
    const kind = event.dataTransfer.getData('application/agentgraph');
    if (!kind) return;
    const position = reactFlow.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    addNode(kind, position);
  }, [addNode, reactFlow]);

  // Delete selected nodes/edges on Delete/Backspace, but never while editing a
  // node's text fields (so typing in a prompt doesn't remove the node).
  useEffect(() => {
    function handleKey(event) {
      const tag = event.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (event.key !== 'Delete' && event.key !== 'Backspace') return;
      const selectedNodeIds = nodesRef.current.filter((node) => node.selected).map((node) => node.id);
      const selectedEdgeIds = edgesRef.current.filter((edge) => edge.selected).map((edge) => edge.id);
      if (!selectedNodeIds.length && !selectedEdgeIds.length) return;
      if (selectedNodeIds.length) {
        const removed = new Set(selectedNodeIds);
        setNodes((current) => current.filter((node) => !removed.has(node.id)));
        setEdges((current) => current.filter((edge) => !removed.has(edge.source) && !removed.has(edge.target) && !selectedEdgeIds.includes(edge.id)));
      } else {
        const removed = new Set(selectedEdgeIds);
        setEdges((current) => current.filter((edge) => !removed.has(edge.id)));
      }
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [setNodes, setEdges]);

  // Debounced autosave: serialize the working graph and PATCH it. The first run
  // records the baseline so simply opening the tab does not trigger a save.
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
    const nextNodes = seedNodes(parsed, agent);
    const nextEdges = seedEdges(parsed, agent);
    setNodes(nextNodes);
    setEdges(nextEdges);
    const payload = {
      status: nextMeta.status,
      summary: nextMeta.summary,
      entry: nextMeta.entry,
      nodes: parsed.nodes,
      edges: parsed.edges,
    };
    await persistRef.current(payload);
    lastSavedRef.current = JSON.stringify(serializeGraph(nextNodes, nextEdges, nextMeta));
    setSaveError(null);
    setSaveState('saved');
  }, [agent, setNodes, setEdges]);

  return (
    <>
      <section className="agents-graph-panel" style={{ '--agent-color': agent.color }}>
        <div className="agents-graph-shell">
          <div
            className="canvas-surface agents-graph-canvas"
            ref={wrapperRef}
            onDrop={onDrop}
            onDragOver={onDragOver}
          >
            <div className="agents-graph-floating">
              <span>{agent.name}</span>
              <span>{nodes.length} nodes</span>
              <label className="agents-graph-entry">
                Entry
                <select
                  className="nodrag agents-graph-entry-select"
                  value={meta.entry || ''}
                  onChange={(event) => setMeta((current) => ({ ...current, entry: event.target.value }))}
                >
                  {nodes.map((node) => (
                    <option key={node.id} value={node.id}>{node.data?.label || node.id}</option>
                  ))}
                </select>
              </label>
              {saveState !== 'idle' ? (
                <span className={`agents-graph-save agents-graph-save--${saveState}`}>
                  {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : 'Save failed'}
                </span>
              ) : null}
              <button type="button" onClick={onShowProfile}>Details</button>
            </div>
            <GraphEditContext.Provider value={editContext}>
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                nodeTypes={agentGraphNodeTypes}
                edgeTypes={agentGraphEdgeTypes}
                defaultEdgeOptions={graphDefaultEdgeOptions}
                fitView
                fitViewOptions={graphFitViewOptions}
                minZoom={0.3}
                maxZoom={2}
                snapToGrid
                snapGrid={[16, 16]}
                nodesConnectable
                nodesDraggable
                elementsSelectable
                deleteKeyCode={null}
                multiSelectionKeyCode="Shift"
              >
                <Background gap={16} size={1} color="var(--color-border)" variant="dots" />
                <Controls showInteractive={false} />
                <MiniMap
                  style={{ height: 100, width: 150 }}
                  nodeColor={minimapNodeColor}
                  maskColor="rgba(0, 0, 0, 0.6)"
                  pannable
                  zoomable
                />
              </ReactFlow>
            </GraphEditContext.Provider>
            <GraphDock onAdd={addNode} />
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

// Bottom-center dock of step kinds. Drag an item onto the canvas, or click to add
// it at the canvas centre. Mirrors the Workspace dock.
function GraphDock({ onAdd }) {
  const onDragStart = (event, kind) => {
    event.dataTransfer.setData('application/agentgraph', kind);
    event.dataTransfer.effectAllowed = 'move';
  };
  return (
    <div className="canvas-dock agents-graph-dock">
      <div className="canvas-dock-shell">
        {GRAPH_NODE_KINDS.map((item) => (
          <button
            key={item.kind}
            type="button"
            className={`canvas-dock-item agents-graph-dock__item--${item.kind}`}
            draggable
            onDragStart={(event) => onDragStart(event, item.kind)}
            onClick={() => onAdd(item.kind)}
          >
            <span className={`canvas-dock-icon ${item.kind}`}>{item.icon}</span>
            <span className="canvas-dock-tooltip">{item.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function AgentGraphTab({ agent, graph, onPersist, onShowProfile, jsonOpen, onOpenJson, onCloseJson }) {
  return (
    <ReactFlowProvider>
      <GraphCanvas
        agent={agent}
        graph={graph}
        onPersist={onPersist}
        onShowProfile={onShowProfile}
        jsonOpen={jsonOpen}
        onOpenJson={onOpenJson}
        onCloseJson={onCloseJson}
      />
    </ReactFlowProvider>
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
