import { describe, expect, it } from 'vitest';

import {
  addForkLane,
  forkJoin,
  forkLaneEdgePosition,
  incompleteNodes,
  missingRequirements,
  missingRequiredConfig,
  removeForkBranch,
  removeForkDefault,
  saveStatusWhenInSync,
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

  it('deletes a dropped branch lane outright and takes it off the join', () => {
    const grown = addForkLane(edges, 'gate', 'noop-1');
    const value = Object.keys(routerOf(grown).branches).find((key) => routerOf(grown).branches[key] === 'noop-1');
    const { edges: next, removedNodeIds } = removeForkBranch(grown, 'gate', 'gate', value);
    expect(removedNodeIds).toEqual(['noop-1']);
    expect(joinOf(next).from).toEqual(['left', 'right']);
    expect(routerOf(next).branches).toEqual(router.branches);
  });

  it('deletes a dropped default lane too', () => {
    const grown = addForkLane(edges, 'gate', 'noop-1', true);
    const { edges: next, removedNodeIds } = removeForkDefault(grown, 'gate', 'gate');
    expect(removedNodeIds).toEqual(['noop-1']);
    expect(joinOf(next).from).toEqual(['left', 'right']);
    expect(routerOf(next).default).toBeUndefined();
  });

  // A lane is a chain, and it is its *last* step that feeds the join, so a
  // drop has to take every step the lane owned and strip the terminal -- not
  // just unhook the head and leave the rest floating.
  it('deletes every step of a multi-step lane, stripping its terminal from the join', () => {
    const chained = [
      { ...router, branches: { happy: 'left', unhappy: 'right', other: 'third' } },
      { from: 'left', to: 'left-2', level: 'normal' },
      { from: ['left-2', 'right', 'third'], to: 'reply', level: 'fan_in' },
      tail,
    ];
    const { edges: next, removedNodeIds } = removeForkBranch(chained, 'gate', 'gate', 'happy');
    expect(removedNodeIds.sort()).toEqual(['left', 'left-2']);
    expect(joinOf(next).from).toEqual(['right', 'third']);
    // The lane's own internal edge goes with it.
    expect(next.some((edge) => edge.to === 'left-2')).toBe(false);
  });

  // A lane holding a nested fork is still one lane: it leaves as a unit.
  it('deletes a lane that holds a nested fork, nested steps and all', () => {
    const nested = [
      { ...router, branches: { happy: 'left', unhappy: 'right', other: 'third' } },
      { from: 'left', level: 'route', router: 'state_equals', state_key: 'k', branches: { a: 'n-a', b: 'n-b' } },
      { from: ['n-a', 'n-b'], to: 'left-end', level: 'fan_in' },
      { from: ['left-end', 'right', 'third'], to: 'reply', level: 'fan_in' },
      tail,
    ];
    const { edges: next, removedNodeIds } = removeForkBranch(nested, 'gate', 'gate', 'happy');
    expect(removedNodeIds.sort()).toEqual(['left', 'left-end', 'n-a', 'n-b']);
    expect(joinOf(next).from).toEqual(['right', 'third']);
    expect(next.some((edge) => edge.router && edge.from === 'left')).toBe(false);
  });

  // Reachability decides ownership, so a step another lane also points at is
  // not the dropped lane's to take.
  it('keeps a step the dropped lane shared with a surviving one', () => {
    const shared = [
      { ...router, branches: { happy: 'shared', unhappy: 'right', other: 'shared' } },
      { from: ['shared', 'right'], to: 'reply', level: 'fan_in' },
      tail,
    ];
    const { edges: next, removedNodeIds } = removeForkBranch(shared, 'gate', 'gate', 'happy');
    expect(removedNodeIds).toEqual([]);
    expect(joinOf(next).from).toEqual(['shared', 'right']);
  });

  it('collapses the join to a plain edge once a single source is left', () => {
    const { edges: next, removedNodeIds } = removeForkBranch(edges, 'gate', 'gate', 'happy');
    expect(removedNodeIds).toEqual(['left']);
    expect(joinOf(next)).toBeUndefined();
    expect(next).toContainEqual({ from: 'right', to: 'reply', level: 'normal' });
  });

  it('leaves no unreachable steps behind after a drop', () => {
    const grown = addForkLane(edges, 'gate', 'noop-1');
    const { edges: next, removedNodeIds } = removeForkBranch(grown, 'gate', 'gate', 'case-1');
    const nodes = ['gate', 'left', 'right', 'noop-1', 'reply', 'done']
      .filter((id) => !removedNodeIds.includes(id))
      .map((id) => ({ id, kind: 'eval' }));
    expect(validateWorkingGraph(nodes, next, 'gate')).toEqual([]);
  });

  it('keeps a grown fork valid as a whole graph', () => {
    const nodes = ['gate', 'left', 'right', 'noop-1', 'reply', 'done'].map((id) => ({ id, kind: 'eval' }));
    expect(validateWorkingGraph(nodes, addForkLane(edges, 'gate', 'noop-1'), 'gate')).toEqual([]);
  });
});

