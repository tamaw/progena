/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  useEffect,
  useMemo,
} from 'react';
import { addEdge, applyEdgeChanges, applyNodeChanges } from '@xyflow/react';
import { useWorkspaceApi } from '../hooks/api/useWorkspaceApi';
import { useWidgetsApi } from '../hooks/api/useWidgetsApi';
import { useSchedulesApi } from '../hooks/api/useSchedulesApi';
import { loadWorkspaceState, saveWorkspaceState } from '../lib/indexedDb';
import {
  DEFAULT_EVENT_TYPE,
  defaultNodeSizes,
  appendEventLog,
  areStringArraysEqual,
  buildEventOutputText,
  buildEventWaitingSummary,
  buildExecutionContext,
  buildManualEventPayload,
  buildPromptInvocation,
  buildServerNode,
  buildUserMessageInstruction,
  createArtefact,
  createDataflowEdge,
  createPersistedGraph,
  createPromptAgentEdge,
  deriveAvailabilityStatusFromRefreshError,
  extractAgentOptions,
  formatWidgetLabel,
  generateId,
  getActionText,
  getAgentNameFromNode,
  getConnectedAgentTarget,
  getNodeOutputText,
  getOutgoingDataflowTargets,
  getPromptTargetAgent,
  getViewportSignature,
  isAgentNodeType,
  isDataflowEdge,
  isDataflowSourceType,
  isDataflowTargetType,
  isPromptAgentEdge,
  loadDismissedWidgetIds,
  mergeNodes,
  normalizeAgentName,
  persistDismissedWidgetIds,
  resolveNodeExecutionInput,
} from './workspaceGraph';

const WorkspaceGraphContext = createContext(null);
const WorkspaceActionsContext = createContext(null);
const WorkspaceMetaContext = createContext(null);

const SAVE_DEBOUNCE_MS = 500;

