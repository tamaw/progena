/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react';
import { applyNodeChanges } from '@xyflow/react';
import { useApi } from '../hooks/useApi';
import { loadWorkspaceState, saveWorkspaceState } from '../lib/indexedDb';

const WorkspaceContext = createContext(null);

const SAVE_DEBOUNCE_MS = 500;
const SERVER_NODE_TYPES = new Set(['agent', 'mcp', 'schedule']);

const defaultNodeSizes = {
  prompt: { width: 320, height: 200 },
  code: { width: 400, height: 280 },
  webframe: { width: 480, height: 360 },
  label: { width: 200, height: 60 },
  schedule: { width: 300, height: 180 },
  group: { width: 600, height: 400 },
  agent: { width: 320, height: 200 },
  mcp: { width: 320, height: 200 },
};

const defaultNodeData = {
  prompt: () => ({
    prompt: '',
    role: '',
    sourcePath: '',
    primaryAgent: '',
    associatedAgents: [],
    variables: {},
    linkedOutputIds: [],
    lastRunId: null,
    summaryState: '',
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
    widgetKind: 'agent',
  }),
  mcp: () => ({
    agentId: '',
    agentStatus: 'idle',
    currentRunId: null,
    summaryState: '',
    logs: [],
    widgetKind: 'mcp',
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
  mcp: 'MCP',
};

let nodeIdCounter = 0;

function generateId() {
  nodeIdCounter += 1;
  return `node_${Date.now()}_${nodeIdCounter}`;
}

function isServerNode(node) {
  return SERVER_NODE_TYPES.has(node.type) || node.data?.serverManaged;
}

function createArtefact(type, position, overrides = {}) {
  const size = defaultNodeSizes[type] || { width: 300, height: 200 };
  const now = new Date().toISOString();

  return {
    id: overrides.id || generateId(),
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

function normalizeLogs(logs) {
  if (!Array.isArray(logs)) {
    return [];
  }
  return logs.map((entry) => ({
    time: entry.time || entry.timestamp || entry.created_at || entry.last_active || null,
    message: entry.message || entry.content || entry.summary || entry.description || String(entry),
    level: entry.level || 'info',
    source: entry.source || entry.path || 'system',
  }));
}

function mapWidgetToNodeData(widget, existingData = {}) {
  const timestamp = widget.updated_at || existingData.updatedAt || new Date().toISOString();
  const title = widget.title || widget.name || existingData.title || widget.id;
  const summary = widget.summary || widget.description || existingData.summaryState || '';
  const logs = normalizeLogs(widget.logs || widget.data?.logs || existingData.logs);

  if (widget.type === 'schedule') {
    return {
      ...defaultNodeData.schedule(),
      ...existingData,
      ...widget.data,
      title,
      summaryState: summary,
      logs,
      serverManaged: true,
      status: widget.status || existingData.status || 'idle',
      scheduleType: widget.data?.schedule_type || widget.schedule_type || existingData.scheduleType || 'cron',
      expression: widget.data?.expression || widget.expression || existingData.expression || '',
      timezone: widget.data?.timezone || widget.timezone || existingData.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone,
      enabled: widget.data?.enabled ?? widget.enabled ?? existingData.enabled ?? false,
      nextRunAt: widget.data?.next_run_at || widget.next_run_at || existingData.nextRunAt || null,
      lastRunAt: widget.data?.last_run_at || widget.last_run_at || existingData.lastRunAt || null,
      lastRunStatus: widget.data?.last_run_status || widget.last_run_status || existingData.lastRunStatus || 'idle',
      updatedAt: timestamp,
    };
  }

  return {
    ...(widget.type === 'mcp' ? defaultNodeData.mcp() : defaultNodeData.agent()),
    ...existingData,
    ...widget.data,
    title,
    summaryState: summary,
    logs,
    serverManaged: true,
    widgetKind: widget.type === 'mcp' ? 'mcp' : 'agent',
    agentId: widget.name || existingData.agentId || '',
    agentStatus: widget.status || existingData.agentStatus || 'idle',
    currentRunId: widget.data?.current_run_id || widget.current_run_id || existingData.currentRunId || null,
    transport: widget.transport || widget.data?.transport || existingData.transport || '',
    updatedAt: timestamp,
  };
}

function getActionText(payload) {
  if (!payload) {
    return '';
  }
  if (typeof payload === 'string') {
    return payload;
  }
  return (
    payload.response ||
    payload.content ||
    payload.output?.[0]?.content?.[0]?.text ||
    payload.data?.content ||
    payload.result ||
    payload.message ||
    ''
  );
}

function buildServerNodes(widgets, existingNodes) {
  const existingMap = new Map(existingNodes.map((node) => [node.id, node]));

  return widgets.map((widget, index) => {
    const existing = existingMap.get(widget.id);
    const type = widget.type;
    const size = defaultNodeSizes[type] || { width: 320, height: 200 };
    const column = index % 3;
    const row = Math.floor(index / 3);
    const position = existing?.position || { x: 80 + column * 360, y: 80 + row * 240 };

    return {
      id: widget.id,
      type,
      position,
      zIndex: existing?.zIndex,
      selected: existing?.selected,
      style: {
        width: existing?.style?.width || size.width,
        height: existing?.style?.height || size.height,
      },
      data: {
        ...mapWidgetToNodeData(widget, existing?.data),
      },
    };
  });
}

function mergeNodes(savedNodes, widgets) {
  const serverNodes = buildServerNodes(widgets, savedNodes);
  const serverIds = new Set(serverNodes.map((node) => node.id));
  const localNodes = savedNodes.filter((node) => !isServerNode(node) || !serverIds.has(node.id));
  return [...localNodes, ...serverNodes];
}

export function WorkspaceProvider({ children }) {
  const {
    getWorkspace,
    getWidgets,
    updateWidget,
    runAction,
    generateAction,
  } = useApi();
  const [nodes, setNodes] = useState([]);
  const [viewport, setViewport] = useState({ x: 0, y: 0, zoom: 1 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const saveTimerRef = useRef(null);
  const patchTimersRef = useRef(new Map());
  const latestNodesRef = useRef([]);
  const latestViewportRef = useRef({ x: 0, y: 0, zoom: 1 });

  const mergeWidgetNodes = useCallback((widgetPayload, nextViewport = null) => {
    const widgetList = widgetPayload?.items || widgetPayload?.widgets || widgetPayload || [];
    if (!Array.isArray(widgetList)) {
      return;
    }

    setNodes((currentNodes) => {
      const baseNodes = latestNodesRef.current.length > 0 ? latestNodesRef.current : currentNodes;
      const mergedNodes = mergeNodes(baseNodes, widgetList);
      latestNodesRef.current = mergedNodes;
      return mergedNodes;
    });

    if (nextViewport) {
      latestViewportRef.current = nextViewport;
      setViewport(nextViewport);
    }
  }, []);

  const refreshWorkspace = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const [savedState, workspacePayload, widgetsPayload] = await Promise.all([
        loadWorkspaceState(),
        getWorkspace().catch(() => ({})),
        getWidgets(),
      ]);

      const savedNodes = savedState?.nodes || [];
      const savedViewport = savedState?.viewport || workspacePayload?.viewport || { x: 0, y: 0, zoom: 1 };
      const widgetList = widgetsPayload?.items || widgetsPayload?.widgets || widgetsPayload || [];
      const mergedNodes = mergeNodes(savedNodes, widgetList);

      latestNodesRef.current = mergedNodes;
      latestViewportRef.current = savedViewport;
      setNodes(mergedNodes);
      setViewport(savedViewport);
    } catch (loadError) {
      console.error('[WorkspaceContext] Failed to load workspace:', loadError);
      setError(loadError);
    } finally {
      setLoading(false);
    }
  }, [getWidgets, getWorkspace]);

  const syncWidgets = useCallback((widgetPayload) => {
    mergeWidgetNodes(widgetPayload);
  }, [mergeWidgetNodes]);

  useEffect(() => {
    refreshWorkspace();
  }, [refreshWorkspace]);

  const persistWorkspace = useCallback((currentNodes, currentViewport) => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = setTimeout(() => {
      saveWorkspaceState({
        nodes: currentNodes,
        viewport: currentViewport,
      });
    }, SAVE_DEBOUNCE_MS);
  }, []);

  useEffect(() => {
    latestNodesRef.current = nodes;
    latestViewportRef.current = viewport;
    if (!loading) {
      persistWorkspace(nodes, viewport);
    }
  }, [nodes, viewport, loading, persistWorkspace]);

  useEffect(() => () => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
    }
    patchTimersRef.current.forEach((timerId) => clearTimeout(timerId));
  }, []);

  const scheduleWidgetPatch = useCallback((nodeId, updates) => {
    const existing = patchTimersRef.current.get(nodeId);
    if (existing) {
      clearTimeout(existing);
    }

    const timerId = setTimeout(async () => {
      patchTimersRef.current.delete(nodeId);
      try {
        await updateWidget(nodeId, updates);
      } catch (patchError) {
        console.warn(`[WorkspaceContext] Failed to patch widget ${nodeId}:`, patchError);
      }
    }, SAVE_DEBOUNCE_MS);

    patchTimersRef.current.set(nodeId, timerId);
  }, [updateWidget]);

  const onNodesChange = useCallback((changes) => {
    setNodes((currentNodes) => applyNodeChanges(changes, currentNodes));
  }, []);

  const addNode = useCallback((type, position, overrides = {}) => {
    const newNode = createArtefact(type, position, overrides);
    setNodes((currentNodes) => [...currentNodes, newNode]);
    return newNode;
  }, []);

  const addPromptFromLibrary = useCallback((prompt, position = null) => {
    const baseIndex = latestNodesRef.current.length;
    const nextPosition = position || {
      x: 120 + (baseIndex % 3) * 340,
      y: 100 + Math.floor(baseIndex / 3) * 220,
    };
    return addNode('prompt', nextPosition, {
      title: prompt.filename.replace(/\.md$/i, ''),
      prompt: prompt.content || '',
      role: prompt.filename.toLowerCase().includes('system') ? 'system' : '',
      sourcePath: prompt.path,
      primaryAgent: prompt.primary_agent || '',
      associatedAgents: prompt.associated_agents || [],
      tags: [...(prompt.associated_agents || [])],
    });
  }, [addNode]);

  const upsertNode = useCallback((nextNode) => {
    setNodes((currentNodes) => {
      const hasNode = currentNodes.some((node) => node.id === nextNode.id);
      if (!hasNode) {
        return [...currentNodes, nextNode];
      }
      return currentNodes.map((node) => (node.id === nextNode.id ? nextNode : node));
    });
  }, []);

  const updateNodeData = useCallback((nodeId, dataUpdates) => {
    setNodes((currentNodes) =>
      currentNodes.map((node) => {
        if (node.id !== nodeId) {
          return node;
        }

        const updatedNode = {
          ...node,
          data: {
            ...node.data,
            ...dataUpdates,
            updatedAt: new Date().toISOString(),
          },
        };

        if (node.type === 'schedule' && node.data?.serverManaged) {
          scheduleWidgetPatch(nodeId, {
            name: updatedNode.data.title,
            enabled: updatedNode.data.enabled,
            schedule_type: updatedNode.data.scheduleType,
            expression: updatedNode.data.expression,
            timezone: updatedNode.data.timezone,
          });
        }

        return updatedNode;
      })
    );
  }, [scheduleWidgetPatch]);

  const removeNodes = useCallback((nodeIds) => {
    const idSet = new Set(nodeIds);
    setNodes((currentNodes) =>
      currentNodes.filter((node) => !idSet.has(node.id) || node.data?.serverManaged)
    );
  }, []);

  const duplicateNode = useCallback((nodeId) => {
    setNodes((currentNodes) => {
      const source = currentNodes.find((node) => node.id === nodeId);
      if (!source || source.data?.serverManaged) {
        return currentNodes;
      }

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

      return [...currentNodes, newNode];
    });
  }, []);

  const bringToFront = useCallback((nodeId) => {
    setNodes((currentNodes) => {
      const maxZ = Math.max(...currentNodes.map((node) => node.zIndex || 0), 0);
      return currentNodes.map((node) =>
        node.id === nodeId ? { ...node, zIndex: maxZ + 1 } : node
      );
    });
  }, []);

  const sendToBack = useCallback((nodeId) => {
    setNodes((currentNodes) => {
      const minZ = Math.min(...currentNodes.map((node) => node.zIndex || 0), 0);
      return currentNodes.map((node) =>
        node.id === nodeId ? { ...node, zIndex: minZ - 1 } : node
      );
    });
  }, []);

  const toggleExpanded = useCallback((nodeId) => {
    setNodes((currentNodes) =>
      currentNodes.map((node) => {
        if (node.id !== nodeId) {
          return node;
        }

        const expanded = !node.data.expanded;
        const defaultSize = defaultNodeSizes[node.type] || { width: 300, height: 200 };
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
          data: {
            ...node.data,
            expanded,
            updatedAt: new Date().toISOString(),
          },
        };
      })
    );
  }, []);

  const runPromptNode = useCallback(async (nodeId) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node) {
      return;
    }

    updateNodeData(nodeId, { status: 'running' });

    try {
      const result = await runAction({
        widget_id: nodeId,
        action_type: 'run_prompt',
        prompt: node.data.prompt,
        role: node.data.role,
        title: node.data.title,
      });
      updateNodeData(nodeId, {
        status: 'completed',
        lastRunId: result.run_id || result.request_id || node.data.lastRunId,
        summaryState: getActionText(result),
      });
    } catch (runError) {
      updateNodeData(nodeId, {
        status: 'error',
        summaryState: runError.message,
      });
    }
  }, [runAction, updateNodeData]);

  const generateFromPromptNode = useCallback(async (nodeId) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node) {
      return;
    }

    updateNodeData(nodeId, { status: 'running' });

    try {
      const result = await generateAction({
        widget_id: nodeId,
        action_type: 'generate_code',
        prompt: node.data.prompt,
        role: node.data.role,
        title: node.data.title,
      });
      const generatedContent = getActionText(result);
      const codeNode = createArtefact('code', {
        x: node.position.x + 360,
        y: node.position.y,
      }, {
        title: `${node.data.title} Output`,
        content: generatedContent || '// No code returned',
        originPromptId: nodeId,
      });

      upsertNode(codeNode);
      updateNodeData(nodeId, {
        status: 'completed',
        summaryState: generatedContent ? 'Code generated via backend.' : 'Generation completed.',
        linkedOutputIds: [...(node.data.linkedOutputIds || []), codeNode.id],
      });
    } catch (generateError) {
      updateNodeData(nodeId, {
        status: 'error',
        summaryState: generateError.message,
      });
    }
  }, [generateAction, updateNodeData, upsertNode]);

  const runCodeNode = useCallback(async (nodeId) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node) {
      return;
    }

    updateNodeData(nodeId, { status: 'running' });

    try {
      const result = await runAction({
        widget_id: nodeId,
        action_type: 'run_code',
        language: node.data.language,
        code: node.data.content,
        title: node.data.title,
      });
      updateNodeData(nodeId, {
        status: 'completed',
        lastRunId: result.run_id || result.request_id || node.data.lastRunId,
        outputRefs: [getActionText(result)].filter(Boolean),
      });
    } catch (runError) {
      updateNodeData(nodeId, {
        status: 'error',
        outputRefs: [runError.message],
      });
    }
  }, [runAction, updateNodeData]);

  const runScheduleNode = useCallback(async (nodeId) => {
    updateNodeData(nodeId, { lastRunStatus: 'running' });
    try {
      const result = await runAction({
        widget_id: nodeId,
        action_type: 'run_schedule',
      });
      updateNodeData(nodeId, {
        lastRunStatus: 'completed',
        lastRunAt: new Date().toISOString(),
        summaryState: getActionText(result),
      });
    } catch (runError) {
      updateNodeData(nodeId, {
        lastRunStatus: 'error',
        summaryState: runError.message,
      });
    }
  }, [runAction, updateNodeData]);

  return (
    <WorkspaceContext.Provider
      value={{
        nodes,
        setNodes,
        viewport,
        setViewport,
        loading,
        error,
        onNodesChange,
        addNode,
        addPromptFromLibrary,
        updateNodeData,
        removeNodes,
        duplicateNode,
        bringToFront,
        sendToBack,
        toggleExpanded,
        refreshWorkspace,
        syncWidgets,
        runPromptNode,
        generateFromPromptNode,
        runCodeNode,
        runScheduleNode,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspaceContext() {
  const context = useContext(WorkspaceContext);
  if (!context) {
    throw new Error('useWorkspaceContext must be used within a WorkspaceProvider');
  }
  return context;
}
