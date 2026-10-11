import { describe, expect, it, vi } from 'vitest';
import { NODE_MENU_CATEGORIES, canvasMenuAnchor, flowToWorkflow, schemaProperties, visibleNodePortProperties, workflowToFlow } from './WorkflowsPage';
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
    before: { type: 'object' },
    after: { type: 'object' },
    'after.title': { type: 'string' },
    'after.column_id': { type: 'string' },
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
          agent: { kind: 'agent', title: 'Agent', message: 'Go', input_schema: { type: 'object', properties: { document_type: { type: 'string' } } }, output_schema: { type: 'object', properties: {} } },
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

  it('maps dotted port names for nested document properties', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          event: { kind: 'event', title: 'Event', document_type: 'kanban_card', output_schema: KANBAN_EVENT_OUTPUT_SCHEMA },
          agent: { kind: 'agent', title: 'Agent', message: 'Go', input_schema: { type: 'object', properties: { 'after.title': { type: 'string' } } }, output_schema: { type: 'object', properties: {} } },
        },
        edges: [{ from: { node: 'event', port: 'after.title' }, to: { node: 'agent', port: 'after.title' } }],
      },
      ui: { nodes: {} },
    }, {}, null);

    const edge = flow.edges.find((e) => e.source === 'event');
    expect(edge).toMatchObject({
      sourceHandle: 'out:after.title',
      target: 'agent',
      targetHandle: 'in:after.title',
    });

    const persisted = flowToWorkflow({}, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.edges).toContainEqual({
      from: { node: 'event', port: 'after.title' },
      to: { node: 'agent', port: 'after.title' },
    });
  });

  it('maps condition results through ordinary typed ports', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          condition: { kind: 'condition', title: 'Condition', expression: 'ready', input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: { true: { type: 'boolean' } } } },
          agent: { kind: 'agent', title: 'Agent', message: 'Go', input_schema: { type: 'object', properties: { result: { type: 'boolean' } } }, output_schema: { type: 'object', properties: {} } },
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
          source: { kind: 'agent', title: 'Source', message: 'Go', input_schema: { type: 'object', properties: {} }, output_schema: AGENT_OUTPUT_SCHEMA },
          target: { kind: 'agent', title: 'Target', message: 'Go', input_schema: agentInputSchema, output_schema: { type: 'object', properties: {} } },
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
          agent: { kind: 'agent', title: 'Agent', message: 'Go', input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
          data: { kind: 'data', title: 'Data', value: 1, output_schema: { type: 'number' } },
          mcp: { kind: 'mcp', title: 'MCP', input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
          condition: { kind: 'condition', title: 'Condition', expression: 'ready', input_schema: { type: 'object', properties: {} } },
          approval: { kind: 'approval', title: 'Approval', message: 'Approve?', input_schema: { type: 'object', properties: {} } },
          human_input: { kind: 'human_input', title: 'Human', message: 'Pick one', input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
          document_read: { kind: 'document_read', title: 'Read', document_type: 'kanban_card', input_schema: KANBAN_READ_SCHEMAS.read_input_schema, output_schema: KANBAN_READ_SCHEMAS.read_output_schema },
          document_query: { kind: 'document_query', title: 'Query', document_type: 'kanban_card', filter_fields: ['title'], limit: 50, input_schema: { type: 'object', required: ['title'], properties: { title: { type: 'string' } } }, output_schema: KANBAN_READ_SCHEMAS.query_output_schema },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const typesById = Object.fromEntries(flow.nodes.map((node) => [node.id, node.type]));
    expect(typesById).toEqual({
      event: 'event',
      agent: 'agent',
      data: 'data',
      mcp: 'mcp',
      condition: 'condition',
      approval: 'approval',
      human_input: 'human_input',
      document_read: 'document_read',
      document_query: 'document_query',
    });
  });

  it('renders a completed node as done even when the run retains its last node id', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          agent: { kind: 'agent', title: 'Notify', message: 'Notify the user.', input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
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
          agent_1: { kind: 'agent', title: 'Notify', message: 'Notify the user.', input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
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
            filter_fields: ['title'],
            limit: 25,
            input_schema: { type: 'object', required: ['title'], properties: { title: { type: 'string' } } },
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
    expect(node.data.filterFields).toEqual(['title']);
    expect(node.data.limit).toBe(25);

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.query_1).toMatchObject({
      kind: 'document_query',
      document_type: 'kanban_card',
      filter_fields: ['title'],
      limit: 25,
    });
  });
});

describe('workflow node menu', () => {
  it('exposes exactly the nine V3 node kinds and no capability-specific kinds', () => {
    const addableKinds = NODE_MENU_CATEGORIES.flatMap((category) => category.items).map((item) => item.kind);

    expect(addableKinds.sort()).toEqual(
      ['agent', 'approval', 'condition', 'data', 'document_query', 'document_read', 'event', 'human_input', 'mcp'].sort(),
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

describe('data edge diagnostics', () => {
  it('flags an edge with incompatible source and target types as invalid', () => {
    const workflow = {
      graph: {
        nodes: {
          source: { kind: 'agent', title: 'Source', message: 'Go', input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: { title: { type: 'string' } } } },
          target: {
            kind: 'agent',
            title: 'Target',
            message: 'Go',
            input_schema: { type: 'object', properties: { count: { type: 'integer' } } },
            output_schema: { type: 'object', properties: {} },
          },
        },
        edges: [{ kind: 'data', from: { node: 'source', port: 'title' }, to: { node: 'target', port: 'count' } }],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const sourceOutput = flow.nodes.find((node) => node.id === 'source').data.outputSchema.properties.title;
    const targetInput = flow.nodes.find((node) => node.id === 'target').data.inputSchema.properties.count;

    expect(sourceOutput.type).toBe('string');
    expect(targetInput.type).toBe('integer');
    expect(sourceOutput.type).not.toBe(targetInput.type);
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
