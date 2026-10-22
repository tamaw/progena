import { describe, expect, it } from 'vitest';
import { makeNode, normalizeGraphNodes, stableStringify } from './graphEdits';

describe('normalizeGraphNodes', () => {
  it('uses each node id as its output key while preserving the rest of the node', () => {
    const nodes = [{
      id: 'eval-1',
      kind: 'eval',
      label: 'Double value',
      output_key: 'result',
      config: { expression: 'value * 2' },
    }];

    expect(normalizeGraphNodes(nodes)).toEqual([{
      ...nodes[0],
      output_key: 'eval-1',
    }]);
    expect(nodes[0].output_key).toBe('result');
  });
});

describe('makeNode', () => {
  it('does not seed allowed_responses for human nodes', () => {
    const node = makeNode('human');
    expect(node.config).toHaveProperty('message');
    expect(node.config).not.toHaveProperty('allowed_responses');
  });
});

describe('stableStringify', () => {
  it('ignores object key insertion order at every graph depth', () => {
    const localGraph = {
      input_schema: {
        type: 'object',
        properties: { value: { type: 'number' } },
        required: ['value'],
      },
    };
    const storedGraph = {
      input_schema: {
        type: 'object',
        required: ['value'],
        properties: { value: { type: 'number' } },
      },
    };

    expect(stableStringify(localGraph)).toBe(stableStringify(storedGraph));
  });

  it('keeps array order significant', () => {
    expect(stableStringify({ required: ['first', 'second'] }))
      .not.toBe(stableStringify({ required: ['second', 'first'] }));
  });
});
