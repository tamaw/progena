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
// Reuse the Workspace canvas chrome (dock, minimap, controls, node card) so both
// canvases share one source of truth. Agent-domain deltas live in chrome-frame.css.
import '../../styles/workspace.css';
import '../../styles/nodes.css';
import hljs from 'highlight.js/lib/common';

const graphFitViewOptions = { padding: 0.18 };
const graphDefaultEdgeOptions = {
  type: 'editable',
  animated: true,
};

// Workflow step kinds offered in the canvas dock (drag onto the canvas or click
// to add). Mirrors the backend VALID_NODE_KINDS executable schema.
const GRAPH_NODE_KINDS = [
  { kind: 'start', label: 'Start', icon: '▶' },
  { kind: 'ask', label: 'Ask', icon: '?' },
  { kind: 'tool', label: 'Tool', icon: '⚙' },
  { kind: 'decision', label: 'Decision', icon: '⑃' },
  { kind: 'end', label: 'End', icon: '■' },
];

// Accent per kind (kept in sync with the .agents-graph-node--* CSS and used by
// the minimap so steps read at a glance).
const GRAPH_KIND_COLORS = {
  start: '#2f9e6f',
  ask: '#2f6ff6',
  tool: '#b06ef6',
  decision: '#e0a23a',
  end: '#6b7280',
};

const GRAPH_KIND_ICONS = Object.fromEntries(GRAPH_NODE_KINDS.map((item) => [item.kind, item.icon]));

function minimapNodeColor(node) {
  return GRAPH_KIND_COLORS[node?.data?.kind] || '#8b949e';
}

// Edit callbacks shared with the custom node/edge renderers (avoids threading
// mutable refs through node data, which React forbids reading during render).
const GraphEditContext = createContext({ updateNodeData: () => {}, updateEdgeLabel: () => {} });

// Inline-editable workflow step, styled like the Workspace canvas nodes: a title
// bar (type-icon chip + editable label + kind badge) over a body whose content is
// editable per kind. Edits are autosaved by the canvas.
function EditableGraphNode({ id, data, selected }) {
  const { updateNodeData } = useContext(GraphEditContext);
  const hasTarget = data.kind !== 'start';
  const hasSource = data.kind !== 'end';
  const hasBody = data.kind === 'ask' || data.kind === 'decision' || data.kind === 'tool';
  return (
    <div
      className={`workspace-node agents-graph-node agents-graph-node--${data.kind} ${selected ? 'selected' : ''}`}
      style={{ '--node-color': GRAPH_KIND_COLORS[data.kind] || data.color }}
    >
      {hasTarget ? <Handle type="target" position={Position.Left} className="workspace-handle" /> : null}
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
      {hasBody ? (
        <div className="node-body node-body-compact">
          {data.kind === 'tool' ? (
            <input
              className="nodrag agents-graph-node__toolinput"
              value={data.tool || ''}
              placeholder="tool name"
              onChange={(event) => updateNodeData(id, { tool: event.target.value })}
            />
          ) : (
            <textarea
              className="nodrag nowheel agents-graph-node__prompt"
              value={data.prompt || ''}
              placeholder={data.kind === 'decision' ? 'Decision question…' : 'Prompt…'}
              onChange={(event) => updateNodeData(id, { prompt: event.target.value })}
            />
          )}
        </div>
      ) : null}
      {hasSource ? <Handle type="source" position={Position.Right} className="workspace-handle" /> : null}
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

function seedNodes(graph, agent) {
  return (graph.nodes || []).map((node, index) => ({
    id: node.id,
    type: 'agentGraph',
    position: { x: node.x ?? index * 240, y: node.y ?? 80 },
    data: {
      kind: node.kind,
      label: node.label ?? node.id,
      prompt: node.prompt,
      tool: node.tool,
      detail: node.detail,
      color: agent.color,
    },
  }));
}

function seedEdges(graph, agent) {
  return (graph.edges || []).map((edge, index) => {
    const [source, target, label] = edge;
    return {
      id: `${source}-${target}-${index}`,
      source,
      target,
      label,
      type: 'editable',
      animated: true,
      style: { stroke: agent.color },
      markerEnd: { type: MarkerType.ArrowClosed, color: agent.color },
    };
  });
}

// Serialize the live React Flow state back into the backend graph schema,
// stripping the injected callbacks and keeping positions for round-tripping.
function serializeGraph(nodes, edges, meta) {
  return {
    status: meta.status || 'Draft',
    summary: meta.summary || '',
    nodes: nodes.map((node) => {
      const data = node.data || {};
      const out = {
        id: node.id,
        kind: data.kind,
        label: data.label,
        x: Math.round(node.position?.x ?? 0),
        y: Math.round(node.position?.y ?? 0),
      };
      if (data.prompt) out.prompt = data.prompt;
      if (data.tool) out.tool = data.tool;
      if (data.detail) out.detail = data.detail;
      return out;
    }),
    edges: edges.map((edge) => (edge.label ? [edge.source, edge.target, String(edge.label)] : [edge.source, edge.target])),
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
  const [meta, setMeta] = useState({ status: graph.status || 'Draft', summary: graph.summary || '' });
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
      color: agent.color,
    };
    if (kind === 'ask' || kind === 'decision') data.prompt = '';
    if (kind === 'tool') data.tool = '';
    setNodes((current) => [...current, { id, type: 'agentGraph', position: point, data }]);
  }, [agent.color, reactFlow, setNodes]);

  const onConnect = useCallback((params) => {
    const sourceNode = nodesRef.current.find((node) => node.id === params.source);
    const label = sourceNode?.data?.kind === 'decision' ? 'branch' : undefined;
    setEdges((current) => addEdge({
      ...params,
      type: 'editable',
      animated: true,
      label,
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
    const nextMeta = { status: parsed.status || 'Draft', summary: parsed.summary || '' };
    setMeta(nextMeta);
    const nextNodes = seedNodes(parsed, agent);
    const nextEdges = seedEdges(parsed, agent);
    setNodes(nextNodes);
    setEdges(nextEdges);
    const payload = { status: nextMeta.status, summary: nextMeta.summary, nodes: parsed.nodes, edges: parsed.edges };
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
            className="workspace-canvas agents-graph-canvas"
            ref={wrapperRef}
            onDrop={onDrop}
            onDragOver={onDragOver}
          >
            <div className="agents-graph-floating">
              <span>{agent.name}</span>
              <span>{nodes.length} nodes</span>
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

// Collapsed right-edge tools dock for the sequences canvas. Mirrors the left
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
    <div className="workspace-dock agents-graph-dock">
      <div className="workspace-dock-shell">
        {GRAPH_NODE_KINDS.map((item) => (
          <button
            key={item.kind}
            type="button"
            className={`workspace-dock-item agents-graph-dock__item--${item.kind}`}
            draggable
            onDragStart={(event) => onDragStart(event, item.kind)}
            onClick={() => onAdd(item.kind)}
          >
            <span className={`workspace-dock-icon ${item.kind}`}>{item.icon}</span>
            <span className="workspace-dock-tooltip">{item.label}</span>
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
