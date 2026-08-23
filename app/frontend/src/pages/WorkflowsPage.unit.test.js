import { describe, expect, it } from 'vitest';
import { NODE_MENU_CATEGORIES, flowToWorkflow, payloadMappingProperties, schemaProperties, workflowToFlow } from './WorkflowsPage';

describe('workflowToFlow', () => {
  it('maps default ports to React Flow implicit handles', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          event: { kind: 'event', title: 'Event' },
          user: { kind: 'user', title: 'User' },
        },
        edges: [{
          from: { node: 'event', port: 'default' },
          to: { node: 'user', port: 'default' },
        }],
      },
      ui: { nodes: {} },
      trigger: { event_type: 'kanban.card.moved' },
    }, {}, null);

    const graphEdge = flow.edges.find((edge) => edge.source === 'event');
    expect(graphEdge).toEqual(expect.objectContaining({
      source: 'event',
      target: 'user',
    }));
    expect(graphEdge).not.toHaveProperty('sourceHandle');
    expect(graphEdge).not.toHaveProperty('targetHandle');
  });

  it('preserves named branch handles', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          condition: { kind: 'condition', title: 'Condition', expression: 'true' },
          user: { kind: 'user', title: 'User' },
        },
        edges: [{
          from: { node: 'condition', port: 'true' },
          to: { node: 'user', port: 'default' },
        }],
      },
      ui: { nodes: {} },
      trigger: { event_type: 'kanban.card.moved' },
    }, {}, null);

    const graphEdge = flow.edges.find((edge) => edge.source === 'condition');
    expect(graphEdge).toEqual(expect.objectContaining({
      sourceHandle: 'true',
      target: 'user',
    }));
    expect(graphEdge).not.toHaveProperty('targetHandle');
  });

  it('maps MCP nodes with server and tool ids', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          mcp_1: { kind: 'mcp', title: 'Read URL', server_id: 'mcp:jina', tool_name: 'read_url', inputs: { url: { source: 'literal', value: 'https://example.com' } } },
        },
        edges: [],
      },
      ui: { nodes: {} },
      trigger: { event_type: 'kanban.card.created' },
    }, {}, null);

    const mcpNode = flow.nodes.find((n) => n.id === 'mcp_1');
    expect(mcpNode).toBeDefined();
    expect(mcpNode.type).toBe('mcp');
    expect(mcpNode.data.serverId).toBe('mcp:jina');
    expect(mcpNode.data.toolName).toBe('read_url');
    expect(mcpNode.data.inputs.url.value).toBe('https://example.com');
  });

  it('maps a knowledge node as a distinct type with only its selected source', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          knowledge_1: {
            kind: 'knowledge',
            title: 'Search product docs',
            prompt: 'Answer using the selected knowledge base.',
            agent_name: 'progenitor',
            knowledge_base_id: 'kb_product',
            references: {},
            inputs: {},
            input_schema: { type: 'object', properties: {} },
            output_schema: { type: 'string' },
          },
        },
        edges: [],
      },
      ui: { nodes: {} },
      trigger: { event_type: 'kanban.card.created' },
    }, {}, null);

    const knowledgeNode = flow.nodes.find((node) => node.id === 'knowledge_1');
    expect(knowledgeNode.type).toBe('knowledge');
    expect(knowledgeNode.data.kind).toBe('knowledge');
    expect(knowledgeNode.data.knowledgeBaseId).toBe('kb_product');
    expect(knowledgeNode.data).not.toHaveProperty('repositoryId');
  });

  it('migrates saved prompt nodes into prompt nodes with a saved prompt reference', () => {
    const workflow = {
      graph: {
        nodes: {
          prompt_1: {
            kind: 'saved_prompt',
            title: 'Review request',
            prompt: 'Review the saved prompt output.',
            prompt_path: '/prompts/review-request',
            agent_name: 'progenitor',
            references: {},
          },
        },
        edges: [],
      },
      ui: { nodes: {} },
      trigger: { event_type: 'kanban.card.created' },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const promptNode = flow.nodes.find((node) => node.id === 'prompt_1');

    expect(promptNode).toMatchObject({
      type: 'prompt',
      data: {
        kind: 'prompt',
        promptPath: '/prompts/review-request',
      },
    });

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes.prompt_1).toMatchObject({
      kind: 'prompt',
      references: { prompt_path: '/prompts/review-request' },
    });
    expect(persisted.graph.nodes.prompt_1).not.toHaveProperty('prompt_path');
  });

  it('renders a completed node as done even when the run retains its last node id', () => {
    const flow = workflowToFlow({
      graph: {
        nodes: {
          prompt_1: { kind: 'prompt', title: 'Notify', prompt: 'Notify the user.', agent_name: 'progenitor' },
        },
        edges: [],
      },
      ui: { nodes: {} },
      trigger: { event_type: 'kanban.card.moved' },
    }, {}, {
      status: 'completed',
      current_node_id: 'prompt_1',
      completed_node_ids: ['prompt_1'],
    });

    expect(flow.nodes.find((node) => node.id === 'prompt_1')?.data).toMatchObject({
      currentNodeId: null,
      completedNodeIds: ['prompt_1'],
    });
  });

  it('renders the persisted Kanban trigger as a non-prompt canvas node', () => {
    const workflow = {
      trigger: {
        type: 'event',
        event_type: 'kanban.card.moved',
        from_column: 'build',
        to_column: 'done',
      },
      graph: {
        nodes: {
          prompt_1: { kind: 'prompt', title: 'Notify', prompt: 'Notify the user.', agent_name: 'progenitor' },
        },
        edges: [],
      },
      ui: { nodes: {} },
    };

    const flow = workflowToFlow(workflow, {}, null);
    const trigger = flow.nodes.find((node) => node.type === 'kanban_trigger');

    expect(trigger).toMatchObject({
      id: 'trigger_kanban',
      type: 'kanban_trigger',
      data: {
        title: 'Kanban trigger',
        eventType: 'kanban.card.moved',
        fromColumn: 'build',
        toColumn: 'done',
      },
    });
    expect(flow.edges).toContainEqual(expect.objectContaining({
      source: 'trigger_kanban',
      target: 'prompt_1',
    }));

    const persisted = flowToWorkflow(workflow, flow.nodes, flow.edges, flow.viewport);
    expect(persisted.graph.nodes).toMatchObject(workflow.graph.nodes);
    expect(persisted.graph.edges).toEqual([]);
    expect(persisted.trigger).toEqual(workflow.trigger);
  });
});

