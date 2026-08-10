import { describe, expect, it } from 'vitest';

import {
  addForkLane,
  forkJoin,
  forkLaneEdgePosition,
  laneNodes,
  removeForkBranch,
  removeForkDefault,
  validateWorkingGraph,
} from './AgentGraph';

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

  // The backend rejects a node with two ways out ("node has more than one
  // outgoing edge declaration"), which is exactly what splicing a step in
  // after one that already feeds a join used to produce -- the fan_in kept
  // naming it as a source while a fresh plain edge was pushed alongside.
  it('flags a step that both feeds a join and carries its own edge', () => {
    const { nodes, entry } = routerJoinGraph;
    const edges = [...routerJoinGraph.edges, { from: 'left', to: 'join', level: 'normal' }];
    expect(validateWorkingGraph(nodes, edges, entry)).toContain(
      '"left" has more than one outgoing edge.',
    );
  });
});


// Regression coverage for the unjoined-lane bug: "+ Add branch"/"+ Add
// default" used to mint a bare node wired only *into* the router, leaving it
// with no outgoing edge at all. The renderer only draws a join that every
// lane reaches, so the join silently vanished from the fork; worse, the
// factory gives a node with no outgoing edge an edge straight to the
// terminal, so taking that branch ended the routine early instead of
// continuing below the fork. A new lane has to be adopted by the join its
// siblings already use, and a dropped lane has to be taken back off it.
describe('fork lanes and their join', () => {
  const router = {
    from: 'gate',
    level: 'route',
    router: 'state_equals',
    state_key: 'category',
    branches: { happy: 'left', unhappy: 'right' },
  };
  const join = { from: ['left', 'right'], to: 'reply', level: 'fan_in' };
  const tail = { from: 'reply', to: 'done', level: 'normal' };
  const edges = [router, join, tail];
  const joinOf = (next) => next.find((edge) => edge.level === 'fan_in');
  const routerOf = (next) => next.find((edge) => edge.router);

  it('adopts a new branch lane into the join its siblings already use', () => {
    const next = addForkLane(edges, 'gate', 'noop-1');
    expect(joinOf(next).from).toEqual(['left', 'right', 'noop-1']);
    expect(Object.values(routerOf(next).branches)).toContain('noop-1');
  });

  it('adopts a new default lane into the join too', () => {
    const next = addForkLane(edges, 'gate', 'noop-1', true);
    expect(joinOf(next).from).toEqual(['left', 'right', 'noop-1']);
    expect(routerOf(next).default).toBe('noop-1');
  });

  // The bug itself: an unadopted lane leaves no node common to every lane, so
  // the fork loses its join entirely rather than merely gaining a stray step.
  it('would lose the join altogether if a new lane were left unwired', () => {
    const unwired = [{ ...router, branches: { ...router.branches, 'case-1': 'noop-1' } }, join, tail];
    expect(forkJoin(unwired, unwired[0])).toBeNull();
    expect(forkJoin(addForkLane(edges, 'gate', 'noop-1'), routerOf(addForkLane(edges, 'gate', 'noop-1'))))
      .toMatchObject({ joinId: 'reply' });
  });

  it('leaves a fork with no join below it alone', () => {
    const unjoined = [router];
    const next = addForkLane(unjoined, 'gate', 'noop-1');
    expect(next).toHaveLength(1);
    expect(Object.values(routerOf(next).branches)).toContain('noop-1');
  });

  it('takes a dropped branch lane back off the join', () => {
    const grown = addForkLane(edges, 'gate', 'noop-1');
    const value = Object.keys(routerOf(grown).branches).find((key) => routerOf(grown).branches[key] === 'noop-1');
    const next = removeForkBranch(grown, 'gate', value);
    expect(joinOf(next).from).toEqual(['left', 'right']);
    expect(routerOf(next).branches).toEqual(router.branches);
  });

  it('takes a dropped default lane back off the join', () => {
    const next = removeForkDefault(addForkLane(edges, 'gate', 'noop-1', true), 'gate');
    expect(joinOf(next).from).toEqual(['left', 'right']);
    expect(routerOf(next).default).toBeUndefined();
  });

  // A lane is a chain, and it is its *last* step that feeds the join, so
  // dropping the lane has to strip the terminal rather than the head.
  it('strips a multi-step lane by its terminal, not its head', () => {
    const chained = [
      { ...router, branches: { happy: 'left', unhappy: 'right', other: 'third' } },
      { from: 'left', to: 'left-2', level: 'normal' },
      { from: ['left-2', 'right', 'third'], to: 'reply', level: 'fan_in' },
      tail,
    ];
    const { joinId, edgeBySource } = forkJoin(chained, chained[0]);
    expect(laneNodes('left', joinId, edgeBySource)).toEqual(['left', 'left-2']);
    // 'left-2' is what fed the join, so that is what has to come off it.
    expect(joinOf(removeForkBranch(chained, 'gate', 'happy')).from).toEqual(['right', 'third']);
  });

  it('collapses the join to a plain edge once a single source is left', () => {
    const next = removeForkBranch(edges, 'gate', 'happy');
    expect(joinOf(next)).toBeUndefined();
    expect(next).toContainEqual({ from: 'right', to: 'reply', level: 'normal' });
  });

  it('keeps a grown fork valid as a whole graph', () => {
    const nodes = ['gate', 'left', 'right', 'noop-1', 'reply', 'done'].map((id) => ({ id, kind: 'eval' }));
    expect(validateWorkingGraph(nodes, addForkLane(edges, 'gate', 'noop-1'), 'gate')).toEqual([]);
  });
});