// Regression coverage for the stranded save banner: adding a node the schema
// rejects fails the PATCH, and removing it again restores the exact signature
// the server already holds. The autosave short-circuits on that match, so
// nothing else ever clears the failure the user has already undone.
describe('saveStatusWhenInSync', () => {
  it('drops a failure once the canvas matches what the server holds', () => {
    expect(saveStatusWhenInSync('error')).toBe('idle');
  });

  it('leaves a successful save reported as saved', () => {
    expect(saveStatusWhenInSync('saved')).toBe('saved');
  });

  it('leaves an untouched canvas idle', () => {
    expect(saveStatusWhenInSync('idle')).toBe('idle');
  });
});

// A node lands from the kind picker with its required config empty, so the
// canvas has to know what the backend will reject before it PATCHes -- the
// alternative is the round trip that produced "prompt_template is not valid
// under any of the given schemas" on every single insert.
describe('missingRequiredConfig', () => {
  it('reports a freshly inserted prompt as missing its template', () => {
    expect(missingRequiredConfig({ kind: 'prompt', config: { prompt_template: '' } }))
      .toEqual(['prompt_template', 'prompt_file']);
  });

  it('accepts either arm of a one-of group, not both', () => {
    expect(missingRequiredConfig({ kind: 'prompt', config: { prompt_template: 'Hi' } })).toEqual([]);
    expect(missingRequiredConfig({ kind: 'prompt', config: { prompt_file: 'p.md' } })).toEqual([]);
  });

  it('treats whitespace as unfilled', () => {
    expect(missingRequiredConfig({ kind: 'human', config: { message: '   ' } })).toEqual(['message']);
  });

  it('has nothing to require of a noop', () => {
    expect(missingRequiredConfig({ kind: 'noop', config: {} })).toEqual([]);
  });

  it('requires both a name and a way to prompt an agent', () => {
    expect(missingRequiredConfig({ kind: 'agent', config: {} }))
      .toEqual(['agent_name', 'prompt_template', 'prompt_file', 'inputs']);
    expect(missingRequiredConfig({ kind: 'agent', config: { agent_name: 'bot', inputs: { a: 'b' } } })).toEqual([]);
  });

  it('counts an empty object or array as unfilled, not as present', () => {
    expect(missingRequiredConfig({ kind: 'agent', config: { agent_name: 'bot', inputs: {} } }))
      .toEqual(['prompt_template', 'prompt_file', 'inputs']);
  });

  it('switches foreach requirements on its own mode', () => {
    expect(missingRequiredConfig({ kind: 'foreach', config: { mode: 'send', items_from: 'data.xs' } })).toEqual([]);
    expect(missingRequiredConfig({ kind: 'foreach', config: { mode: 'agent', items_from: 'data.xs' } }))
      .toEqual(['agent_name', 'prompt_template']);
  });

  it('requires a condition to carry an expression, like eval', () => {
    expect(missingRequiredConfig({ kind: 'condition', config: {} })).toEqual(['expression', 'expression_file']);
  });
});

// The autosave gate: a graph is withheld while any step is incomplete, so the
// backend never sees a payload it is guaranteed to reject.
describe('incompleteNodes', () => {
  it('names every step still missing config', () => {
    const nodes = [
      { id: 'a', kind: 'prompt', config: { prompt_template: 'ok' } },
      { id: 'b', kind: 'prompt', config: { prompt_template: '' } },
      { id: 'c', kind: 'noop', config: {} },
      { id: 'd', kind: 'routine', config: {} },
    ];
    expect(incompleteNodes(nodes).map((entry) => entry.node.id)).toEqual(['b', 'd']);
  });

  it('clears once the last required field is filled, releasing the save', () => {
    expect(incompleteNodes([{ id: 'a', kind: 'routine', config: { routine_name: 'child' } }])).toEqual([]);
  });
});

// The inspector counts unmet requirements, not field names: telling an author
// a fresh prompt is missing "2 fields" when either a template or a file will
// satisfy it is simply wrong.
describe('missingRequirements', () => {
  it('counts a one-of choice as a single unmet requirement', () => {
    expect(missingRequirements({ kind: 'prompt', config: {} })).toEqual([['prompt_template', 'prompt_file']]);
  });

  it('keeps independent requirements separate', () => {
    expect(missingRequirements({ kind: 'classify', config: {} })).toEqual([['rubric_prompt'], ['response_schema']]);
  });

  it('drops a requirement as soon as any one alternative is filled', () => {
    expect(missingRequirements({ kind: 'agent', config: { prompt_file: 'p.md' } })).toEqual([['agent_name']]);
  });
});