describe('workflow node menu', () => {
  it('groups every addable dock node and exposes triggers and agents', () => {
    const categories = Object.fromEntries(NODE_MENU_CATEGORIES.map((category) => [category.id, category]));
    const addableKinds = NODE_MENU_CATEGORIES.flatMap((category) => category.items)
      .filter((item) => !item.disabled)
      .map((item) => item.kind);

    expect(categories.triggers.items).toContainEqual(expect.objectContaining({ label: 'Kanban event', disabled: true }));
    expect(categories.agents.items.map((item) => item.kind)).toEqual(['prompt', 'call-agent']);
    expect(addableKinds).toEqual(expect.arrayContaining([
      'prompt', 'call-agent', 'knowledge-search', 'repository-task', 'kanban-task',
      'mcp-tool', 'ask-user', 'inbox-message', 'routine', 'condition', 'approval',
    ]));
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
            payload: { type: 'object' },
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
      expect.objectContaining({ name: 'payload', required: false }),
      expect.objectContaining({ name: 'source', required: false }),
    ]);
  });

  it('uses payload paths for every node output property', () => {
    expect(payloadMappingProperties({
      type: 'object',
      properties: {
        summary: { type: 'string' },
        score: { type: 'number' },
      },
    })).toEqual([
      expect.objectContaining({ name: 'summary', path: '/payload/summary' }),
      expect.objectContaining({ name: 'score', path: '/payload/score' }),
    ]);
  });

  it('uses the same payload mapping for trigger envelopes', () => {
    expect(payloadMappingProperties({
      oneOf: [{
        type: 'object',
        properties: {
          event_type: { const: 'kanban.card.created' },
          timestamp: { type: 'string' },
          payload: {
            type: 'object',
            properties: {
              event_type: { const: 'kanban.card.created' },
              timestamp: { type: 'string' },
              card: { type: 'object' },
              task: { type: 'object' },
            },
          },
        },
      }],
    })).toEqual([
      expect.objectContaining({ name: 'card', path: '/payload/card' }),
      expect.objectContaining({ name: 'task', path: '/payload/task' }),
    ]);
  });
});
