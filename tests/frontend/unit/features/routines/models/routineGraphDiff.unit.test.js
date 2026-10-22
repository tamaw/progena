import { describe, expect, it } from 'vitest';
import { diffRoutineGraphs, isNodeRunValid } from '@frontend/features/routines/models/routineGraphDiff';

function node(id, overrides = {}) {
  return { id, kind: 'eval', label: id, description: '', config: {}, ...overrides };
}
function edge(from, to, overrides = {}) {
  return { from, to, ...overrides };
}

describe('diffRoutineGraphs', () => {
  it('invalidates nothing when the graphs are identical', () => {
    const graph = {
      entry: 'a',
      nodes: [node('a'), node('b'), node('c')],
      edges: [edge('a', 'b'), edge('b', 'c')],
    };
    const invalid = diffRoutineGraphs(graph, graph);
    expect(invalid.size).toBe(0);
  });

  it('invalidates only the edited node and everything downstream of it', () => {
    const baseline = {
      entry: 'a',
      nodes: [node('a'), node('b'), node('c')],
      edges: [edge('a', 'b'), edge('b', 'c')],
    };
    const current = {
      entry: 'a',
      nodes: [node('a'), node('b', { config: { expression: 'new' } }), node('c')],
      edges: [edge('a', 'b'), edge('b', 'c')],
    };
    const invalid = diffRoutineGraphs(baseline, current);
    expect(invalid).toEqual(new Set(['b', 'c']));
    expect(isNodeRunValid('a', invalid)).toBe(true);
    expect(isNodeRunValid('b', invalid)).toBe(false);
    expect(isNodeRunValid('c', invalid)).toBe(false);
  });

  it('leaves an unchanged node valid but invalidates downstream when its edges are rerouted', () => {
    const baseline = {
      entry: 'a',
      nodes: [node('a'), node('b'), node('c'), node('d')],
      edges: [edge('a', 'b'), edge('b', 'c')],
    };
    const current = {
      entry: 'a',
      nodes: [node('a'), node('b'), node('c'), node('d')],
      edges: [edge('a', 'b'), edge('b', 'd')],
    };
    const invalid = diffRoutineGraphs(baseline, current);
    // 'b' itself is byte-identical -- only where it routes to changed.
    expect(isNodeRunValid('b', invalid)).toBe(true);
    expect(invalid).toEqual(new Set(['d']));
  });

  it('invalidates a newly inserted node and its downstream, leaves upstream valid', () => {
    const baseline = {
      entry: 'a',
      nodes: [node('a'), node('c')],
      edges: [edge('a', 'c')],
    };
    const current = {
      entry: 'a',
      nodes: [node('a'), node('b'), node('c')],
      edges: [edge('a', 'b'), edge('b', 'c')],
    };
    const invalid = diffRoutineGraphs(baseline, current);
    expect(isNodeRunValid('a', invalid)).toBe(true);
    expect(invalid).toEqual(new Set(['b', 'c']));
  });

  it('does not infinite-loop on a loop back-edge and only walks it once', () => {
    const graph = {
      entry: 'a',
      nodes: [node('a'), node('b'), node('c')],
      edges: [edge('a', 'b'), edge('b', 'c'), edge('c', 'b', { loop_guard: 'i < 3' })],
    };
    const invalid = diffRoutineGraphs(graph, graph);
    expect(invalid.size).toBe(0);
  });

  it('treats an unreachable node as unaffected regardless of edits', () => {
    const baseline = {
      entry: 'a',
      nodes: [node('a'), node('orphan')],
      edges: [],
    };
    const current = {
      entry: 'a',
      nodes: [node('a'), node('orphan', { config: { expression: 'changed' } })],
      edges: [],
    };
    const invalid = diffRoutineGraphs(baseline, current);
    expect(invalid.size).toBe(0);
  });

  it('invalidates downstream of a condition router when a branch target is edited', () => {
    const baseline = {
      entry: 'c',
      nodes: [node('c', { kind: 'condition' }), node('t'), node('f')],
      edges: [{ from: 'c', router: true, branches: { true: 't', false: 'f' } }],
    };
    const current = structuredClone(baseline);
    current.nodes[1].config = { expression: 'changed' };
    const invalid = diffRoutineGraphs(baseline, current);
    expect(invalid).toEqual(new Set(['t']));
    expect(isNodeRunValid('c', invalid)).toBe(true);
    expect(isNodeRunValid('f', invalid)).toBe(true);
  });

  it('invalidates downstream of a fan_in join when a source node is edited', () => {
    const baseline = {
      entry: 'a',
      nodes: [node('a', { kind: 'condition' }), node('b'), node('c'), node('j')],
      edges: [
        { from: 'a', router: true, branches: { true: 'b', false: 'c' } },
        { from: ['b', 'c'], to: 'j', level: 'fan_in' },
      ],
    };
    const current = structuredClone(baseline);
    current.nodes[1].config = { expression: 'changed' };
    const invalid = diffRoutineGraphs(baseline, current);
    expect(invalid).toEqual(new Set(['b', 'j']));
    expect(isNodeRunValid('a', invalid)).toBe(true);
    expect(isNodeRunValid('c', invalid)).toBe(true);
  });

  it('invalidates loop body and exit nodes when a loop node is edited', () => {
    const baseline = {
      entry: 'a',
      nodes: [node('a'), node('b'), node('c'), node('d')],
      edges: [
        edge('a', 'b'),
        edge('b', 'c'),
        { from: 'c', to: 'b', loop_guard: { expression: 'i < 3', on_false: 'd' } },
      ],
    };
    const current = structuredClone(baseline);
    current.nodes[1].config = { expression: 'changed' };
    const invalid = diffRoutineGraphs(baseline, current);
    expect(invalid).toEqual(new Set(['b', 'c', 'd']));
    expect(isNodeRunValid('a', invalid)).toBe(true);
  });
});
