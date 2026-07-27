import { describe, expect, it } from 'vitest';
import { workflowToFlow } from './WorkflowsPage';

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

    expect(flow.edges).toEqual([expect.objectContaining({
      source: 'event',
      target: 'user',
    })]);
    expect(flow.edges[0]).not.toHaveProperty('sourceHandle');
    expect(flow.edges[0]).not.toHaveProperty('targetHandle');
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

    expect(flow.edges[0]).toEqual(expect.objectContaining({
      sourceHandle: 'true',
      target: 'user',
    }));
    expect(flow.edges[0]).not.toHaveProperty('targetHandle');
  });
});
