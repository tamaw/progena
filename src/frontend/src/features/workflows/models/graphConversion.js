import { cloneValue } from './values';
import { nodeOutputPorts, nodeInputPorts } from './schema';
import { DEFAULT_NODE_SIZES, DOCK_NODE_DEFAULTS, resolveNodeSizes, DEFAULT_EDGE_OPTIONS, DEFAULT_VIEWPORT } from './nodePalette';

export function createPaletteNode(kind, nodeId, position, documentTypes, callbacks, preferenceTimezone) {
  const sizes = DEFAULT_NODE_SIZES[kind] || DEFAULT_NODE_SIZES.agent;
  const defaultData = DOCK_NODE_DEFAULTS[kind](documentTypes, preferenceTimezone);
  const inputPortCount = nodeInputPorts(kind, defaultData.inputSchema).length;
  const outputPortCount = nodeOutputPorts(kind, defaultData.outputSchema).length;
  const maxPorts = Math.max(inputPortCount, outputPortCount);
  const height = Math.max(sizes.data.height, maxPorts > 0 ? 69 + (maxPorts - 1) * 28 : sizes.data.height);
  return {
    id: nodeId,
    type: kind,
    width: sizes.data.width,
    height,
    style: { width: sizes.data.width, height },
    position,
    data: {
      kind,
      ...defaultData,
      currentNodeId: null,
      completedNodeIds: [],
      expanded: false,
      ...callbacks,
      canDelete: true,
    },
  };
}

function workflowRunState(latestRun) {
  return {
    currentNodeId: latestRun?.status === 'running' ? latestRun.current_node_id || null : null,
    completedNodeIds: latestRun?.completed_node_ids || [],
  };
}

function buildNodeData(kind, node, callbacks, latestRun) {
  const shared = {
    expanded: false,
    onToggleExpanded: callbacks.onToggleExpanded,
    onOpenOptions: callbacks.onOpenOptions,
    onResize: callbacks.onResize,
    onUnpack: callbacks.onUnpack,
  };
  const runState = workflowRunState(latestRun);
  if (kind === 'event') {
    return {
      ...shared,
      kind,
      title: node.title || 'Document event',
      documentType: node.document_type || '',
      key: node.key || null,
      operations: Array.isArray(node.operations) ? [...node.operations] : [],
      changedFields: Array.isArray(node.changed_fields) ? [...node.changed_fields] : [],
      match: cloneValue(node.match) || {},
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: false,
    };
  }
  if (kind === 'cron') {
    return {
      ...shared,
      kind,
      title: node.title || 'Cron',
      expression: node.expression || '*/5 * * * *',
      timezone: node.timezone || '',
      overlap: node.overlap || 'skip',
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: false,
    };
  }
  if (kind === 'manual') {
    return {
      ...shared,
      kind,
      title: node.title || 'Manual trigger',
      outputSchema: cloneValue(node.output_schema) || { type: 'object', properties: {} },
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'agent') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Agent',
      instruction: node.instruction ?? null,
      template: node.template ?? null,
      subagent: node.subagent ?? null,
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'extract') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Extract',
      instruction: node.instruction ?? null,
      template: node.template ?? null,
      schema: node.schema ?? null,
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'format') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Format',
      template: node.template || '',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }

  if (kind === 'data') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Data',
      value: cloneValue(node.value) ?? null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'condition') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Condition',
      expression: node.expression || '',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || { type: 'string', enum: ['true', 'false'] },
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'approval') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Approval',
      message: node.message || '',
      approver: node.approver || 'any',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || { type: 'string', enum: ['approved', 'rejected'] },
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'document_read') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Read document',
      documentType: node.document_type || '',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'document_query') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Query documents',
      documentType: node.document_type || '',
      filters: Array.isArray(node.filters) ? cloneValue(node.filters) : [],
      orderBy: node.order_by || null,
      order: node.order || 'asc',
      limit: Number.isFinite(node.limit) ? node.limit : 50,
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'document_write' || kind === 'document_create' || kind === 'document_delete') {
    const labels = {
      document_write: 'Write document',
      document_create: 'Create document',
      document_delete: 'Delete document',
    };
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || labels[kind],
      documentType: node.document_type || '',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'routine') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Routine',
      routine: node.routine || '',
      inputSchema: cloneValue(node.input_schema) || null,
      outputSchema: cloneValue(node.output_schema) || null,
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  if (kind === 'unpack') {
    return {
      ...shared,
      ...runState,
      kind,
      title: node.title || 'Unpack',
      showAllFields: Boolean(node.show_all_fields),
      inputSchema: cloneValue(node.input_schema) || { type: 'object', required: ['value'], properties: { value: { type: 'object' } } },
      outputSchema: cloneValue(node.output_schema) || { type: 'object', properties: {} },
      onChange: callbacks.onNodeChange,
      onDelete: callbacks.onDeleteNode,
      canDelete: true,
    };
  }
  return { ...shared, ...runState, kind, title: node.title || 'Node', onChange: callbacks.onNodeChange, onDelete: callbacks.onDeleteNode, canDelete: true };
}

