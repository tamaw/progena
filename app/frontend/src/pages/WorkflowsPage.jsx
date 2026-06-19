import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  useOnViewportChange,
  ReactFlowProvider,
} from '@xyflow/react';

import '../styles/workspace.css';
import '../styles/nodes.css';
import WorkspaceSidebar from '../components/workspace/WorkspaceSidebar';
import WorkspaceDock from '../components/workspace/WorkspaceDock';
import NodeContextMenu from '../components/workspace/NodeContextMenu';
import {
  WorkspaceProvider,
  useWorkspaceActions,
  useWorkspaceGraph,
  createPersistedGraph,
} from '../context/WorkspaceContext';
import { WorkflowJsonDock, WorkflowJsonEditor } from '../components/workspace/WorkflowJsonPanel';

import PromptNode from '../components/workspace/nodes/PromptNode';
import CodeNode from '../components/workspace/nodes/CodeNode';
import WebFrameNode from '../components/workspace/nodes/WebFrameNode';
import LabelNode from '../components/workspace/nodes/LabelNode';
import ScheduleNode from '../components/workspace/nodes/ScheduleNode';
import EventNode from '../components/workspace/nodes/EventNode';
import GroupNode from '../components/workspace/nodes/GroupNode';
import AgentNode from '../components/workspace/nodes/AgentNode';
import UserNode from '../components/workspace/nodes/UserNode';
import { useStatusSSE } from '../hooks/useSSE';

const nodeTypes = {
  prompt: PromptNode,
  code: CodeNode,
  user: UserNode,
  webframe: WebFrameNode,
  label: LabelNode,
  schedule: ScheduleNode,
  event: EventNode,
  group: GroupNode,
  agent: AgentNode,
  mcp: AgentNode,
};

const minimapStyle = {
  height: 100,
  width: 150,
};
const defaultEdgeOptions = {
  type: 'smoothstep',
  animated: true,
};
const snapGrid = [16, 16];
const defaultViewport = { x: 0, y: 0, zoom: 1 };

function getViewportSignature(viewport = {}) {
  return `${viewport.x ?? 0}:${viewport.y ?? 0}:${viewport.zoom ?? 1}`;
}

const minimapNodeColor = (node) => {
  switch (node.type) {
    case 'prompt': return '#58a6ff';
    case 'code': return '#3fb950';
    case 'user': return '#39c5cf';
    case 'webframe': return '#d29922';
    case 'label': return '#8b949e';
    case 'schedule': return '#bc8cff';
    case 'event': return '#ffa657';
    case 'group': return '#6e7681';
    case 'agent': return '#f85149';
    default: return '#8b949e';
  }
};

