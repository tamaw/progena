import { describe, expect, it, vi } from 'vitest';
import {
  NODE_MENU_CATEGORIES,
  canvasMenuAnchor,
  flowToWorkflow,
  schemaProperties,
  visibleNodePortProperties,
  workflowToFlow,
  deriveUnpackSchemas,
  recomputeUnpackSchemas,
  computeLostEdgesForUnpackChange,
  pruneLostUnpackEdges,
  isUnpackableSchema,
  parsePromptPlaceholders,
  derivePromptInputSchema,
  deriveFormatInputSchema,
  promptTemplatePatch,
  formatTemplatePatch,
  instructionPatch,
  createPaletteNode,
  nodeInputPorts,
  repoNodeSchemas,
  schemasAreCompatible,
} from './WorkflowsPage';
import { canvasToolSettings } from './workflowCanvasTools';

const AGENT_OUTPUT_SCHEMA = {
  type: 'object',
  required: ['title'],
  properties: { title: { type: 'string' } },
};

const KANBAN_EVENT_OUTPUT_SCHEMA = {
  type: 'object',
  properties: {
    event_id: { type: 'integer' },
    document_type: { type: 'string' },
    key: { type: 'string' },
    id: { type: 'string' },
    op: { type: 'string' },
    timestamp: { type: 'string' },
    changed_fields: { type: 'array', items: { type: 'string' } },
    before: { title: 'Kanban Card', type: 'object', properties: { title: { type: 'string' }, column_id: { type: 'string' } } },
    after: { title: 'Kanban Card', type: 'object', properties: { title: { type: 'string' }, column_id: { type: 'string' } } },
  },
};

const KANBAN_READ_SCHEMAS = {
  read_input_schema: { type: 'object', required: ['id'], properties: { id: { type: 'string' } } },
  read_output_schema: { type: 'object', required: ['found'], properties: { found: { type: 'boolean' }, key: { type: 'string' }, id: { type: 'string' }, document: { type: 'object' } } },
  query_output_schema: { type: 'object', required: ['items'], properties: { items: { type: 'array' }, keys: { type: 'array', items: { type: 'string' } }, count: { type: 'integer' } } },
  document_schema: { type: 'object', properties: { title: { type: 'string' }, column_id: { type: 'string' } } },
};