export function workflowToFlow(definition, callbacks, latestRun) {
  const uiNodes = definition?.ui?.nodes || {};
  const graphNodes = Object.entries(definition?.graph?.nodes || {}).map(([nodeId, node]) => {
    const kind = DEFAULT_NODE_SIZES[node.kind] ? node.kind : 'agent';
    const uiNode = uiNodes[nodeId] || {};
    const sizes = resolveNodeSizes(kind, uiNode.size, node.input_schema, node.output_schema);
    const position = uiNode.position || { x: 456, y: 160 };
    const nodeData = kind === 'event'
      ? {
          ...node,
          documentType: node.document_type,
          key: node.key,
          operations: node.operations,
          changedFields: node.changed_fields,
          match: node.match,
          outputSchema: cloneValue(node.output_schema) || null,
        }
      : kind === 'cron'
        ? {
            ...node,
            expression: node.expression,
            timezone: node.timezone,
            overlap: node.overlap,
            outputSchema: cloneValue(node.output_schema) || null,
          }
        : kind === 'manual'
          ? {
              ...node,
              outputSchema: cloneValue(node.output_schema) || { type: 'object', properties: {} },
            }
          : kind === 'agent'
        ? {
          ...node,
          instruction: node.instruction ?? null,
          template: node.template ?? null,
          subagent: node.subagent ?? null,
        }
      : kind === 'extract'
        ? {
          ...node,
          instruction: node.instruction ?? null,
          template: node.template ?? null,
          schema: node.schema ?? null,
        }
        : kind === 'format'
        ? {
          ...node,
          template: node.template || '',
        }
      : kind === 'document_read' || kind === 'document_query' || kind === 'document_write'
        ? { ...node, documentType: node.document_type }
        : kind === 'unpack'
        ? { ...node, show_all_fields: Boolean(uiNode.show_all_fields) }
        : node;
    const data = buildNodeData(kind, nodeData, callbacks, latestRun);
    // A stored size is a user resize: keep it in node data so serializing and
    // Unpack auto-sizing treat the node as customized.
    if (uiNode.size) {
      data.dataSize = sizes.data;
      data.settingsSize = sizes.settings;
    }
    return {
      id: nodeId,
      type: kind,
      position,
      width: sizes.data.width,
      height: sizes.data.height,
      style: { width: sizes.data.width, height: sizes.data.height },
      data,
    };
  });
  // A typed value connection is also the execution dependency. There are no
  // separate control edges or connector handles.
  const graphEdges = (definition?.graph?.edges || []).map((edge) => {
    const fromPort = edge.from.port || 'default';
    const toPort = edge.to.port || 'default';
    return {
      id: `edge_${edge.from.node}_${fromPort}_${edge.to.node}_${toPort}`,
      source: edge.from.node,
      sourceHandle: `out:${fromPort}`,
      target: edge.to.node,
      targetHandle: `in:${toPort}`,
      ...DEFAULT_EDGE_OPTIONS,
      className: 'workflow-edge--data',
    };
  });
  return {
    nodes: graphNodes,
    edges: graphEdges,
    viewport: definition?.ui?.viewport || DEFAULT_VIEWPORT,
  };
}

