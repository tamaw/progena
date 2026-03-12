import { useCallback, useRef, useState } from 'react';
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
import NodeContextMenu from '../components/workspace/NodeContextMenu';
import { useWorkspaceContext } from '../hooks/useWorkspaceContext';

import PromptNode from '../components/workspace/nodes/PromptNode';
import CodeNode from '../components/workspace/nodes/CodeNode';
import WebFrameNode from '../components/workspace/nodes/WebFrameNode';
import LabelNode from '../components/workspace/nodes/LabelNode';
import ScheduleNode from '../components/workspace/nodes/ScheduleNode';
import GroupNode from '../components/workspace/nodes/GroupNode';
import AgentNode from '../components/workspace/nodes/AgentNode';

const nodeTypes = {
  prompt: PromptNode,
  code: CodeNode,
  webframe: WebFrameNode,
  label: LabelNode,
  schedule: ScheduleNode,
  group: GroupNode,
  agent: AgentNode,
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
  } = useWorkspaceContext();

  const reactFlowInstance = useReactFlow();
  const reactFlowWrapper = useRef(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [contextMenu, setContextMenu] = useState(null);
  const [dragOver, setDragOver] = useState(false);

  // Handle drop from sidebar
  const onDrop = useCallback(
    (event) => {
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
    [reactFlowInstance, addNode]
  );

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    setDragOver(true);
  }, []);

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
    event.preventDefault();
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      nodeId: node.id,
    });
  }, []);

  const onPaneContextMenu = useCallback((event) => {
    event.preventDefault();
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      nodeId: null,
    });
  }, []);

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
    [nodes, removeNodes, duplicateNode]
  );

  return (
    <div className="workspace-container" onKeyDown={onKeyDown} tabIndex={-1}>
      <WorkspaceSidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
      />
      <div
        className={`workspace-canvas ${dragOver ? 'drag-over' : ''}`}
        ref={reactFlowWrapper}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
      >
        <ReactFlow
          nodes={nodes}
          edges={[]}
          onNodesChange={onNodesChange}
          onMoveEnd={onMoveEnd}
          onNodeContextMenu={onNodeContextMenu}
          onPaneContextMenu={onPaneContextMenu}
          onPaneClick={onPaneClick}
          nodeTypes={nodeTypes}
          fitView={false}
          deleteKeyCode={null}
          multiSelectionKeyCode="Shift"
          selectionOnDrag
          panOnDrag={[1, 2]}
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
    </div>
  );
}

export default function WorkspacePage() {
  return (
    <>
      <Header connectionState="connected" />
      <ReactFlowProvider>
        <WorkspaceInner />
      </ReactFlowProvider>
    </>
  );
}