describe('workflowToFlow', () => {
  it('maps a connected property to typed data handles', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          event: { kind: 'event', title: 'Event', document_type: 'kanban_card', output_schema: KANBAN_EVENT_OUTPUT_SCHEMA },
          agent: { kind: 'agent', title: 'Agent', instruction: null, template: 'Go', subagent: null, input_schema: { type: 'object', properties: { document_type: { type: 'string' } } }, output_schema: { type: 'object', properties: {} } },
        },
        edges: [{ from: { node: 'event', port: 'document_type' }, to: { node: 'agent', port: 'document_type' } }],
      },
      ui: { nodes: {} },
    }, {}, null);

    expect(flow.edges.find((edge) => edge.source === 'event')).toMatchObject({
      sourceHandle: 'out:document_type',
      target: 'agent',
      targetHandle: 'in:document_type',
    });
  });

  it('maps condition results through ordinary typed ports', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          condition: { kind: 'condition', title: 'Condition', expression: 'ready', input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: { true: { type: 'boolean' } } } },
          agent: { kind: 'agent', title: 'Agent', instruction: null, template: 'Go', subagent: null, input_schema: { type: 'object', properties: { result: { type: 'boolean' } } }, output_schema: { type: 'object', properties: {} } },
        },
        edges: [{ from: { node: 'condition', port: 'true' }, to: { node: 'agent', port: 'result' } }],
      },
      ui: { nodes: {} },
    }, {}, null);

    expect(flow.edges.find((edge) => edge.source === 'condition')).toMatchObject({
      sourceHandle: 'out:true',
      target: 'agent',
      targetHandle: 'in:result',
    });
  });

  it('maps a typed edge without touching either schema', () => {
    const agentInputSchema = {
      type: 'object',
      required: ['title'],
      properties: { title: { type: 'string' } },
    };
    const workflow = {
      graph: {
        nodes: {
          source: { kind: 'agent', title: 'Source', instruction: null, template: 'Go', subagent: null, input_schema: { type: 'object', properties: {} }, output_schema: AGENT_OUTPUT_SCHEMA },
          target: { kind: 'agent', title: 'Target', instruction: null, template: 'Go', subagent: null, input_schema: agentInputSchema, output_schema: { type: 'object', properties: {} } },
        },
        edges: [{ from: { node: 'source', port: 'title' }, to: { node: 'target', port: 'title' } }],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const graphEdge = flow.edges.find((edge) => edge.source === 'source');
    expect(graphEdge).toMatchObject({
      source: 'source',
      sourceHandle: 'out:title',
      target: 'target',
      targetHandle: 'in:title',
    });

    const targetNode = flow.nodes.find((node) => node.id === 'target');
    expect(targetNode.data.inputSchema).toEqual(agentInputSchema);

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.target.input_schema).toEqual(agentInputSchema);
    expect(persisted.graph.edges).toContainEqual({
      from: { node: 'source', port: 'title' },
      to: { node: 'target', port: 'title' },
    });
  });

  it('maps a data node to a single "value" output port typed by its own schema', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          tool: {
            kind: 'data',
            title: 'GitHub tool',
            value: { server_id: 'github', tool_name: 'create_issue' },
            output_schema: { title: 'McpTool', type: 'object', properties: { server_id: { type: 'string' }, tool_name: { type: 'string' } } },
          },
        },
        edges: [],
      },
      ui: { nodes: {} },
    }, {}, null);

    const dataNode = flow.nodes.find((node) => node.id === 'tool');
    expect(dataNode.type).toBe('data');
    expect(dataNode.data.value).toEqual({ server_id: 'github', tool_name: 'create_issue' });
  });

  it('maps every V3 node kind to its own canvas type', () => {
    const workflow = {
      graph: {
        nodes: {
          event: { kind: 'event', title: 'Event', document_type: 'kanban_card', output_schema: KANBAN_EVENT_OUTPUT_SCHEMA },
          cron: { kind: 'cron', title: 'Cron', expression: '0 9 * * 1-5', timezone: 'Australia/Melbourne', overlap: 'skip', output_schema: { type: 'object', required: ['scheduled_for', 'fired_at', 'timezone'], properties: { scheduled_for: { type: 'string', format: 'date-time' }, fired_at: { type: 'string', format: 'date-time' }, timezone: { type: 'string' } } } },
          agent: { kind: 'agent', title: 'Agent', instruction: null, template: 'Go', subagent: null, input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
          data: { kind: 'data', title: 'Data', value: 1, output_schema: { type: 'number' } },
          extract: { kind: 'extract', title: 'Extract', instruction: null, template: '', schema: null, input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
          condition: { kind: 'condition', title: 'Condition', expression: 'ready', input_schema: { type: 'object', properties: {} } },
          approval: { kind: 'approval', title: 'Approval', message: 'Approve?', input_schema: { type: 'object', properties: {} } },
          document_read: { kind: 'document_read', title: 'Read', document_type: 'kanban_card', input_schema: KANBAN_READ_SCHEMAS.read_input_schema, output_schema: KANBAN_READ_SCHEMAS.read_output_schema },
          document_query: { kind: 'document_query', title: 'Query', document_type: 'kanban_card', filters: [{ field: 'title', op: 'eq' }], limit: 50, input_schema: { type: 'object', properties: { title: { type: 'string' } } }, output_schema: KANBAN_READ_SCHEMAS.query_output_schema },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const typesById = Object.fromEntries(flow.nodes.map((node) => [node.id, node.type]));
    expect(typesById).toEqual({
      event: 'event',
      cron: 'cron',
      agent: 'agent',
      data: 'data',
      extract: 'extract',
      condition: 'condition',
      approval: 'approval',
      document_read: 'document_read',
      document_query: 'document_query',
    });
  });

  it('renders a completed node as done even when the run retains its last node id', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          agent: { kind: 'agent', title: 'Notify', instruction: null, template: 'Notify the user.', subagent: null, input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
        },
        edges: [],
      },
      ui: { nodes: {} },
    }, {}, {
      status: 'completed',
      current_node_id: 'agent',
      completed_node_ids: ['agent'],
    });

    expect(flow.nodes.find((node) => node.id === 'agent')?.data).toMatchObject({
      currentNodeId: null,
      completedNodeIds: ['agent'],
    });
  });

  it('round-trips a graph-native document event with its filters', () => {
    const workflow = {
      graph: {
        nodes: {
          event_1: {
            kind: 'event',
            title: 'Card updated',
            document_type: 'kanban_card',
            key: null,
            operations: ['updated'],
            changed_fields: ['column_id'],
            match: { 'after.column_id': 'done' },
            output_schema: KANBAN_EVENT_OUTPUT_SCHEMA,
          },
          agent_1: { kind: 'agent', title: 'Notify', instruction: null, template: 'Notify the user.', subagent: null, input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
        },
        edges: [{ from: { node: 'event_1', port: 'default' }, to: { node: 'agent_1', port: 'default' } }],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const trigger = flow.nodes.find((node) => node.type === 'event');

    expect(trigger).toMatchObject({
      id: 'event_1',
      type: 'event',
      data: {
        title: 'Card updated',
        documentType: 'kanban_card',
        operations: ['updated'],
        changedFields: ['column_id'],
        match: { 'after.column_id': 'done' },
        canDelete: false,
      },
    });

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.version).toBe(3);
    expect(persisted.graph.nodes.event_1).toMatchObject({
      kind: 'event',
      document_type: 'kanban_card',
      operations: ['updated'],
      changed_fields: ['column_id'],
      match: { 'after.column_id': 'done' },
    });
  });

  it('round-trips a document_write node', () => {
    const workflow = {
      graph: {
        nodes: {
          write_1: {
            kind: 'document_write',
            title: 'Rename',
            document_type: 'session',
            input_schema: { type: 'object', required: ['id'], properties: { id: { type: 'string' }, title: { type: 'string' } } },
            output_schema: { type: 'object', required: ['found', 'title'], properties: { found: { type: 'boolean' }, title: { type: 'string' } } },
          },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };
    const flow = workflowToFlow(workflow, {}, null);
    const node = flow.nodes.find((n) => n.id === 'write_1');
    expect(node.type).toBe('document_write');
    expect(node.data.documentType).toBe('session');

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.write_1).toMatchObject({
      kind: 'document_write',
      document_type: 'session',
    });
  });

  it('round-trips a routine node with its routine id and ports', () => {
    const inputSchema = { type: 'object', required: ['message'], properties: { message: { type: 'string' } } };
    const workflow = {
      graph: {
        nodes: {
          run_1: { kind: 'routine', title: 'Triage', routine: 'triage', input_schema: inputSchema, output_schema: { type: 'object' } },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };
    const flow = workflowToFlow(workflow, {}, null);
    const node = flow.nodes.find((n) => n.id === 'run_1');
    expect(node.type).toBe('routine');
    expect(node.data.routine).toBe('triage');

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.run_1).toEqual({
      kind: 'routine',
      title: 'Triage',
      routine: 'triage',
      input_schema: inputSchema,
      output_schema: { type: 'object' },
    });
  });

  it('round-trips approval approvers and notify_on_pause', () => {
    const workflow = {
      notify_on_pause: true,
      graph: {
        nodes: {
          gate: { kind: 'approval', title: 'Gate', message: 'Ship?', approver: 'human' },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };
    const flow = workflowToFlow(workflow, {}, null);
    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);

    expect(persisted.notify_on_pause).toBe(true);
    expect(persisted.graph.nodes.gate.approver).toBe('human');
  });

  it('round-trips a cron node', () => {
    const workflow = {
      graph: {
        nodes: {
          cron_1: {
            kind: 'cron',
            title: 'Weekdays 9am',
            expression: '0 9 * * 1-5',
            timezone: 'Australia/Melbourne',
            overlap: 'skip',
            output_schema: {
              type: 'object',
              required: ['scheduled_for', 'fired_at', 'timezone'],
              properties: {
                scheduled_for: { type: 'string', format: 'date-time' },
                fired_at: { type: 'string', format: 'date-time' },
                timezone: { type: 'string' },
              },
            },
          },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const node = flow.nodes.find((n) => n.id === 'cron_1');
    expect(node.type).toBe('cron');
    expect(node.data.expression).toBe('0 9 * * 1-5');
    expect(node.data.timezone).toBe('Australia/Melbourne');
    expect(node.data.overlap).toBe('skip');
    expect(node.data.canDelete).toBe(false);

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.cron_1).toMatchObject({
      kind: 'cron',
      expression: '0 9 * * 1-5',
      timezone: 'Australia/Melbourne',
      overlap: 'skip',
    });
  });

  it('round-trips a document_read node', () => {
    const workflow = {
      graph: {
        nodes: {
          read_1: {
            kind: 'document_read',
            title: 'Read card',
            document_type: 'kanban_card',
            input_schema: KANBAN_READ_SCHEMAS.read_input_schema,
            output_schema: KANBAN_READ_SCHEMAS.read_output_schema,
          },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const node = flow.nodes.find((n) => n.id === 'read_1');
    expect(node.type).toBe('document_read');
    expect(node.data.documentType).toBe('kanban_card');

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.read_1).toMatchObject({
      kind: 'document_read',
      document_type: 'kanban_card',
    });
  });

  it('round-trips a document_query node', () => {
    const workflow = {
      graph: {
        nodes: {
          query_1: {
            kind: 'document_query',
            title: 'Query cards',
            document_type: 'kanban_card',
            filters: [{ field: 'title', op: 'eq' }],
            order_by: 'title',
            order: 'desc',
            limit: 25,
            input_schema: { type: 'object', properties: { title: { type: 'string' } } },
            output_schema: KANBAN_READ_SCHEMAS.query_output_schema,
          },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const node = flow.nodes.find((n) => n.id === 'query_1');
    expect(node.type).toBe('document_query');
    expect(node.data.documentType).toBe('kanban_card');
    expect(node.data.filters).toEqual([{ field: 'title', op: 'eq' }]);
    expect(node.data.orderBy).toBe('title');
    expect(node.data.order).toBe('desc');
    expect(node.data.limit).toBe(25);

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.query_1).toMatchObject({
      kind: 'document_query',
      document_type: 'kanban_card',
      filters: [{ field: 'title', op: 'eq' }],
      order_by: 'title',
      order: 'desc',
      limit: 25,
    });
  });

  it('round-trips document_create and document_delete nodes', () => {
    const workflow = {
      graph: {
        nodes: {
          create_1: { kind: 'document_create', title: 'Create card', document_type: 'kanban_card', input_schema: { type: 'object' }, output_schema: { type: 'object' } },
          delete_1: { kind: 'document_delete', title: 'Delete card', document_type: 'kanban_card', input_schema: { type: 'object' }, output_schema: { type: 'object' } },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };
    const flow = workflowToFlow(workflow, {}, null);
    expect(flow.nodes.map((node) => node.type).sort()).toEqual(['document_create', 'document_delete']);

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.create_1).toMatchObject({ kind: 'document_create', document_type: 'kanban_card' });
    expect(persisted.graph.nodes.delete_1).toMatchObject({ kind: 'document_delete', document_type: 'kanban_card' });
  });
  it('derives repo ports per action and round-trips the selected action', () => {
    expect(Object.keys(repoNodeSchemas('sync').inputSchema.properties)).toEqual(['repo_id']);
    expect(Object.keys(repoNodeSchemas('worktree').inputSchema.properties)).toEqual(['repo_id', 'branch', 'base']);
    expect(Object.keys(repoNodeSchemas('publish').inputSchema.properties)).toEqual(['repo_id', 'branch', 'title', 'body']);
    expect(Object.keys(repoNodeSchemas('remove_worktree').outputSchema.properties)).toEqual(['repo_id', 'branch', 'removed']);

    const workflow = {
      graph: {
        nodes: {
          publish_1: {
            kind: 'repo',
            title: 'Publish branch',
            action: 'publish',
            input_schema: repoNodeSchemas('publish').inputSchema,
            output_schema: repoNodeSchemas('publish').outputSchema,
          },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    expect(flow.nodes[0].data).toMatchObject({ action: 'publish', inputSchema: repoNodeSchemas('publish').inputSchema });

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.publish_1).toMatchObject({
      kind: 'repo',
      title: 'Publish branch',
      action: 'publish',
      input_schema: repoNodeSchemas('publish').inputSchema,
      output_schema: repoNodeSchemas('publish').outputSchema,
    });
  });
});

describe('workflow node menu', () => {
  it('exposes every workflow node kind and no capability-specific kinds', () => {
    const addableKinds = NODE_MENU_CATEGORIES.flatMap((category) => category.items).map((item) => item.kind);

    expect(addableKinds.sort()).toEqual(
      ['agent', 'approval', 'condition', 'cron', 'data', 'document_create', 'document_delete', 'document_query', 'document_read', 'document_write', 'event', 'extract', 'format', 'repo', 'routine', 'unpack'].sort(),
    );
  });
});

describe('node interface properties', () => {
  it('merges object properties from alternative schema branches', () => {
    expect(schemaProperties({
      oneOf: [
        {
          type: 'object',
          required: ['document_type'],
          properties: {
            document_type: { const: 'kanban_card' },
            card: { type: 'object' },
          },
        },
        {
          type: 'object',
          properties: {
            document_type: { const: 'kanban_card' },
            source: { type: 'string' },
          },
        },
      ],
    })).toEqual([
      expect.objectContaining({ name: 'document_type', required: true }),
      expect.objectContaining({ name: 'card', required: false }),
      expect.objectContaining({ name: 'source', required: false }),
    ]);
  });

  it('gives cron nodes no input ports', () => {
    expect(nodeInputPorts('cron', { type: 'object', properties: { a: { type: 'string' } } })).toEqual([]);
  });

  it('exposes an event node\'s output properties directly, with no payload envelope', () => {
    const properties = schemaProperties({
      title: 'Event',
      type: 'object',
      required: ['document_type', 'timestamp'],
      properties: {
        document_type: { type: 'string' },
        timestamp: { type: 'string' },
        card: { title: 'Card', type: 'object', properties: { title: { type: 'string' } } },
        task: { title: 'Task', type: 'object', properties: { content: { type: 'string' } } },
      },
    });

    expect(properties.map((property) => property.name).sort()).toEqual(
      ['card', 'document_type', 'task', 'timestamp'].sort(),
    );
    expect(properties.find((property) => property.name === 'card').definition).toMatchObject({ title: 'Card' });
  });
});

describe('canvas interaction tools', () => {
  it('keeps secondary-button panning in every tool and enables primary-button panning only for grab', () => {
    expect(canvasToolSettings('pointer')).toMatchObject({
      panOnDrag: [2],
      selectionOnDrag: true,
      nodesDraggable: true,
      nodesConnectable: true,
    });
    expect(canvasToolSettings('grab')).toMatchObject({
      panOnDrag: [0, 2],
      selectionOnDrag: false,
      nodesDraggable: false,
      nodesConnectable: false,
    });
    expect(canvasToolSettings('cut').panOnDrag).toEqual([2]);
  });
});

describe('data edge compatibility', () => {
  it('rejects a connection whose source type the target does not accept', () => {
    expect(schemasAreCompatible({ type: 'string' }, { type: 'integer' })).toBe(false);
    expect(schemasAreCompatible({ type: 'string' }, { type: 'number' })).toBe(false);
    expect(schemasAreCompatible({ type: ['string', 'null'] }, { type: 'string' })).toBe(false);
    expect(schemasAreCompatible({ type: 'object', title: 'Kanban Card' }, { type: 'object', title: 'Workflow' })).toBe(false);
  });

  it('accepts a source whose types the target accepts, including JSON integer numbers', () => {
    expect(schemasAreCompatible({ type: 'string' }, { type: 'string' })).toBe(true);
    expect(schemasAreCompatible({ type: 'string' }, { type: ['string', 'null'] })).toBe(true);
    expect(schemasAreCompatible({ type: 'integer' }, { type: 'number' })).toBe(true);
    expect(schemasAreCompatible({ type: 'integer' }, { type: ['number', 'null'] })).toBe(true);
    expect(schemasAreCompatible({}, { type: 'integer' })).toBe(true);
  });
});

describe('workflow hydration', () => {
  it('keeps falsy data constants and stored node sizes through a round trip', () => {
    const workflow = {
      graph: {
        nodes: {
          off: { kind: 'data', title: 'Off', value: false, output_schema: { type: 'boolean' } },
          zero: { kind: 'data', title: 'Zero', value: 0, output_schema: { type: 'integer' } },
          empty: { kind: 'data', title: 'Empty', value: '', output_schema: { type: 'string' } },
        },
        edges: [],
      },
      ui: {
        nodes: {
          off: {
            position: { x: 0, y: 0 },
            size: { data: { width: 500, height: 300 }, settings: { width: 600, height: 400 } },
          },
        },
      },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);

    expect(persisted.graph.nodes.off.value).toBe(false);
    expect(persisted.graph.nodes.zero.value).toBe(0);
    expect(persisted.graph.nodes.empty.value).toBe('');
    expect(persisted.ui.nodes.off.size).toEqual(workflow.ui.nodes.off.size);
    expect(persisted.ui.nodes.zero.size).toBeUndefined();
  });
});

describe('canvas node insertion', () => {
  it('keeps right-click screen and flow coordinates together', () => {
    const event = { button: 2, clientX: 320, clientY: 240 };
    const reactFlow = {
      screenToFlowPosition: vi.fn(() => ({ x: 96, y: 64 })),
    };

    expect(canvasMenuAnchor(event, reactFlow)).toEqual({
      screenPosition: { x: 320, y: 240 },
      flowPosition: { x: 96, y: 64 },
    });
    expect(reactFlow.screenToFlowPosition).toHaveBeenCalledWith({ x: 320, y: 240 });
  });
});

describe('resize-aware handles', () => {
  it('only keeps ports whose handle position fits inside the node height', () => {
    const properties = [{ name: 'first' }, { name: 'second' }, { name: 'third' }, { name: 'fourth' }];

    expect(visibleNodePortProperties(properties, 110)).toEqual(properties.slice(0, 2));
    expect(visibleNodePortProperties(properties, 190)).toEqual(properties);
    expect(visibleNodePortProperties(properties)).toEqual(properties);
  });

  it('always includes connected ports even when the node is too short for all ports', () => {
    const properties = [{ name: 'a' }, { name: 'b' }, { name: 'c' }, { name: 'd' }, { name: 'e' }];

    // At height 110, normally only 2 ports fit
    expect(visibleNodePortProperties(properties, 110)).toEqual(properties.slice(0, 2));

    // But if ports 'd' and 'e' are connected, they must still appear
    // At height 110 only 2 ports fit, so connected ones take both slots
    expect(visibleNodePortProperties(properties, 110, ['d', 'e'])).toEqual([
      { name: 'd' },
      { name: 'e' },
    ]);
  });

  it('shows all ports when the node is tall enough, regardless of connected set', () => {
    const properties = [{ name: 'x' }, { name: 'y' }, { name: 'z' }];
    // Connected ports are prioritized, so 'z' moves to the front
    expect(visibleNodePortProperties(properties, 200, ['z'])).toEqual([
      { name: 'z' }, { name: 'x' }, { name: 'y' },
    ]);
  });
});

describe('auto-sized nodes without stored size', () => {
  it('auto-sizes an event node to fit all output ports when no ui size is stored', () => {
    const schema = {
      type: 'object',
      properties: {
        a: { type: 'string' },
        b: { type: 'string' },
        c: { type: 'string' },
        d: { type: 'string' },
        e: { type: 'string' },
        f: { type: 'string' },
      },
    };
    const workflow = {
      graph: {
        nodes: {
          event_1: { kind: 'event', title: 'Event', document_type: 'test', output_schema: schema },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const node = flow.nodes.find((n) => n.id === 'event_1');
    // 6 output ports => autoHeight = 69 + 5*28 = 209, but default event min is 220
    expect(node.height).toBe(220);
  });

  it('keeps a stored size when one is present', () => {
    const schema = {
      type: 'object',
      properties: {
        a: { type: 'string' },
        b: { type: 'string' },
        c: { type: 'string' },
        d: { type: 'string' },
        e: { type: 'string' },
        f: { type: 'string' },
      },
    };
    const workflow = {
      graph: {
        nodes: {
          event_1: { kind: 'event', title: 'Event', document_type: 'test', output_schema: schema },
        },
        edges: [],
      },
      ui: {
        nodes: {
          event_1: {
            position: { x: 0, y: 0 },
            size: { data: { width: 300, height: 120 }, settings: { width: 400, height: 250 } },
          },
        },
      },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const node = flow.nodes.find((n) => n.id === 'event_1');
    expect(node.height).toBe(120);
  });

  it('does not persist size in flowToWorkflow when the node was never resized', () => {
    const schema = {
      type: 'object',
      properties: { a: { type: 'string' } },
    };
    const workflow = {
      graph: {
        nodes: {
          event_1: { kind: 'event', title: 'Event', document_type: 'test', output_schema: schema },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.ui.nodes.event_1.size).toBeUndefined();
  });

  it('persists size in flowToWorkflow when the node was manually resized', () => {
    const schema = {
      type: 'object',
      properties: { a: { type: 'string' } },
    };
    const workflow = {
      graph: {
        nodes: {
          event_1: { kind: 'event', title: 'Event', document_type: 'test', output_schema: schema },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const node = flow.nodes.find((n) => n.id === 'event_1');
    node.data.dataSize = { width: 350, height: 180 };
    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.ui.nodes.event_1.size).toEqual({
      data: { width: 350, height: 180 },
      settings: { width: 340, height: 260 },
    });
  });
});

describe('palette node creation', () => {
  it('creates compact nodes so their persisted resize is a data size', () => {
    const node = createPaletteNode('agent', 'agent_1', { x: 1, y: 2 }, [], {});

    expect(node.data.expanded).toBe(false);
    expect(node.width).toBe(340);
    expect(node.height).toBe(160);
  });
});

describe('unpack schema derivation', () => {
  it('derives unpack schemas from a connected object port', () => {
    const nodes = [
      { id: 'event', data: { kind: 'event', outputSchema: KANBAN_EVENT_OUTPUT_SCHEMA } },
      { id: 'unpack', data: { kind: 'unpack', id: 'unpack' } },
    ];
    const edges = [{ source: 'event', target: 'unpack', sourceHandle: 'out:after', targetHandle: 'in:value' }];
    const { inputSchema, outputSchema } = deriveUnpackSchemas('unpack', nodes, edges);
    expect(inputSchema.properties.value).toMatchObject({ title: 'Kanban Card', type: 'object' });
    expect(outputSchema.properties).toHaveProperty('title');
    expect(outputSchema.properties).toHaveProperty('column_id');
  });

  it('derives fields from a nullable object port', () => {
    const nodes = [
      { id: 'source', data: { kind: 'event', outputSchema: { type: 'object', properties: { card: { type: ['object', 'null'], properties: { title: { type: 'string' } } } } } } },
      { id: 'unpack', data: { kind: 'unpack' } },
    ];
    const { outputSchema } = deriveUnpackSchemas('unpack', nodes, [{ source: 'source', target: 'unpack', sourceHandle: 'out:card', targetHandle: 'in:value' }]);

    expect(outputSchema.properties).toEqual({ title: { type: 'string' } });
  });

  it('resolves chained local refs before exposing Unpack outputs', () => {
    const nodes = [
      {
        id: 'source',
        data: {
          kind: 'data',
          outputSchema: {
            $ref: '#/$defs/Card',
            $defs: {
              Card: { $ref: '#/$defs/CardShape' },
              CardShape: { type: 'object', required: ['title'], properties: { title: { type: 'string' } } },
            },
          },
        },
      },
      { id: 'unpack', data: { kind: 'unpack' } },
    ];
    const { inputSchema, outputSchema } = deriveUnpackSchemas('unpack', nodes, [{ source: 'source', target: 'unpack', sourceHandle: 'out:value', targetHandle: 'in:value' }]);

    expect(inputSchema.properties.value).toMatchObject({ $ref: '#/$defs/Card' });
    expect(outputSchema).toMatchObject({
      properties: { title: { type: 'string' } },
      required: ['title'],
      $defs: nodes[0].data.outputSchema.$defs,
    });
  });

  it('returns empty schemas when no value edge is connected', () => {
    const nodes = [{ id: 'unpack', data: { kind: 'unpack', id: 'unpack' } }];
    const edges = [];
    const { inputSchema, outputSchema } = deriveUnpackSchemas('unpack', nodes, edges);
    expect(inputSchema.properties.value).toEqual({ type: 'object' });
    expect(outputSchema.properties).toEqual({});
  });

  it('handles chained unpack nodes', () => {
    const nodes = [
      { id: 'event', data: { kind: 'event', outputSchema: KANBAN_EVENT_OUTPUT_SCHEMA } },
      { id: 'unpack1', data: { kind: 'unpack', id: 'unpack1', outputSchema: { type: 'object', properties: { title: { type: 'string' }, column_id: { type: 'string' } } } } },
      { id: 'unpack2', data: { kind: 'unpack', id: 'unpack2' } },
    ];
    const edges = [
      { source: 'event', target: 'unpack1', sourceHandle: 'out:after', targetHandle: 'in:value' },
      { source: 'unpack1', target: 'unpack2', sourceHandle: 'out:column_id', targetHandle: 'in:value' },
    ];
    const result = recomputeUnpackSchemas(nodes, edges);
    const unpack1 = result.find((n) => n.id === 'unpack1');
    const unpack2 = result.find((n) => n.id === 'unpack2');
    expect(unpack1.data.outputSchema.properties).toHaveProperty('title');
    expect(unpack2.data.inputSchema.properties.value).toEqual({ type: 'string' });
    expect(unpack2.data.outputSchema.properties).toEqual({});
  });
});

describe('unpack visible ports', () => {
  it('shows only connected fields collapsed and every field expanded, in schema order', () => {
    const properties = [
      { name: 'title' },
      { name: 'column_id' },
      { name: 'status' },
    ];
    expect(visibleNodePortProperties(properties, 200, ['status', 'title'], false).map((p) => p.name)).toEqual(['title', 'status']);
    expect(visibleNodePortProperties(properties, 200, ['status'], true).map((p) => p.name)).toEqual(['title', 'column_id', 'status']);
  });

  it('shows all ports for a normal node when fields is not provided', () => {
    const properties = [
      { name: 'title' },
      { name: 'column_id' },
    ];
    const visible = visibleNodePortProperties(properties, 200, []);
    expect(visible.map((p) => p.name)).toEqual(['title', 'column_id']);
  });
});

describe('unpack edge loss computation', () => {
  it('detects edges that would be lost when an unpack output schema changes', () => {
    const currentEdges = [
      { id: 'e1', source: 'unpack', sourceHandle: 'out:title', target: 'agent' },
      { id: 'e2', source: 'unpack', sourceHandle: 'out:column_id', target: 'query' },
    ];
    const nextNodes = [
      { id: 'unpack', data: { kind: 'unpack', outputSchema: { type: 'object', properties: { title: { type: 'string' } } } } },
    ];
    const lost = computeLostEdgesForUnpackChange(currentEdges, nextNodes);
    expect(lost).toHaveLength(1);
    expect(lost[0].id).toBe('e2');
  });

  it('returns an empty array when no edges would be lost', () => {
    const currentEdges = [
      { id: 'e1', source: 'unpack', sourceHandle: 'out:title', target: 'agent' },
    ];
    const nextNodes = [
      { id: 'unpack', data: { kind: 'unpack', outputSchema: { type: 'object', properties: { title: { type: 'string' }, column_id: { type: 'string' } } } } },
    ];
    const lost = computeLostEdgesForUnpackChange(currentEdges, nextNodes);
    expect(lost).toHaveLength(0);
  });
});

describe('unpack dependent edge pruning', () => {

  it('prunes every dangling edge in a downstream unpack chain', () => {
    const nodes = [
      { id: 'unpack_1', data: { kind: 'unpack', outputSchema: { type: 'object', properties: { card: { type: 'object', properties: { title: { type: 'string' } } } } } } },
      { id: 'unpack_2', data: { kind: 'unpack', outputSchema: { type: 'object', properties: { title: { type: 'string' } } } } },
      { id: 'consumer', data: { kind: 'agent' } },
    ];
    const edges = [
      { id: 'unpack-value', source: 'unpack_1', sourceHandle: 'out:card', target: 'unpack_2', targetHandle: 'in:value' },
      { id: 'consumer-value', source: 'unpack_2', sourceHandle: 'out:title', target: 'consumer', targetHandle: 'in:title' },
    ];

    const pruned = pruneLostUnpackEdges(nodes, edges);

    expect(pruned.edges).toEqual([]);
    expect(pruned.lostEdges.map((edge) => edge.id)).toEqual(['unpack-value', 'consumer-value']);
  });
});


describe('isUnpackableSchema', () => {
  it('returns true for an object schema or nullable object schema with properties', () => {
    expect(isUnpackableSchema({ type: 'object', properties: { a: { type: 'string' } } })).toBe(true);
    expect(isUnpackableSchema({ type: ['object', 'null'], properties: { a: { type: 'string' } } })).toBe(true);
  });

  it('returns false for non-object or empty properties', () => {
    expect(isUnpackableSchema({ type: 'string' })).toBe(false);
    expect(isUnpackableSchema({ type: 'object', properties: {} })).toBe(false);
    expect(isUnpackableSchema(null)).toBe(false);
  });
});

describe('parsePromptPlaceholders', () => {
  it('extracts dotted placeholder roots in first-appearance order', () => {
    const result = parsePromptPlaceholders('Card {card.title} moved to {card.column_id} by {actor.name}');
    expect(result.roots).toEqual(['card', 'actor']);
    expect(result.placeholders).toEqual(['card.title', 'card.column_id', 'actor.name']);
  });

  it('ignores JSON literals like braces with quotes', () => {
    const result = parsePromptPlaceholders('Payload: {"a": 1} and {root}');
    expect(result.roots).toEqual(['root']);
    expect(result.placeholders).toEqual(['root']);
  });

  it('treats thread_id as reserved and excludes it from roots', () => {
    const result = parsePromptPlaceholders('{thread_id} and {thread_id.value} and {data}');
    expect(result.roots).toEqual(['data']);
    expect(result.placeholders).toEqual(['data']);
  });

  it('returns empty arrays for null or empty text', () => {
    expect(parsePromptPlaceholders('')).toEqual({ roots: [], placeholders: [] });
    expect(parsePromptPlaceholders(null)).toEqual({ roots: [], placeholders: [] });
  });
});

describe('derivePromptInputSchema', () => {
  it('builds an object schema with required placeholder roots and optional thread_id', () => {
    const schema = derivePromptInputSchema('Card {card.title} in {board.name}');
    expect(schema).toEqual({
      type: 'object',
      required: ['card', 'board'],
      properties: { thread_id: { type: 'string' }, card: {}, board: {} },
    });
  });

  it('keeps thread_id optional when there are no placeholders', () => {
    const schema = derivePromptInputSchema('Hello world');
    expect(schema).toEqual({ type: 'object', properties: { thread_id: { type: 'string' } } });
  });

  it('changes only an inline template input contract, preserving typed Extract outputs', () => {
    const extractOutputSchema = {
      type: 'object',
      required: ['result', 'thread_id'],
      properties: { result: { type: 'object', properties: { title: { type: 'string' } } }, thread_id: { type: 'string' } },
    };
    const node = {
      template: 'Old template',
      inputSchema: { type: 'object', properties: {} },
      outputSchema: extractOutputSchema,
    };
    const patch = promptTemplatePatch('Summarize {card.title}');
    const updated = { ...node, ...patch };

    expect(patch).toEqual({
      template: 'Summarize {card.title}',
      inputSchema: { type: 'object', required: ['card'], properties: { thread_id: { type: 'string' }, card: {} } },
    });
    expect(updated.outputSchema).toEqual(extractOutputSchema);
  });

  it('derives the selected instruction input contract from its loaded text', () => {
    expect(instructionPatch('workflows/card.md', 'Review {card.title}')).toEqual({
      instruction: 'workflows/card.md',
      template: null,
      inputSchema: { type: 'object', required: ['card'], properties: { thread_id: { type: 'string' }, card: {} } },
    });
  });
});

describe('format node helpers', () => {
  it('derives required placeholder ports without adding a thread port', () => {
    expect(deriveFormatInputSchema('Hello {value.name} in {context}')).toEqual({
      type: 'object',
      required: ['value', 'context'],
      properties: { value: {}, context: {} },
    });
    expect(formatTemplatePatch('{thread_id}')).toEqual({
      template: '{thread_id}',
      inputSchema: { type: 'object', required: ['thread_id'], properties: { thread_id: {} } },
    });
  });

  it('round-trips its template and derived ports', () => {
    const workflow = {
      graph: {
        nodes: {
          format_1: {
            kind: 'format',
            title: 'Greeting',
            template: 'Hello {value.name}',
            input_schema: { type: 'object', required: ['value'], properties: { value: {} } },
            output_schema: { type: 'object', required: ['text'], properties: { text: { type: 'string' } } },
          },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    expect(flow.nodes[0]).toMatchObject({
      type: 'format',
      data: { template: 'Hello {value.name}', inputSchema: workflow.graph.nodes.format_1.input_schema },
    });
    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.format_1).toMatchObject({
      kind: 'format',
      title: 'Greeting',
      template: 'Hello {value.name}',
      input_schema: workflow.graph.nodes.format_1.input_schema,
      output_schema: workflow.graph.nodes.format_1.output_schema,
    });
  });
});

describe('agent node round-trip', () => {
  it('serializes and deserializes instruction, template, and subagent', () => {
    const workflow = {
      graph: {
        nodes: {
          agent_1: {
            kind: 'agent',
            title: 'Notify',
            instruction: null,
            template: 'Notify about {card.title}',
            subagent: 'Helper',
            input_schema: { type: 'object', required: ['card'], properties: { card: {} } },
            output_schema: { type: 'object', required: ['message', 'thread_id'], properties: { message: { type: 'string' }, thread_id: { type: 'string' } } },
          },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const node = flow.nodes.find((n) => n.id === 'agent_1');
    expect(node.type).toBe('agent');
    expect(node.data.instruction).toBeNull();
    expect(node.data.template).toBe('Notify about {card.title}');
    expect(node.data.subagent).toBe('Helper');

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.agent_1).toMatchObject({
      kind: 'agent',
      instruction: null,
      template: 'Notify about {card.title}',
      subagent: 'Helper',
    });
  });
});

describe('extract node round-trip', () => {
  it('serializes and deserializes instruction, template, and schema', () => {
    const workflow = {
      graph: {
        nodes: {
          extract_1: {
            kind: 'extract',
            title: 'Parse',
            instruction: 'workflows/parse.md',
            template: null,
            schema: 'kanban_card',
            input_schema: { type: 'object', properties: {} },
            output_schema: { type: 'object', required: ['result', 'thread_id'], properties: { result: {}, thread_id: { type: 'string' } } },
          },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const node = flow.nodes.find((n) => n.id === 'extract_1');
    expect(node.type).toBe('extract');
    expect(node.data.instruction).toBe('workflows/parse.md');
    expect(node.data.template).toBeNull();
    expect(node.data.schema).toBe('kanban_card');

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.extract_1).toMatchObject({
      kind: 'extract',
      instruction: 'workflows/parse.md',
      template: null,
      schema: 'kanban_card',
    });
  });
});