export function WorkspaceProvider({ children }) {
  const { getWorkspace, runAction, generateAction, createEvent } = useWorkspaceApi();
  const { getWidgets, getWidgetSchemas, refreshWidget, updateWidget, deleteWidget } = useWidgetsApi();
  const { createSchedule } = useSchedulesApi();
  const [nodes, setNodes] = useState([]);
  const [viewport, setViewportState] = useState({ x: 0, y: 0, zoom: 1 });
  const [viewportVersion, setViewportVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [widgetSchemas, setWidgetSchemas] = useState({});
  const [agentOptions, setAgentOptions] = useState(['progenitor']);
  const saveTimerRef = useRef(null);
  const patchTimersRef = useRef(new Map());
  const latestNodesRef = useRef([]);
  const latestEdgesRef = useRef([]);
  const latestViewportRef = useRef({ x: 0, y: 0, zoom: 1 });
  const lastSavedGraphSignatureRef = useRef('');
  const lastSavedViewportSignatureRef = useRef(getViewportSignature({ x: 0, y: 0, zoom: 1 }));
  const dismissedWidgetIdsRef = useRef(loadDismissedWidgetIds());
  const runNodeByTypeRef = useRef(null);
  const [edges, setEdges] = useState([]);

  const mergeWidgetNodes = useCallback((widgetPayload, nextViewport = null) => {
    const widgetList = widgetPayload?.items || widgetPayload?.widgets || widgetPayload || [];
    if (!Array.isArray(widgetList)) {
      return;
    }

    setNodes((currentNodes) => {
      const mergedNodes = mergeNodes(currentNodes, widgetList)
        .filter((node) => !dismissedWidgetIdsRef.current.has(node.id));
      latestNodesRef.current = mergedNodes;
      return mergedNodes;
    });

    if (nextViewport && getViewportSignature(latestViewportRef.current) !== getViewportSignature(nextViewport)) {
      latestViewportRef.current = nextViewport;
      setViewportState(nextViewport);
      setViewportVersion((currentVersion) => currentVersion + 1);
    }
  }, []);

  const refreshWorkspace = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const [savedState, workspacePayload, widgetsPayload, schemasPayload] = await Promise.all([
        loadWorkspaceState(),
        getWorkspace().catch(() => ({})),
        getWidgets(),
        getWidgetSchemas().catch(() => ({ schemas: [] })),
      ]);

      const savedNodes = savedState?.nodes || [];
      const savedEdges = savedState?.edges || [];
      const savedViewport = savedState?.viewport || workspacePayload?.viewport || { x: 0, y: 0, zoom: 1 };
      const widgetList = widgetsPayload?.items || widgetsPayload?.widgets || widgetsPayload || [];
      const schemaMap = Object.fromEntries(
        ((schemasPayload?.schemas) || []).map((schema) => [schema.type, schema])
      );
      const mergedNodes = mergeNodes(savedNodes, widgetList)
        .filter((node) => !dismissedWidgetIdsRef.current.has(node.id));
      const persistedGraph = createPersistedGraph(mergedNodes, savedEdges);

      latestNodesRef.current = mergedNodes;
      latestEdgesRef.current = savedEdges;
      latestViewportRef.current = savedViewport;
      lastSavedGraphSignatureRef.current = JSON.stringify(persistedGraph);
      lastSavedViewportSignatureRef.current = getViewportSignature(savedViewport);
      setWidgetSchemas(schemaMap);
      setNodes(mergedNodes);
      setEdges(savedEdges);
      setViewportState(savedViewport);
      setViewportVersion((currentVersion) => currentVersion + 1);
    } catch (loadError) {
      console.error('[WorkspaceContext] Failed to load workspace:', loadError);
      setError(loadError);
    } finally {
      setLoading(false);
    }
  }, [getWidgetSchemas, getWidgets, getWorkspace]);

  const getWidgetSchema = useCallback((type) => widgetSchemas[type] || null, [widgetSchemas]);
  const getWidgetTabs = useCallback((type) => widgetSchemas[type]?.tabs || [], [widgetSchemas]);
  const getWidgetDefaultTab = useCallback((type) => widgetSchemas[type]?.default_tab || null, [widgetSchemas]);
  const getWidgetHelpText = useCallback((type) => widgetSchemas[type]?.help_text || '', [widgetSchemas]);

  const syncWidgets = useCallback((widgetPayload) => {
    mergeWidgetNodes(widgetPayload);
  }, [mergeWidgetNodes]);

  const mergeSingleWidget = useCallback((widgetPayload, dataUpdates = {}) => {
    if (!widgetPayload) {
      return;
    }

    setNodes((currentNodes) => {
      const mergedNodes = mergeNodes(currentNodes, [widgetPayload]).map((node) => {
        if (node.id !== widgetPayload.id) {
          return node;
        }
        return {
          ...node,
          data: {
            ...node.data,
            ...dataUpdates,
            updatedAt: new Date().toISOString(),
          },
        };
      });
      latestNodesRef.current = mergedNodes;
      return mergedNodes;
    });
  }, []);

  // Replace the entire graph from an external source (e.g. the JSON editor on the
  // Workflows page). Nodes/edges are sanitized so a hand-edited payload can't crash
  // React Flow; the nodes/edges effects below persist the result automatically.
  const replaceGraph = useCallback((nextNodes, nextEdges) => {
    const safeNodes = (Array.isArray(nextNodes) ? nextNodes : [])
      .filter((node) => node && typeof node.id === 'string')
      .map((node) => ({
        ...node,
        type: node.type || 'label',
        position: node.position && typeof node.position === 'object'
          ? { x: Number(node.position.x) || 0, y: Number(node.position.y) || 0 }
          : { x: 0, y: 0 },
        data: node.data && typeof node.data === 'object' ? node.data : {},
      }));
    const nodeIds = new Set(safeNodes.map((node) => node.id));
    const safeEdges = (Array.isArray(nextEdges) ? nextEdges : [])
      .filter((edge) => (
        edge && typeof edge.id === 'string'
        && nodeIds.has(edge.source) && nodeIds.has(edge.target)
      ));
    latestNodesRef.current = safeNodes;
    latestEdgesRef.current = safeEdges;
    setNodes(safeNodes);
    setEdges(safeEdges);
  }, []);

  useEffect(() => {
    refreshWorkspace();
  }, [refreshWorkspace]);

  const persistWorkspace = useCallback(() => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = setTimeout(() => {
      const nextGraph = createPersistedGraph(latestNodesRef.current, latestEdgesRef.current);
      const nextGraphSignature = JSON.stringify(nextGraph);
      const nextViewport = latestViewportRef.current;
      const nextViewportSignature = getViewportSignature(nextViewport);

      if (
        lastSavedGraphSignatureRef.current === nextGraphSignature
        && lastSavedViewportSignatureRef.current === nextViewportSignature
      ) {
        return;
      }

      lastSavedGraphSignatureRef.current = nextGraphSignature;
      lastSavedViewportSignatureRef.current = nextViewportSignature;
      saveWorkspaceState({
        nodes: nextGraph.nodes,
        edges: nextGraph.edges,
        viewport: nextViewport,
      });
    }, SAVE_DEBOUNCE_MS);
  }, []);

  const captureViewport = useCallback((nextViewport) => {
    if (getViewportSignature(latestViewportRef.current) === getViewportSignature(nextViewport)) {
      return;
    }

    latestViewportRef.current = nextViewport;
    if (!loading) {
      persistWorkspace();
    }
  }, [loading, persistWorkspace]);

  useEffect(() => {
    latestNodesRef.current = nodes;
    const nextAgentOptions = extractAgentOptions(nodes);
    setAgentOptions((currentValues) => (
      areStringArraysEqual(currentValues, nextAgentOptions) ? currentValues : nextAgentOptions
    ));
    if (!loading) {
      persistWorkspace();
    }
  }, [nodes, loading, persistWorkspace]);

  useEffect(() => {
    latestEdgesRef.current = edges;
    if (!loading) {
      persistWorkspace();
    }
  }, [edges, loading, persistWorkspace]);

  useEffect(() => () => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
    }
    patchTimersRef.current.forEach((timerId) => clearTimeout(timerId));
  }, []);

  const queueWidgetPatch = useCallback((nodeId, updates) => {
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

  const clearQueuedWidgetPatch = useCallback((nodeId) => {
    const existing = patchTimersRef.current.get(nodeId);
    if (existing) {
      clearTimeout(existing);
      patchTimersRef.current.delete(nodeId);
    }
  }, []);

  const setNodeSaveError = useCallback((nodeId, message) => {
    setNodes((currentNodes) => currentNodes.map((entry) => (
      entry.id === nodeId
        ? {
          ...entry,
          data: {
            ...entry.data,
            saveError: message,
            updatedAt: new Date().toISOString(),
          },
        }
        : entry
    )));
  }, []);

  const deleteBackendManagedNode = useCallback(async (node) => {
    if (node.type !== 'schedule' || !node.data?.serverManaged) {
      return false;
    }

    clearQueuedWidgetPatch(node.id);
    const deleteResult = await deleteWidget(node.id);
    if (deleteResult?.warning) {
      console.warn(`[WorkspaceContext] ${deleteResult.warning}`);
    }
    return true;
  }, [clearQueuedWidgetPatch, deleteWidget]);

  const onNodesChange = useCallback((changes) => {
    setNodes((currentNodes) => applyNodeChanges(changes, currentNodes));
  }, []);

  const onEdgesChange = useCallback((changes) => {
    setEdges((currentEdges) => applyEdgeChanges(changes, currentEdges));
  }, []);

  const addNode = useCallback((type, position, overrides = {}) => {
    const newNode = createArtefact(type, position, overrides);
    if (type === 'schedule' && !newNode.data.serverManaged) {
      newNode.style = {
        ...newNode.style,
        width: Math.max(defaultNodeSizes.schedule.width, 480),
        height: Math.max(defaultNodeSizes.schedule.height, 420),
      };
      newNode.data = {
        ...newNode.data,
        title: overrides.title || 'New Schedule',
        expanded: overrides.expanded ?? true,
        isUnsaved: true,
      };
    }
    if (type === 'event' && !newNode.data.serverManaged) {
      newNode.style = {
        ...newNode.style,
        width: Math.max(defaultNodeSizes.event.width, 480),
        height: Math.max(defaultNodeSizes.event.height, 400),
      };
      newNode.data = {
        ...newNode.data,
        title: overrides.title || 'New Event',
        expanded: overrides.expanded ?? true,
        enabled: overrides.enabled ?? true,
        isUnsaved: true,
        summaryState: overrides.summaryState || buildEventWaitingSummary(overrides.eventType || newNode.data.eventType),
      };
    }
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
      targetAgent: prompt.primary_agent || prompt.associated_agents?.[0] || 'progenitor',
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

        let nextDataUpdates = dataUpdates;
        if (
          node.type === 'event'
          && Object.prototype.hasOwnProperty.call(dataUpdates, 'eventType')
          && dataUpdates.summaryState === undefined
        ) {
          const currentEventType = node.data?.eventType || DEFAULT_EVENT_TYPE;
          const nextEventType = dataUpdates.eventType || currentEventType;
          const currentSummary = node.data?.summaryState || '';
          if (!currentSummary || currentSummary === buildEventWaitingSummary(currentEventType)) {
            nextDataUpdates = {
              ...dataUpdates,
              summaryState: buildEventWaitingSummary(nextEventType),
            };
          }
        }

        const updatedNode = {
          ...node,
          data: {
            ...node.data,
            ...nextDataUpdates,
            updatedAt: new Date().toISOString(),
          },
        };

        if (node.type === 'schedule' && node.data?.serverManaged) {
          queueWidgetPatch(nodeId, {
            name: updatedNode.data.title,
            instruction: updatedNode.data.instruction,
            enabled: updatedNode.data.enabled,
            schedule_type: updatedNode.data.scheduleType,
            expression: updatedNode.data.expression,
            timezone: updatedNode.data.timezone,
          });
        }

        if (node.type === 'event' && node.data?.serverManaged) {
          queueWidgetPatch(nodeId, {
            name: updatedNode.data.title,
            enabled: updatedNode.data.enabled,
            event_type: updatedNode.data.eventType,
          });
        }

        return updatedNode;
      })
    );
  }, [queueWidgetPatch]);

  const connectPromptToAgent = useCallback((promptNodeId, targetAgent) => {
    const promptNode = latestNodesRef.current.find((node) => node.id === promptNodeId && node.type === 'prompt');
    if (!promptNode) {
      return;
    }

    const normalizedTarget = normalizeAgentName(targetAgent);
    const targetNode = latestNodesRef.current.find((node) => (
      isAgentNodeType(node.type)
      && normalizeAgentName(getAgentNameFromNode(node)) === normalizedTarget
    ));

    setEdges((currentEdges) => {
      const withoutPromptAgentEdges = currentEdges.filter(
        (edge) => !(edge.source === promptNodeId && isPromptAgentEdge(edge))
      );
      if (!targetNode || normalizedTarget === 'progenitor') {
        latestEdgesRef.current = withoutPromptAgentEdges;
        return withoutPromptAgentEdges;
      }

      const nextEdges = addEdge(
        createPromptAgentEdge(promptNodeId, targetNode.id),
        withoutPromptAgentEdges
      );
      latestEdgesRef.current = nextEdges;
      return nextEdges;
    });

    updateNodeData(promptNodeId, {
      targetAgent: targetNode ? getAgentNameFromNode(targetNode) : 'progenitor',
    });
  }, [updateNodeData]);

  const connectDataflowNodes = useCallback((sourceNodeId, targetNodeId) => {
    const sourceNode = latestNodesRef.current.find((node) => node.id === sourceNodeId);
    const targetNode = latestNodesRef.current.find((node) => node.id === targetNodeId);
    if (
      !sourceNode
      || !targetNode
      || sourceNodeId === targetNodeId
      || !isDataflowSourceType(sourceNode.type)
      || !isDataflowTargetType(targetNode.type)
    ) {
      return;
    }

    setEdges((currentEdges) => {
      const nextEdgesBase = currentEdges.filter(
        (edge) => !(edge.target === targetNodeId && isDataflowEdge(edge))
      );
      const nextEdges = addEdge(
        createDataflowEdge(sourceNodeId, targetNodeId),
        nextEdgesBase
      );
      latestEdgesRef.current = nextEdges;
      return nextEdges;
    });
  }, []);

  const removeEdges = useCallback((edgeIds) => {
    if (!Array.isArray(edgeIds) || edgeIds.length === 0) {
      return;
    }

    const idSet = new Set(edgeIds);
    const currentEdges = latestEdgesRef.current;
    const removedEdges = currentEdges.filter((edge) => idSet.has(edge.id));
    if (removedEdges.length === 0) {
      return;
    }

    const nextEdges = currentEdges.filter((edge) => !idSet.has(edge.id));
    latestEdgesRef.current = nextEdges;
    setEdges(nextEdges);

    const promptIdsToReset = new Set();
    removedEdges.forEach((edge) => {
      const sourceNode = latestNodesRef.current.find((node) => node.id === edge.source);
      const targetNode = latestNodesRef.current.find((node) => node.id === edge.target);
      if (sourceNode?.type === 'prompt' && isPromptAgentEdge(edge) && isAgentNodeType(targetNode?.type)) {
        const stillConnected = nextEdges.some((nextEdge) => {
          if (nextEdge.source !== edge.source || !isPromptAgentEdge(nextEdge)) {
            return false;
          }
          const nextTarget = latestNodesRef.current.find((node) => node.id === nextEdge.target);
          return isAgentNodeType(nextTarget?.type);
        });

        if (!stillConnected) {
          promptIdsToReset.add(edge.source);
        }
      }
    });

    promptIdsToReset.forEach((promptId) => {
      updateNodeData(promptId, { targetAgent: 'progenitor' });
    });
  }, [updateNodeData]);

  const onConnect = useCallback((connection) => {
    const sourceNode = latestNodesRef.current.find((node) => node.id === connection.source);
    const targetNode = latestNodesRef.current.find((node) => node.id === connection.target);
    if (!sourceNode || !targetNode || sourceNode.id === targetNode.id) {
      return;
    }

    if (sourceNode.type === 'prompt' && isAgentNodeType(targetNode.type)) {
      connectPromptToAgent(sourceNode.id, getAgentNameFromNode(targetNode));
      return;
    }

    if (isDataflowSourceType(sourceNode.type) && isDataflowTargetType(targetNode.type)) {
      connectDataflowNodes(sourceNode.id, targetNode.id);
    }
  }, [connectDataflowNodes, connectPromptToAgent]);

  const saveScheduleNode = useCallback(async (nodeId) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node || node.type !== 'schedule' || node.data?.serverManaged) {
      return;
    }

    const title = (node.data.title || '').trim();
    const instruction = (node.data.instruction || '').trim();
    const expression = (node.data.expression || '').trim();

    if (!title || !instruction || !expression) {
      updateNodeData(nodeId, {
        saveError: 'Name, task, and schedule expression are required before saving.',
      });
      return;
    }

    setNodes((currentNodes) => currentNodes.map((entry) => (
      entry.id === nodeId
        ? {
          ...entry,
          data: {
            ...entry.data,
            isSaving: true,
            saveError: null,
            updatedAt: new Date().toISOString(),
          },
        }
        : entry
    )));

    try {
      const widget = await createSchedule({
        name: title,
        instruction,
        schedule_type: node.data.scheduleType,
        expression,
        timezone: node.data.timezone,
        enabled: node.data.enabled,
      });

      setNodes((currentNodes) => {
        const existingNode = currentNodes.find((entry) => entry.id === nodeId) || node;
        const remainingNodes = currentNodes.filter((entry) => entry.id !== nodeId);
        const serverNode = buildServerNode(
          widget,
          {
            ...existingNode,
            id: widget.id,
            data: {
              ...existingNode.data,
              expanded: existingNode.data.expanded,
            },
          },
          remainingNodes.length,
        );
        return [...remainingNodes, serverNode];
      });
    } catch (saveError) {
      setNodes((currentNodes) => currentNodes.map((entry) => (
        entry.id === nodeId
          ? {
            ...entry,
            data: {
              ...entry.data,
              isSaving: false,
              saveError: saveError.message,
              updatedAt: new Date().toISOString(),
            },
          }
          : entry
      )));
    }
  }, [createSchedule, updateNodeData]);

  const saveEventNode = useCallback(async (nodeId) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node || node.type !== 'event' || node.data?.serverManaged) {
      return;
    }

    const title = (node.data.title || '').trim();
    const eventType = (node.data.eventType || '').trim();

    if (!title || !eventType) {
      updateNodeData(nodeId, {
        saveError: 'Name and event type are required before saving.',
      });
      return;
    }

    setNodes((currentNodes) => currentNodes.map((entry) => (
      entry.id === nodeId
        ? {
          ...entry,
          data: {
            ...entry.data,
            isSaving: true,
            saveError: null,
            updatedAt: new Date().toISOString(),
          },
        }
        : entry
    )));

    try {
      const widget = await createEvent({
        name: title,
        event_type: eventType,
        enabled: node.data.enabled,
      });

      setNodes((currentNodes) => {
        const existingNode = currentNodes.find((entry) => entry.id === nodeId) || node;
        const remainingNodes = currentNodes.filter((entry) => entry.id !== nodeId);
        const serverNode = buildServerNode(
          widget,
          {
            ...existingNode,
            id: widget.id,
            data: {
              ...existingNode.data,
              expanded: existingNode.data.expanded,
            },
          },
          remainingNodes.length,
        );
        return [...remainingNodes, serverNode];
      });
    } catch (saveError) {
      setNodes((currentNodes) => currentNodes.map((entry) => (
        entry.id === nodeId
          ? {
            ...entry,
            data: {
              ...entry.data,
              isSaving: false,
              saveError: saveError.message,
              updatedAt: new Date().toISOString(),
            },
          }
          : entry
      )));
    }
  }, [createEvent, updateNodeData]);

  const removeNodeIdsLocally = useCallback((nodeIds) => {
    if (!Array.isArray(nodeIds) || nodeIds.length === 0) {
      return;
    }

    nodeIds.forEach(clearQueuedWidgetPatch);
    const idSet = new Set(nodeIds);
    setNodes((currentNodes) => {
      const nextNodes = currentNodes.filter((node) => !idSet.has(node.id));
      latestNodesRef.current = nextNodes;
      return nextNodes;
    });
    setEdges((currentEdges) => {
      const nextEdges = currentEdges.filter(
        (edge) => !idSet.has(edge.source) && !idSet.has(edge.target)
      );
      latestEdgesRef.current = nextEdges;
      return nextEdges;
    });
  }, [clearQueuedWidgetPatch]);

  const removeNodes = useCallback(async (nodeIds) => {
    const nodeIdSet = new Set(nodeIds);
    const targets = latestNodesRef.current.filter((node) => nodeIdSet.has(node.id));
    const removableIds = [];

    for (const node of targets) {
      try {
        if (await deleteBackendManagedNode(node)) {
          removableIds.push(node.id);
          continue;
        }
      } catch (deleteError) {
        const message = deleteError instanceof Error ? deleteError.message : String(deleteError);
        setNodeSaveError(node.id, message);
        continue;
      }

      if (!node.data?.serverManaged) {
        removableIds.push(node.id);
      }
    }

    removeNodeIdsLocally(removableIds);
  }, [deleteBackendManagedNode, removeNodeIdsLocally, setNodeSaveError]);

  // Remove a server-managed widget card (e.g. an agent) from the canvas without
  // touching the underlying registration. The id is remembered so the card is
  // not re-added on the next widget sync or reload.
  const dismissWidget = useCallback((nodeId) => {
    if (!nodeId) {
      return;
    }
    dismissedWidgetIdsRef.current.add(nodeId);
    persistDismissedWidgetIds(dismissedWidgetIdsRef.current);
    removeNodeIdsLocally([nodeId]);
  }, [removeNodeIdsLocally]);

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

  const refreshWidgetNode = useCallback(async (nodeId) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node || !isAgentNodeType(node.type)) {
      return { ok: false, error: 'Only agent and MCP widgets support refresh.' };
    }

    updateNodeData(nodeId, {
      isRefreshing: true,
      refreshError: null,
    });

    try {
      const refreshedWidget = await refreshWidget(nodeId);
      mergeSingleWidget(refreshedWidget, {
        isRefreshing: false,
        refreshError: null,
        availabilityStatus: refreshedWidget?.data?.availability_status || 'online',
      });
      return { ok: true };
    } catch (refreshError) {
      const message = refreshError?.message || String(refreshError);
      updateNodeData(nodeId, {
        isRefreshing: false,
        refreshError: message,
        availabilityStatus: deriveAvailabilityStatusFromRefreshError(node.data?.agentStatus, message),
      });
      return { ok: false, error: message };
    }
  }, [mergeSingleWidget, refreshWidget, updateNodeData]);

  const runConnectedTargets = useCallback(async (sourceNodeId, options = {}) => {
    const sourceNode = latestNodesRef.current.find((entry) => entry.id === sourceNodeId);
    if (!sourceNode) {
      return [];
    }

    const outputText = typeof options.outputText === 'string'
      ? options.outputText
      : getNodeOutputText(sourceNode);

    const targets = getOutgoingDataflowTargets(sourceNodeId, latestNodesRef.current, latestEdgesRef.current);
    if (targets.length === 0) {
      return [];
    }

    const visitedNodeIds = new Set(options.visitedNodeIds || []);
    visitedNodeIds.add(sourceNodeId);

    const results = [];
    for (const targetNode of targets) {
      if (visitedNodeIds.has(targetNode.id)) {
        results.push({
          ok: false,
          error: `Cycle detected for ${formatWidgetLabel(targetNode)}.`,
        });
        continue;
      }

      const runNodeByType = runNodeByTypeRef.current;
      if (!runNodeByType) {
        break;
      }

      results.push(await runNodeByType(targetNode.id, {
        upstreamOutput: outputText,
        upstreamSourceNodeId: sourceNodeId,
        visitedNodeIds: [...visitedNodeIds],
        executionContext: options.executionContext,
      }));
    }

    return results;
  }, []);

  const runPromptNode = useCallback(async (nodeId, options = {}) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node) {
      return { ok: false, error: 'Prompt node not found.' };
    }

    const visitedNodeIds = new Set(options.visitedNodeIds || []);
    if (visitedNodeIds.has(nodeId)) {
      return { ok: false, error: `Cycle detected for ${formatWidgetLabel(node)}.` };
    }

    visitedNodeIds.add(nodeId);

    const connectedTarget = getConnectedAgentTarget(nodeId, latestNodesRef.current, latestEdgesRef.current);
    const prompt = buildPromptInvocation(node.data, connectedTarget?.agentName);
    const executionInput = resolveNodeExecutionInput(
      nodeId,
      latestNodesRef.current,
      latestEdgesRef.current,
      options
    );
    if (!prompt) {
      updateNodeData(nodeId, {
        status: 'failed',
        summaryState: 'Enter a prompt before running it.',
      });
      return { ok: false, error: 'Enter a prompt before running it.' };
    }

    updateNodeData(nodeId, { status: 'running', summaryState: '' });
    const executionContext = buildExecutionContext(
      nodeId,
      options.executionContext?.invocationSource || 'workspace.run_prompt',
      options.executionContext,
    );

    try {
      const result = await runAction({
        widget_id: nodeId,
        action_type: 'run_prompt',
        prompt,
        input: executionInput || undefined,
        role: node.data.role,
        title: node.data.title,
        thread_id: executionContext.threadId,
        invocation_type: executionContext.invocationType,
        invocation_source: executionContext.invocationSource,
        root_widget_id: executionContext.rootWidgetId,
      });
      const outputText = getActionText(result);
      updateNodeData(nodeId, {
        status: 'success',
        targetAgent: getPromptTargetAgent(node.data, connectedTarget?.agentName),
        lastThreadId: result.thread_id || executionContext.threadId || node.data.lastThreadId,
        summaryState: outputText,
        lastOutput: outputText,
      });
      const downstreamResults = await runConnectedTargets(nodeId, {
        visitedNodeIds: [...visitedNodeIds],
        outputText,
        executionContext,
      });
      return {
        ok: true,
        message: outputText || 'Prompt completed.',
        output: outputText,
        downstreamResults,
      };
    } catch (runError) {
      updateNodeData(nodeId, {
        status: 'failed',
        summaryState: runError.message,
      });
      return { ok: false, error: runError.message };
    }
  }, [runAction, runConnectedTargets, updateNodeData]);

  const generateFromPromptNode = useCallback(async (nodeId) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node) {
      return;
    }

    const connectedTarget = getConnectedAgentTarget(nodeId, latestNodesRef.current, latestEdgesRef.current);
    const prompt = buildPromptInvocation(node.data, connectedTarget?.agentName);
    const executionInput = resolveNodeExecutionInput(
      nodeId,
      latestNodesRef.current,
      latestEdgesRef.current
    );
    if (!prompt) {
      updateNodeData(nodeId, {
        status: 'failed',
        summaryState: 'Enter a prompt before generating code.',
      });
      return;
    }

    updateNodeData(nodeId, { status: 'running', summaryState: '' });
    const executionContext = buildExecutionContext(nodeId, 'workspace.generate_prompt');

    try {
      const result = await generateAction({
        widget_id: nodeId,
        action_type: 'generate_code',
        prompt,
        input: executionInput || undefined,
        role: node.data.role,
        title: node.data.title,
        thread_id: executionContext.threadId,
        invocation_type: executionContext.invocationType,
        invocation_source: executionContext.invocationSource,
        root_widget_id: executionContext.rootWidgetId,
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
        status: 'success',
        targetAgent: getPromptTargetAgent(node.data, connectedTarget?.agentName),
        lastThreadId: result.thread_id || executionContext.threadId || node.data.lastThreadId,
        summaryState: generatedContent ? 'Code generated via backend.' : 'Generation completed.',
        linkedOutputIds: [...(node.data.linkedOutputIds || []), codeNode.id],
      });
    } catch (generateError) {
      updateNodeData(nodeId, {
        status: 'failed',
        summaryState: generateError.message,
      });
    }
  }, [generateAction, updateNodeData, upsertNode]);

  const runCodeNode = useCallback(async (nodeId, options = {}) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node) {
      return { ok: false, error: 'Code node not found.' };
    }

    const visitedNodeIds = new Set(options.visitedNodeIds || []);
    if (visitedNodeIds.has(nodeId)) {
      return { ok: false, error: `Cycle detected for ${formatWidgetLabel(node)}.` };
    }

    visitedNodeIds.add(nodeId);
    const executionInput = resolveNodeExecutionInput(
      nodeId,
      latestNodesRef.current,
      latestEdgesRef.current,
      options
    );

    updateNodeData(nodeId, { status: 'running' });
    const executionContext = buildExecutionContext(
      nodeId,
      options.executionContext?.invocationSource || 'workspace.run_code',
      options.executionContext,
    );

    try {
      const result = await runAction({
        widget_id: nodeId,
        action_type: 'run_code',
        language: node.data.language,
        code: node.data.content,
        input: executionInput || undefined,
        title: node.data.title,
        thread_id: executionContext.threadId,
        invocation_type: executionContext.invocationType,
        invocation_source: executionContext.invocationSource,
        root_widget_id: executionContext.rootWidgetId,
      });
      const outputText = getActionText(result);
      updateNodeData(nodeId, {
        status: 'completed',
        lastThreadId: result.thread_id || executionContext.threadId || node.data.lastThreadId,
        outputRefs: [outputText].filter(Boolean),
        lastOutput: outputText,
      });
      const downstreamResults = await runConnectedTargets(nodeId, {
        visitedNodeIds: [...visitedNodeIds],
        outputText,
        executionContext,
      });
      return {
        ok: true,
        message: outputText || 'Code executed.',
        output: outputText,
        downstreamResults,
      };
    } catch (runError) {
      updateNodeData(nodeId, {
        status: 'error',
        outputRefs: [runError.message],
      });
      return { ok: false, error: runError.message };
    }
  }, [runAction, runConnectedTargets, updateNodeData]);

  const runUserNode = useCallback(async (nodeId, options = {}) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node || node.type !== 'user') {
      return { ok: false, error: 'User node not found.' };
    }

    const visitedNodeIds = new Set(options.visitedNodeIds || []);
    if (visitedNodeIds.has(nodeId)) {
      return { ok: false, error: `Cycle detected for ${formatWidgetLabel(node)}.` };
    }

    visitedNodeIds.add(nodeId);
    const executionInput = resolveNodeExecutionInput(
      nodeId,
      latestNodesRef.current,
      latestEdgesRef.current,
      options
    );
    const instruction = buildUserMessageInstruction(node.data, executionInput);
    if (instruction.error) {
      updateNodeData(nodeId, {
        status: 'failed',
        summaryState: instruction.error,
      });
      return { ok: false, error: instruction.error };
    }

    updateNodeData(nodeId, { status: 'running', summaryState: '' });
    const executionContext = buildExecutionContext(
      nodeId,
      options.executionContext?.invocationSource || 'workspace.run_user',
      options.executionContext,
    );

    try {
      const result = await runAction({
        widget_id: nodeId,
        prompt: instruction.prompt,
        broadcast_response: false,
        thread_id: executionContext.threadId,
        invocation_type: executionContext.invocationType,
        invocation_source: executionContext.invocationSource,
        root_widget_id: executionContext.rootWidgetId,
      });
      const responseText = getActionText(result);
      const summary = responseText && responseText !== 'sent'
        ? `Sent via ${instruction.delivery}. ${responseText}`
        : `Sent via ${instruction.delivery}.`;

      updateNodeData(nodeId, {
        status: 'success',
        lastSentAt: new Date().toISOString(),
        summaryState: summary,
        lastOutput: instruction.previewMessage,
      });
      const downstreamResults = await runConnectedTargets(nodeId, {
        visitedNodeIds: [...visitedNodeIds],
        outputText: instruction.previewMessage,
        executionContext,
      });

      return {
        ok: true,
        delivery: instruction.delivery,
        previewTitle: instruction.previewTitle,
        previewMessage: instruction.previewMessage,
        level: instruction.level,
        output: instruction.previewMessage,
        downstreamResults,
      };
    } catch (runError) {
      updateNodeData(nodeId, {
        status: 'failed',
        summaryState: runError.message,
      });
      return { ok: false, error: runError.message };
    }
  }, [runAction, runConnectedTargets, updateNodeData]);

  const runScheduleNode = useCallback(async (nodeId, options = {}) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node || node.type !== 'schedule') {
      return { ok: false, error: 'Schedule node not found.' };
    }

    const visitedNodeIds = new Set(options.visitedNodeIds || []);
    if (visitedNodeIds.has(nodeId)) {
      return { ok: false, error: `Cycle detected for ${formatWidgetLabel(node)}.` };
    }

    visitedNodeIds.add(nodeId);
    if (!node.data?.serverManaged) {
      updateNodeData(nodeId, {
        lastRunStatus: 'error',
        summaryState: 'Save the schedule before running it.',
      });
      return { ok: false, error: 'Save the schedule before running it.' };
    }

    const executionInput = resolveNodeExecutionInput(
      nodeId,
      latestNodesRef.current,
      latestEdgesRef.current,
      options
    );
    updateNodeData(nodeId, { lastRunStatus: 'running' });
    const executionContext = buildExecutionContext(
      nodeId,
      options.executionContext?.invocationSource || 'workspace.run_schedule',
      options.executionContext,
    );
    try {
      const result = await runAction({
        widget_id: nodeId,
        action_type: 'run_schedule',
        thread_id: executionContext.threadId,
        invocation_type: executionContext.invocationType,
        invocation_source: executionContext.invocationSource,
        root_widget_id: executionContext.rootWidgetId,
        input: executionInput || undefined,
      });
      const outputText = getActionText(result);
      updateNodeData(nodeId, {
        lastRunStatus: 'completed',
        lastRunAt: new Date().toISOString(),
        lastThreadId: result.thread_id || executionContext.threadId || node.data.lastThreadId,
        summaryState: outputText,
        lastOutput: outputText,
      });
      const downstreamResults = await runConnectedTargets(nodeId, {
        visitedNodeIds: [...visitedNodeIds],
        outputText,
        executionContext,
      });
      return {
        ok: true,
        message: outputText || 'Schedule completed.',
        output: outputText,
        downstreamResults,
      };
    } catch (runError) {
      updateNodeData(nodeId, {
        lastRunStatus: 'error',
        summaryState: runError.message,
      });
      return { ok: false, error: runError.message };
    }
  }, [runAction, runConnectedTargets, updateNodeData]);

  const runEventNode = useCallback(async (nodeId) => {
    const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
    if (!node || node.type !== 'event') {
      return { ok: false, error: 'Event node not found.' };
    }

    const eventPayload = buildManualEventPayload(node);
    const eventOutput = buildEventOutputText(eventPayload);
    const linkedTargets = getOutgoingDataflowTargets(nodeId, latestNodesRef.current, latestEdgesRef.current);
    const initialSummary = linkedTargets.length > 0
      ? `Running ${linkedTargets.length} linked widget${linkedTargets.length === 1 ? '' : 's'}.`
      : `Manual trigger fired for ${eventPayload.event_type}.`;
    const initialLogs = appendEventLog(node.data.logs, initialSummary, eventPayload.triggered_at);

    updateNodeData(nodeId, {
      triggerCount: (node.data.triggerCount || 0) + 1,
      lastTriggeredAt: eventPayload.triggered_at,
      lastPayload: eventPayload,
      lastRunStatus: linkedTargets.length > 0 ? 'running' : 'completed',
      summaryState: initialSummary,
      logs: initialLogs,
      lastOutput: eventOutput,
    });

    if (linkedTargets.length === 0) {
      return { ok: true, triggered: 0, output: eventOutput };
    }

    const executionContext = buildExecutionContext(nodeId, 'workspace.event_chain');
    const results = await runConnectedTargets(nodeId, {
      visitedNodeIds: [nodeId],
      outputText: eventOutput,
      executionContext,
    });

    const successfulCount = results.filter((result) => result?.ok).length;
    const failedTargets = linkedTargets.filter((_, index) => !results[index]?.ok);
    const targetLabel = linkedTargets.map((targetNode) => formatWidgetLabel(targetNode)).join(', ');
    const failedLabel = failedTargets.map((targetNode) => formatWidgetLabel(targetNode)).join(', ');
    const summary = failedTargets.length > 0
      ? `Manual trigger ran ${successfulCount}/${linkedTargets.length} linked widgets. Failed: ${failedLabel}.`
      : `Manual trigger ran ${linkedTargets.length} linked widget${linkedTargets.length === 1 ? '' : 's'}: ${targetLabel}.`;

    updateNodeData(nodeId, {
      lastRunStatus: failedTargets.length > 0 ? 'error' : 'completed',
      summaryState: summary,
      logs: appendEventLog(
        initialLogs,
        summary,
        new Date().toISOString(),
        failedTargets.length > 0 ? 'error' : 'info'
      ),
      lastOutput: eventOutput,
    });

    return {
      ok: failedTargets.length === 0,
      triggered: linkedTargets.length,
      successfulCount,
      failedCount: failedTargets.length,
      output: eventOutput,
    };
  }, [runConnectedTargets, updateNodeData]);

  useEffect(() => {
    runNodeByTypeRef.current = async (nodeId, options = {}) => {
      const node = latestNodesRef.current.find((entry) => entry.id === nodeId);
      if (!node) {
        return { ok: false, error: 'Widget not found.' };
      }

      if (node.type === 'prompt') {
        return runPromptNode(nodeId, options);
      }
      if (node.type === 'code') {
        return runCodeNode(nodeId, options);
      }
      if (node.type === 'user') {
        return runUserNode(nodeId, options);
      }
      if (node.type === 'schedule') {
        return runScheduleNode(nodeId, options);
      }

      return {
        ok: false,
        error: `Unsupported linked widget type: ${node.type}`,
      };
    };

    return () => {
      runNodeByTypeRef.current = null;
    };
  }, [runCodeNode, runPromptNode, runScheduleNode, runUserNode]);

  const graphValue = useMemo(() => ({
    nodes,
    edges,
    viewport,
    viewportVersion,
    loading,
    error,
  }), [nodes, edges, viewport, viewportVersion, loading, error]);

  const actionsValue = useMemo(() => ({
    captureViewport,
    onNodesChange,
    onEdgesChange,
    onConnect,
    addNode,
    addPromptFromLibrary,
    updateNodeData,
    connectPromptToAgent,
    replaceGraph,
    removeNodes,
    dismissWidget,
    removeEdges,
    duplicateNode,
    bringToFront,
    sendToBack,
    toggleExpanded,
    refreshWidgetNode,
    refreshWorkspace,
    syncWidgets,
    runPromptNode,
    generateFromPromptNode,
    runCodeNode,
    runUserNode,
    runScheduleNode,
    saveScheduleNode,
    saveEventNode,
    runEventNode,
  }), [
    captureViewport,
    onNodesChange,
    onEdgesChange,
    onConnect,
    addNode,
    addPromptFromLibrary,
    updateNodeData,
    connectPromptToAgent,
    replaceGraph,
    removeNodes,
    dismissWidget,
    removeEdges,
    duplicateNode,
    bringToFront,
    sendToBack,
    toggleExpanded,
    refreshWidgetNode,
    refreshWorkspace,
    syncWidgets,
    runPromptNode,
    generateFromPromptNode,
    runCodeNode,
    runUserNode,
    runScheduleNode,
    saveScheduleNode,
    saveEventNode,
    runEventNode,
  ]);

  const metaValue = useMemo(() => ({
    agentOptions,
    getWidgetSchema,
    getWidgetTabs,
    getWidgetDefaultTab,
    getWidgetHelpText,
  }), [agentOptions, getWidgetSchema, getWidgetTabs, getWidgetDefaultTab, getWidgetHelpText]);

  return (
    <WorkspaceGraphContext.Provider value={graphValue}>
      <WorkspaceActionsContext.Provider value={actionsValue}>
        <WorkspaceMetaContext.Provider value={metaValue}>
          {children}
        </WorkspaceMetaContext.Provider>
      </WorkspaceActionsContext.Provider>
    </WorkspaceGraphContext.Provider>
  );
}

export function useWorkspaceGraph() {
  const context = useContext(WorkspaceGraphContext);
  if (!context) {
    throw new Error('useWorkspaceGraph must be used within a WorkspaceProvider');
  }
  return context;
}

export function useWorkspaceActions() {
  const context = useContext(WorkspaceActionsContext);
  if (!context) {
    throw new Error('useWorkspaceActions must be used within a WorkspaceProvider');
  }
  return context;
}

export function useWorkspaceMeta() {
  const context = useContext(WorkspaceMetaContext);
  if (!context) {
    throw new Error('useWorkspaceMeta must be used within a WorkspaceProvider');
  }
  return context;
}
