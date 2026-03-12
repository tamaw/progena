import { createContext, useState, useCallback, useRef, useEffect } from 'react';
import { applyNodeChanges } from '@xyflow/react';

const WorkspaceContext = createContext(null);

const STORAGE_KEY = 'progenitor_workspace_state';
const SAVE_DEBOUNCE_MS = 500;

const defaultNodeSizes = {
  prompt: { width: 320, height: 200 },
  code: { width: 400, height: 280 },
  webframe: { width: 480, height: 360 },
  label: { width: 200, height: 60 },
  schedule: { width: 300, height: 180 },
  group: { width: 600, height: 400 },
  agent: { width: 320, height: 200 },
};

const defaultNodeData = {
  prompt: () => ({
    prompt: '',
    role: '',
    variables: {},
    linkedOutputIds: [],
    lastRunId: null,
  }),
  code: () => ({
    language: 'javascript',
    content: '',
    runtimeProfile: '',
    originPromptId: null,
    lastRunId: null,
    outputRefs: [],
  }),
  webframe: () => ({
    sourceType: 'html',
    html: '<html>\n<body>\n  <h1>Hello World</h1>\n</body>\n</html>',
    css: '',
    js: '',
    url: '',
    sandbox: {
      allowScripts: true,
      allowSameOrigin: false,
      allowForms: false,
      allowPopups: false,
    },
  }),
  label: () => ({
    text: 'Label',
    variant: 'note',
  }),
  schedule: () => ({
    scheduleType: 'cron',
    expression: '',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    enabled: false,
    targetArtefactId: null,
    nextRunAt: null,
    lastRunAt: null,
    lastRunStatus: 'idle',
  }),
  group: () => ({
    childIds: [],
    description: '',
  }),
  agent: () => ({
    agentId: '',
    agentStatus: 'idle',
    currentRunId: null,
    summaryState: '',
    logs: [],
  }),
};

const typeLabels = {
  prompt: 'Prompt',
  code: 'Code',
  webframe: 'Web Frame',
  label: 'Label',
  schedule: 'Schedule',
  group: 'Group',
  agent: 'Agent',
};

let nodeIdCounter = 0;

function generateId() {
  nodeIdCounter += 1;
  return `node_${Date.now()}_${nodeIdCounter}`;
}

function createArtefact(type, position, overrides = {}) {
  const size = defaultNodeSizes[type] || { width: 300, height: 200 };
  const now = new Date().toISOString();

  return {
    id: generateId(),
    type,
    position: { x: position.x, y: position.y },
    style: { width: size.width, height: size.height },
    data: {
      title: overrides.title || typeLabels[type] || type,
      createdAt: now,
      updatedAt: now,
      createdBy: { kind: 'user', id: 'local' },
      tags: [],
      status: 'idle',
      locked: false,
      expanded: false,
      ...defaultNodeData[type]?.(),
      ...overrides,
    },
  };
}

function loadWorkspace() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      return {
        nodes: parsed.nodes || [],
        viewport: parsed.viewport || { x: 0, y: 0, zoom: 1 },
      };
    }
  } catch (e) {
    console.warn('[WorkspaceContext] Failed to load workspace:', e);
  }
  return { nodes: [], viewport: { x: 0, y: 0, zoom: 1 } };
}

export function WorkspaceProvider({ children }) {
  const [nodes, setNodes] = useState(() => loadWorkspace().nodes);
  const [viewport, setViewport] = useState(() => loadWorkspace().viewport);
  const saveTimerRef = useRef(null);

  // Debounced save to localStorage
  const saveWorkspace = useCallback((currentNodes, currentViewport) => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = setTimeout(() => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
          nodes: currentNodes,
          viewport: currentViewport,
        }));
      } catch (e) {
        console.warn('[WorkspaceContext] Failed to save workspace:', e);
      }
    }, SAVE_DEBOUNCE_MS);
  }, []);

  // Save whenever nodes or viewport change
  useEffect(() => {
    saveWorkspace(nodes, viewport);
  }, [nodes, viewport, saveWorkspace]);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
      }
    };
  }, []);

  const onNodesChange = useCallback((changes) => {
    setNodes((nds) => applyNodeChanges(changes, nds));
  }, []);

  const addNode = useCallback((type, position, overrides = {}) => {
    const newNode = createArtefact(type, position, overrides);
    setNodes((nds) => [...nds, newNode]);
    return newNode;
  }, []);

  const updateNodeData = useCallback((nodeId, dataUpdates) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: {
              ...node.data,
              ...dataUpdates,
              updatedAt: new Date().toISOString(),
            },
          };
        }
        return node;
      })
    );
  }, []);

  const removeNodes = useCallback((nodeIds) => {
    const idSet = new Set(nodeIds);
    setNodes((nds) => nds.filter((node) => !idSet.has(node.id)));
  }, []);

  const duplicateNode = useCallback((nodeId) => {
    setNodes((nds) => {
      const source = nds.find((n) => n.id === nodeId);
      if (!source) return nds;

      const newNode = {
        ...source,
        id: generateId(),
        position: {
          x: source.position.x + 40,
          y: source.position.y + 40,
        },
        data: {
          ...source.data,
          title: `${source.data.title} (copy)`,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        },
      };
      return [...nds, newNode];
    });
  }, []);

  const bringToFront = useCallback((nodeId) => {
    setNodes((nds) => {
      const maxZ = Math.max(...nds.map((n) => n.zIndex || 0), 0);
      return nds.map((n) =>
        n.id === nodeId ? { ...n, zIndex: maxZ + 1 } : n
      );
    });
  }, []);

  const sendToBack = useCallback((nodeId) => {
    setNodes((nds) => {
      const minZ = Math.min(...nds.map((n) => n.zIndex || 0), 0);
      return nds.map((n) =>
        n.id === nodeId ? { ...n, zIndex: minZ - 1 } : n
      );
    });
  }, []);

  const toggleExpanded = useCallback((nodeId) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          const expanded = !node.data.expanded;
          const type = node.type;
          const defaultSize = defaultNodeSizes[type] || { width: 300, height: 200 };
          const expandedSize = {
            width: Math.max(defaultSize.width, 480),
            height: Math.max(defaultSize.height, 400),
          };
          return {
            ...node,
            style: {
              ...node.style,
              width: expanded ? expandedSize.width : defaultSize.width,
              height: expanded ? expandedSize.height : defaultSize.height,
            },
            data: { ...node.data, expanded },
          };
        }
        return node;
      })
    );
  }, []);

  return (
    <WorkspaceContext.Provider
      value={{
        nodes,
        setNodes,
        viewport,
        setViewport,
        onNodesChange,
        addNode,
        updateNodeData,
        removeNodes,
        duplicateNode,
        bringToFront,
        sendToBack,
        toggleExpanded,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export { WorkspaceContext };
