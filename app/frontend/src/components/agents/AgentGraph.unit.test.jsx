import { describe, expect, it } from 'vitest';

import { forkLaneEdgePosition, validateWorkingGraph } from './AgentGraph';

// Regression coverage for the router-fork rail bug: the "+ Add branch" slot
// is always appended after every real lane when the fork is editable (see
// ForkBlock in AgentGraph.jsx), so the right rail terminator must land on
// that slot, not on the last real branch lane one position early.
describe('forkLaneEdgePosition', () => {
  it('marks the last real lane as the right rail terminator when read-only (no add-branch slot follows)', () => {
    expect(forkLaneEdgePosition(2, 3, false)).toEqual({ isFirst: false, isLast: true });
  });

  it('does NOT mark the last real lane as the terminator when editable, since "+ Add branch" renders after it', () => {
    expect(forkLaneEdgePosition(2, 3, true)).toEqual({ isFirst: false, isLast: false });
  });

  it('marks index 0 as the left rail terminator regardless of editability', () => {
    expect(forkLaneEdgePosition(0, 3, true)).toEqual({ isFirst: true, isLast: false });
    expect(forkLaneEdgePosition(0, 3, false)).toEqual({ isFirst: true, isLast: false });
  });

  it('a lone branch (lanesLength 1) is both the first and, when read-only, the last terminator', () => {
    expect(forkLaneEdgePosition(0, 1, false)).toEqual({ isFirst: true, isLast: true });
    expect(forkLaneEdgePosition(0, 1, true)).toEqual({ isFirst: true, isLast: false });
  });

  it('a middle lane owns neither terminator', () => {
    expect(forkLaneEdgePosition(1, 3, false)).toEqual({ isFirst: false, isLast: false });
    expect(forkLaneEdgePosition(1, 3, true)).toEqual({ isFirst: false, isLast: false });
  });
});

// Regression coverage for the fan_in validation bug: edge.from is an array
// for a fan_in join (its whole point is multiple sources), but the
// validator used to compare/look it up as if it were always a single
// string id, so it flagged the join's sources as missing and the join node
// itself as unreachable on every graph that used "Join branches" -- Run
// stayed permanently disabled for any routine containing one.
describe('validateWorkingGraph', () => {
  const routerJoinGraph = {
    nodes: [
      { id: 'gate', kind: 'eval' },
      { id: 'left', kind: 'eval' },
      { id: 'right', kind: 'eval' },
      { id: 'join', kind: 'eval' },
    ],
    edges: [
      {
        from: 'gate',
        level: 'route',
        router: 'state_equals',
        state_key: 'gate',
        branches: { True: 'left', False: 'right' },
      },
      { from: ['left', 'right'], to: 'join', level: 'fan_in' },
    ],
    entry: 'gate',
  };

  it('accepts a router fork whose branches converge on a fan_in join', () => {
    const { nodes, edges, entry } = routerJoinGraph;
    expect(validateWorkingGraph(nodes, edges, entry)).toEqual([]);
  });

  it('still flags a fan_in edge naming a genuinely missing source', () => {
    const nodes = routerJoinGraph.nodes.filter((node) => node.id !== 'right');
    const edges = routerJoinGraph.edges.map((edge) => (
      edge.level === 'fan_in' ? { ...edge, from: ['left', 'right'] } : edge
    ));
    expect(validateWorkingGraph(nodes, edges, routerJoinGraph.entry)).toContain(
      'Edge source "right" is missing.',
    );
  });
});