function WorkspaceInner() {
  const {
    nodes,
    edges,
    viewport,
    viewportVersion,
    loading,
    error,
  } = useWorkspaceGraph();
  const {
    onNodesChange,
    onEdgesChange,
    onConnect,
    addNode,
    captureViewport,
    removeNodes,
    removeEdges,
    duplicateNode,
    bringToFront,
    sendToBack,
    toggleExpanded,
    refreshWorkspace,
    syncWidgets,
    replaceGraph,
  } = useWorkspaceActions();
  const workspaceReady = !loading && !error;

  const [jsonOpen, setJsonOpen] = useState(false);
  const graphJson = useMemo(
    () => JSON.stringify(createPersistedGraph(nodes, edges), null, 2),
    [nodes, edges],
  );
  const handleJsonApply = useCallback((parsed) => {
    replaceGraph(parsed.nodes, parsed.edges);
  }, [replaceGraph]);

  const reactFlowInstance = useReactFlow();
  const reactFlowWrapper = useRef(null);
  const viewportSignatureRef = useRef(getViewportSignature(defaultViewport));
  const lastAppliedViewportVersionRef = useRef(-1);
  const [contextMenu, setContextMenu] = useState(null);
  const [dragOver, setDragOver] = useState(false);

  useOnViewportChange({
    onChange: workspaceReady
      ? (nextViewport) => {
        viewportSignatureRef.current = getViewportSignature(nextViewport);
        captureViewport(nextViewport);
      }
      : undefined,
  });

  useEffect(() => {
    if (!workspaceReady || lastAppliedViewportVersionRef.current === viewportVersion) {
      return;
    }

    lastAppliedViewportVersionRef.current = viewportVersion;
    const nextSignature = getViewportSignature(viewport);
    if (viewportSignatureRef.current === nextSignature) {
      return;
    }

    viewportSignatureRef.current = nextSignature;
    reactFlowInstance.setViewport(viewport);
  }, [reactFlowInstance, viewport, viewportVersion, workspaceReady]);

  const handleStatusEvent = useCallback((event) => {
    if (event?.type !== 'widgets' || !event?.data) {
      return;
    }
    syncWidgets(event.data.items || event.data.widgets || []);
  }, [syncWidgets]);

  useStatusSSE(handleStatusEvent);

  // Handle drop from sidebar
  const onDrop = useCallback(
    (event) => {
      if (!workspaceReady) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      setDragOver(false);

      const type = event.dataTransfer.getData('application/reactflow');
      if (!type) return;

      const position = reactFlowInstance.screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });

      addNode(type, position);
    },
    [workspaceReady, reactFlowInstance, addNode]
  );

  const onDragOver = useCallback((event) => {
    if (!workspaceReady) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    setDragOver(true);
  }, [workspaceReady]);

  const onDragLeave = useCallback(() => {
    setDragOver(false);
  }, []);

  // Context menu
  const onNodeContextMenu = useCallback((event, node) => {
    if (!workspaceReady) {
      return;
    }
    event.preventDefault();
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      nodeId: node.id,
    });
  }, [workspaceReady]);

  const onPaneContextMenu = useCallback((event) => {
    if (!workspaceReady) {
      return;
    }
    event.preventDefault();
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      nodeId: null,
    });
  }, [workspaceReady]);

  const onContextMenuAction = useCallback(
    (action, nodeId) => {
      switch (action) {
        case 'delete':
          if (nodeId) removeNodes([nodeId]);
          break;
        case 'duplicate':
          if (nodeId) duplicateNode(nodeId);
          break;
        case 'bringToFront':
          if (nodeId) bringToFront(nodeId);
          break;
        case 'sendToBack':
          if (nodeId) sendToBack(nodeId);
          break;
        case 'expand':
          if (nodeId) toggleExpanded(nodeId);
          break;
        case 'fitView':
          reactFlowInstance.fitView({ padding: 0.2 });
          break;
        case 'resetView':
          reactFlowInstance.setViewport({ x: 0, y: 0, zoom: 1 });
          break;
      }
    },
    [removeNodes, duplicateNode, bringToFront, sendToBack, toggleExpanded, reactFlowInstance]
  );

  const closeContextMenu = useCallback(() => {
    setContextMenu(null);
  }, []);

  // Close context menu on click
  const onPaneClick = useCallback(() => {
    setContextMenu(null);
  }, []);

  const handleKeyboardShortcut = useCallback(
    (event) => {
      if (!workspaceReady) {
        return;
      }
      // Only handle if not in an input/textarea
      const tag = event.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      if (event.key === 'Delete' || event.key === 'Backspace') {
        const selectedNodes = nodes.filter((node) => node.selected);
        const selectedEdges = edges.filter((edge) => edge.selected);
        if (selectedEdges.length > 0) {
          removeEdges(selectedEdges.map((edge) => edge.id));
        }
        if (selectedNodes.length > 0) {
          removeNodes(selectedNodes.map((node) => node.id));
        }
      }

      if (event.ctrlKey && event.key === 'd') {
        event.preventDefault();
        const selectedNode = nodes.find((n) => n.selected);
        if (selectedNode) {
          duplicateNode(selectedNode.id);
        }
      }
    },
    [workspaceReady, nodes, edges, removeNodes, removeEdges, duplicateNode]
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyboardShortcut);
    return () => window.removeEventListener('keydown', handleKeyboardShortcut);
  }, [handleKeyboardShortcut]);

  return (
    <div className="workspace-container" tabIndex={-1}>
      <WorkspaceSidebar />
      <div
        className={`workspace-canvas ${dragOver ? 'drag-over' : ''} ${!workspaceReady ? 'locked' : ''}`}
        ref={reactFlowWrapper}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
      >
        {loading && <div className="workspace-loading">Loading workflows...</div>}
        {error && (
          <div className="workspace-loading workspace-error">
            Failed to load workflows.
            <button className="node-action-btn primary" onClick={refreshWorkspace}>Retry</button>
          </div>
        )}
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={workspaceReady ? onNodesChange : undefined}
          onEdgesChange={workspaceReady ? onEdgesChange : undefined}
          onConnect={workspaceReady ? onConnect : undefined}
          onNodeContextMenu={workspaceReady ? onNodeContextMenu : undefined}
          onPaneContextMenu={workspaceReady ? onPaneContextMenu : undefined}
          onPaneClick={workspaceReady ? onPaneClick : undefined}
          nodeTypes={nodeTypes}
          fitView={false}
          deleteKeyCode={null}
          multiSelectionKeyCode="Shift"
          selectionOnDrag={workspaceReady}
          panOnDrag={workspaceReady ? [1, 2] : false}
          nodesDraggable={workspaceReady}
          nodesConnectable={workspaceReady}
          elementsSelectable={workspaceReady}
          zoomOnScroll={workspaceReady}
          zoomOnPinch={workspaceReady}
          zoomOnDoubleClick={workspaceReady}
          panOnScroll={false}
          selectionMode="partial"
          defaultEdgeOptions={defaultEdgeOptions}
          snapToGrid
          snapGrid={snapGrid}
          minZoom={0.1}
          maxZoom={4}
          defaultViewport={defaultViewport}
        >
          <Background gap={16} size={1} color="var(--color-border)" variant="dots" />
          <Controls showInteractive={false} />
          <MiniMap
            style={minimapStyle}
            nodeColor={minimapNodeColor}
            maskColor="rgba(0, 0, 0, 0.6)"
            pannable
            zoomable
          />
        </ReactFlow>

        {contextMenu && (
          <NodeContextMenu
            x={contextMenu.x}
            y={contextMenu.y}
            nodeId={contextMenu.nodeId}
            onClose={closeContextMenu}
            onAction={onContextMenuAction}
          />
        )}
        <WorkspaceDock disabled={!workspaceReady} />
        {jsonOpen ? (
          <WorkflowJsonEditor
            value={graphJson}
            onApply={handleJsonApply}
            onClose={() => setJsonOpen(false)}
          />
        ) : (
          <WorkflowJsonDock onOpen={() => setJsonOpen(true)} />
        )}
      </div>
    </div>
  );
}

export default function WorkflowsPage() {
  return (
    <ReactFlowProvider>
      <WorkspaceProvider>
        <WorkspaceInner />
      </WorkspaceProvider>
    </ReactFlowProvider>
  );
}
