import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  ReactFlowProvider,
} from '@xyflow/react';

import Header from '../components/layout/Header';
import WorkspaceSidebar from '../components/workspace/WorkspaceSidebar';
import PromptLibraryPanel from '../components/workspace/PromptLibraryPanel';
import NodeContextMenu from '../components/workspace/NodeContextMenu';
import { WorkspaceProvider, useWorkspaceContext } from '../context/WorkspaceContext';

import PromptNode from '../components/workspace/nodes/PromptNode';
import CodeNode from '../components/workspace/nodes/CodeNode';
import WebFrameNode from '../components/workspace/nodes/WebFrameNode';
import LabelNode from '../components/workspace/nodes/LabelNode';
import ScheduleNode from '../components/workspace/nodes/ScheduleNode';
import GroupNode from '../components/workspace/nodes/GroupNode';
import AgentNode from '../components/workspace/nodes/AgentNode';
import { useApi } from '../hooks/useApi';
import { useStatusSSE } from '../hooks/useSSE';

const nodeTypes = {
  prompt: PromptNode,
  code: CodeNode,
  webframe: WebFrameNode,
  label: LabelNode,
  schedule: ScheduleNode,
  group: GroupNode,
  agent: AgentNode,
  mcp: AgentNode,
};

const minimapStyle = {
  height: 100,
  width: 150,
};

const minimapNodeColor = (node) => {
  switch (node.type) {
    case 'prompt': return '#58a6ff';
    case 'code': return '#3fb950';
    case 'webframe': return '#d29922';
    case 'label': return '#8b949e';
    case 'schedule': return '#bc8cff';
    case 'group': return '#6e7681';
    case 'agent': return '#f85149';
    default: return '#8b949e';
  }
};

function WorkspaceInner() {
  const {
    nodes,
    viewport,
    onNodesChange,
    addNode,
    removeNodes,
    duplicateNode,
    bringToFront,
    sendToBack,
    toggleExpanded,
    setViewport,
    loading,
    error,
    refreshWorkspace,
    syncWidgets,
  } = useWorkspaceContext();
  const workspaceReady = !loading && !error;

  const reactFlowInstance = useReactFlow();
  const reactFlowWrapper = useRef(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [contextMenu, setContextMenu] = useState(null);
  const [dragOver, setDragOver] = useState(false);

  useEffect(() => {
    reactFlowInstance.setViewport(viewport);
  }, [reactFlowInstance, viewport]);

  const handleStatusEvent = useCallback((event) => {
    if (!event?.data) {
      return;
    }

    if (event.type === 'widgets') {
      syncWidgets(event.data.items || event.data.widgets || []);
      return;
    }

    if (event.type === 'connected' || event.type === 'status' || event.type === 'heartbeat') {
      if (Array.isArray(event.data.widgets)) {
        syncWidgets(event.data.widgets);
      }
    }
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

  // Track viewport changes for persistence
  const onMoveEnd = useCallback(
    (_, viewport) => {
      setViewport(viewport);
    },
    [setViewport]
  );

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

  // Keyboard shortcuts
  const onKeyDown = useCallback(
    (event) => {
      if (!workspaceReady) {
        return;
      }
      // Only handle if not in an input/textarea
      const tag = event.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      if (event.key === 'Delete' || event.key === 'Backspace') {
        const selectedNodes = nodes.filter(
          (n) => n.selected
        );
        if (selectedNodes.length > 0) {
          removeNodes(selectedNodes.map((n) => n.id));
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
    [workspaceReady, nodes, removeNodes, duplicateNode]
  );

  return (
    <div className="workspace-container" onKeyDown={onKeyDown} tabIndex={-1}>
      <WorkspaceSidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
        disabled={!workspaceReady}
      />
      <div
        className={`workspace-canvas ${dragOver ? 'drag-over' : ''} ${!workspaceReady ? 'locked' : ''}`}
        ref={reactFlowWrapper}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
      >
        {loading && <div className="workspace-loading">Loading workspace...</div>}
        {error && (
          <div className="workspace-loading workspace-error">
            Failed to load workspace.
            <button className="node-action-btn primary" onClick={refreshWorkspace}>Retry</button>
          </div>
        )}
        <ReactFlow
          nodes={nodes}
          edges={[]}
          onNodesChange={workspaceReady ? onNodesChange : undefined}
          onMoveEnd={workspaceReady ? onMoveEnd : undefined}
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
          panOnScroll={workspaceReady}
          selectionMode="partial"
          snapToGrid
          snapGrid={[16, 16]}
          minZoom={0.1}
          maxZoom={4}
          defaultViewport={viewport}
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
      </div>
      <PromptLibraryPanel disabled={!workspaceReady} />
    </div>
  );
}

export default function WorkspacePage() {
  const { connectionState } = useApi();

  return (
    <>
      <Header connectionState={connectionState} />
      <ReactFlowProvider>
        <WorkspaceProvider>
          <WorkspaceInner />
        </WorkspaceProvider>
      </ReactFlowProvider>
    </>
  );
}
