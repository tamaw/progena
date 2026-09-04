import { describe, expect, it, vi } from 'vitest';
import { NODE_MENU_CATEGORIES, canvasMenuAnchor, flowToWorkflow, schemaProperties, visibleNodePortProperties, workflowToFlow } from './WorkflowsPage';
import { canvasToolSettings } from './workflowCanvasTools';

const AGENT_OUTPUT_SCHEMA = {
  type: 'object',
  required: ['title'],
  properties: { title: { type: 'string' } },
};

describe('workflowToFlow', () => {
  it('maps a connected property to typed data handles', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          event: { kind: 'event', title: 'Event', event_type: 'kanban.card.moved' },
          agent: { kind: 'agent', title: 'Agent', message: 'Go', input_schema: { type: 'object', properties: { event_type: { type: 'string' } } }, output_schema: { type: 'object', properties: {} } },
        },
        edges: [{ from: { node: 'event', port: 'event_type' }, to: { node: 'agent', port: 'event_type' } }],
      },
      ui: { nodes: {} },
    }, {}, null);

    expect(flow.edges.find((edge) => edge.source === 'event')).toMatchObject({
      sourceHandle: 'out:event_type',
      target: 'agent',
      targetHandle: 'in:event_type',
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
          event: { kind: 'event', title: 'Event', event_type: 'kanban.card.created' },
          agent: { kind: 'agent', title: 'Agent', message: 'Go', input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
          data: { kind: 'data', title: 'Data', value: 1, output_schema: { type: 'number' } },
          mcp: { kind: 'mcp', title: 'MCP', input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
          condition: { kind: 'condition', title: 'Condition', expression: 'ready', input_schema: { type: 'object', properties: {} } },
          approval: { kind: 'approval', title: 'Approval', message: 'Approve?', input_schema: { type: 'object', properties: {} } },
          human_input: { kind: 'human_input', title: 'Human', message: 'Pick one', input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
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

  it('round-trips a graph-native Kanban event with its column filters', () => {
    const workflow = {
      graph: {
        nodes: {
          event_1: {
            kind: 'event',
            title: 'Moved to Done',
            event_type: 'kanban.card.moved',
            from_column: 'build',
            to_column: 'done',
          },
          agent_1: { kind: 'agent', title: 'Notify', message: 'Notify the user.', input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} } },
        },
        edges: [{ kind: 'control', from: { node: 'event_1', port: 'default' }, to: { node: 'agent_1', port: 'default' } }],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const trigger = flow.nodes.find((node) => node.type === 'event');

    expect(trigger).toMatchObject({
      id: 'event_1',
      type: 'event',
      data: {
        title: 'Moved to Done',
        eventType: 'kanban.card.moved',
        fromColumn: 'build',
        toColumn: 'done',
        canDelete: false,
      },
    });

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.version).toBe(3);
    expect(persisted.graph.nodes.event_1).toMatchObject({
      kind: 'event',
      event_type: 'kanban.card.moved',
      from_column: 'build',
      to_column: 'done',
    });
  });
});

describe('workflow node menu', () => {
  it('exposes exactly the seven V3 node kinds and no capability-specific kinds', () => {
    const addableKinds = NODE_MENU_CATEGORIES.flatMap((category) => category.items).map((item) => item.kind);

    expect(addableKinds.sort()).toEqual(
      ['agent', 'approval', 'condition', 'data', 'event', 'human_input', 'mcp'].sort(),
    );
  });
});

describe('node interface properties', () => {
  it('merges object properties from alternative schema branches', () => {
    expect(schemaProperties({
      oneOf: [
        {
          type: 'object',
          required: ['event_type'],
          properties: {
            event_type: { const: 'kanban.card.created' },
            card: { type: 'object' },
          },
        },
        {
          type: 'object',
          properties: {
            event_type: { const: 'kanban.card.created' },
            source: { type: 'string' },
          },
        },
      ],
    })).toEqual([
      expect.objectContaining({ name: 'event_type', required: true }),
      expect.objectContaining({ name: 'card', required: false }),
      expect.objectContaining({ name: 'source', required: false }),
    ]);
  });

  it('exposes an event node\'s output properties directly, with no payload envelope', () => {
    const properties = schemaProperties({
      title: 'Event',
      type: 'object',
      required: ['event_type', 'timestamp'],
      properties: {
        event_type: { type: 'string' },
        timestamp: { type: 'string' },
        card: { title: 'Card', type: 'object', properties: { title: { type: 'string' } } },
        task: { title: 'Task', type: 'object', properties: { content: { type: 'string' } } },
      },
    });

    expect(properties.map((property) => property.name).sort()).toEqual(
      ['card', 'event_type', 'task', 'timestamp'].sort(),
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
});