function isTriggerKind(kind) {
  return kind === 'event' || kind === 'cron' || kind === 'manual';
}

export function applyRunStateToNodes(nodes, latestRun) {
  const { currentNodeId, completedNodeIds } = workflowRunState(latestRun);
  return nodes.map((node) => {
    const nextData = isTriggerKind(node.type)
      ? node.data
      : { ...node.data, currentNodeId, completedNodeIds };
    if (nextData === node.data) {
      return node;
    }
    return { ...node, data: nextData };
  });
}

function edgeEndpointFromHandle(handle) {
  return handle && (handle.startsWith('out:') || handle.startsWith('in:'))
    ? handle.slice(handle.indexOf(':') + 1)
    : 'default';
}

export function flowToWorkflow(baseWorkflow, nodes, edges, viewport) {
  const graphNodes = {};
  const uiNodes = {};
  nodes.forEach((node) => {
    if (node.type === 'event') {
      graphNodes[node.id] = {
        kind: 'event',
        title: String(node.data?.title || 'Document event'),
        document_type: node.data?.documentType || '',
        key: node.data?.key || null,
        operations: Array.isArray(node.data?.operations) ? [...node.data.operations] : [],
        changed_fields: Array.isArray(node.data?.changedFields) ? [...node.data.changedFields] : [],
        match: cloneValue(node.data?.match) || {},
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (node.type === 'cron') {
      const timezone = typeof node.data?.timezone === 'string' ? node.data.timezone.trim() : '';
      graphNodes[node.id] = {
        kind: 'cron',
        title: String(node.data?.title || 'Cron'),
        expression: node.data?.expression || '*/5 * * * *',
        ...(timezone ? { timezone } : {}),
        overlap: node.data?.overlap || 'skip',
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (node.type === 'manual') {
      graphNodes[node.id] = {
        kind: 'manual',
        title: String(node.data?.title || 'Manual trigger'),
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object', properties: {} },
      };
    } else if (node.type === 'document_read') {
      graphNodes[node.id] = {
        kind: 'document_read',
        title: String(node.data?.title || 'Read document'),
        document_type: node.data?.documentType || '',
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (node.type === 'document_query') {
      graphNodes[node.id] = {
        kind: 'document_query',
        title: String(node.data?.title || 'Query documents'),
        document_type: node.data?.documentType || '',
        filters: Array.isArray(node.data?.filters) ? cloneValue(node.data.filters) : [],
        order_by: node.data?.orderBy || null,
        order: node.data?.order || 'asc',
        limit: Number.isFinite(node.data?.limit) ? node.data.limit : 50,
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (['document_write', 'document_create', 'document_delete'].includes(node.type)) {
      const titles = {
        document_write: 'Write document',
        document_create: 'Create document',
        document_delete: 'Delete document',
      };
      graphNodes[node.id] = {
        kind: node.type,
        title: String(node.data?.title || titles[node.type]),
        document_type: node.data?.documentType || '',
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (node.type === 'routine') {
      graphNodes[node.id] = {
        kind: 'routine',
        title: String(node.data?.title || 'Routine'),
        routine: node.data?.routine || '',
        input_schema: cloneValue(node.data?.inputSchema) || null,
        output_schema: cloneValue(node.data?.outputSchema) || null,
      };
    } else if (node.type === 'data') {
      graphNodes[node.id] = {
        kind: 'data',
        title: String(node.data?.title || 'Data'),
        value: cloneValue(node.data?.value) ?? null,
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object' },
      };
    } else if (node.type === 'agent') {
      graphNodes[node.id] = {
        kind: 'agent',
        title: String(node.data?.title || 'Agent'),
        instruction: node.data?.instruction ?? null,
        template: node.data?.template ?? null,
        subagent: node.data?.subagent ?? null,
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object', required: ['message', 'thread_id'], properties: { message: { type: 'string' }, thread_id: { type: 'string' } } },
      };
    } else if (node.type === 'format') {
      graphNodes[node.id] = {
        kind: 'format',
        title: String(node.data?.title || 'Format'),
        template: node.data?.template || '',
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object', required: ['text'], properties: { text: { type: 'string' } } },
      };
    } else if (node.type === 'extract') {
      graphNodes[node.id] = {
        kind: 'extract',
        title: String(node.data?.title || 'Extract'),
        instruction: node.data?.instruction ?? null,
        template: node.data?.template ?? null,
        schema: node.data?.schema ?? null,
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object', required: ['result', 'thread_id'], properties: { result: {}, thread_id: { type: 'string' } } },
      };
    } else if (node.type === 'condition') {
      graphNodes[node.id] = {
        kind: 'condition',
        title: String(node.data?.title || 'Condition'),
        expression: String(node.data?.expression || ''),
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'string', enum: ['true', 'false'] },
      };
    } else if (node.type === 'approval') {
      graphNodes[node.id] = {
        kind: 'approval',
        title: String(node.data?.title || 'Approval'),
        message: String(node.data?.message || ''),
        approver: node.data?.approver === 'human' ? 'human' : 'any',
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'string', enum: ['approved', 'rejected'] },
      };
    } else if (node.type === 'unpack') {
      graphNodes[node.id] = {
        kind: 'unpack',
        title: String(node.data?.title || 'Unpack'),
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', required: ['value'], properties: { value: { type: 'object' } } },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object', properties: {} },
      };
    } else {
      graphNodes[node.id] = {
        kind: 'agent',
        title: String(node.data?.title || 'Agent'),
        instruction: node.data?.instruction ?? null,
        template: node.data?.template ?? null,
        subagent: node.data?.subagent ?? null,
        input_schema: cloneValue(node.data?.inputSchema) || { type: 'object', properties: {} },
        output_schema: cloneValue(node.data?.outputSchema) || { type: 'object', required: ['message', 'thread_id'], properties: { message: { type: 'string' }, thread_id: { type: 'string' } } },
      };
    }
    const hasCustomSize = node.data?.dataSize || node.data?.settingsSize;
    const sizes = resolveNodeSizes(node.type, hasCustomSize ? {
      data: node.data?.dataSize,
      settings: node.data?.settingsSize,
    } : null, graphNodes[node.id].input_schema, graphNodes[node.id].output_schema);
    uiNodes[node.id] = {
      position: {
        x: Math.round(node.position?.x || 0),
        y: Math.round(node.position?.y || 0),
      },
      ...(hasCustomSize ? {
        size: {
          data: { width: Math.round(sizes.data.width), height: Math.round(sizes.data.height) },
          settings: { width: Math.round(sizes.settings.width), height: Math.round(sizes.settings.height) },
        },
      } : {}),
      // View state only: whether an Unpack node lists every field.
      ...(node.type === 'unpack' && node.data?.showAllFields ? { show_all_fields: true } : {}),
    };
  });
  return {
    ...baseWorkflow,
    version: 3,
    graph: {
      nodes: graphNodes,
      edges: edges.map((edge) => ({
        from: { node: edge.source, port: edgeEndpointFromHandle(edge.sourceHandle) },
        to: { node: edge.target, port: edgeEndpointFromHandle(edge.targetHandle) },
      })),
    },
    ui: {
      ...(baseWorkflow.ui || {}),
      nodes: uiNodes,
      viewport: viewport || baseWorkflow.ui?.viewport || DEFAULT_VIEWPORT,
    },
  };
}
