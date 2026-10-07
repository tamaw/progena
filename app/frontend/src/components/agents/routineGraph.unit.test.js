import { describe, expect, it } from 'vitest';

import {
  addForkLane,
  makeNode,
  removeForkBranch,
  removeForkDefault,
} from './graphEdits';
import {
  buildLoopMembership,
  forkJoin,
  forkLaneEdgePosition,
  isFlowEdge,
  joinTerminals,
  nodeSuccessors,
  predecessorsOf,
  reachableFrom,
} from './graphTopology';
import {
  incompleteLoopEdges,
  incompleteNodes,
  isNodeComplete,
  missingRequirements,
  missingRequiredConfig,
  saveStatusWhenInSync,
  validateWorkingGraph,
} from './graphValidation';
import {
  deriveActiveNodeIds,
  nodeRequiresRunConfirmation,
  pickEntryForIteration,
} from './graphRunState';
import {
  buildStatePathSuggestions,
  filterStatePathSuggestions,
  getExpressionAutocompleteContext,
  schemaFieldsToText,
  textToSchemaFields,
} from './graphFieldHelpers';

describe('nodeRequiresRunConfirmation', () => {
  it('requires confirmation when a not-yet-run predecessor is a side-effecting kind', () => {
    const nodes = [{ id: 'a', kind: 'agent' }, { id: 'b', kind: 'noop' }];
    const edges = [{ from: 'a', to: 'b', level: 'normal' }];
    expect(nodeRequiresRunConfirmation('b', nodes, edges, {})).toBe(true);
  });

  it('does not require confirmation when every predecessor is pure/deterministic', () => {
    const nodes = [{ id: 'a', kind: 'eval' }, { id: 'b', kind: 'noop' }];
    const edges = [{ from: 'a', to: 'b', level: 'normal' }];
    expect(nodeRequiresRunConfirmation('b', nodes, edges, {})).toBe(false);
  });

  it('requires confirmation for the target node itself, not just its predecessors', () => {
    // target_node runs THROUGH the clicked node (interrupt_after) -- an
    // agent kind AT the target is actually executed by this click.
    const nodes = [{ id: 'a', kind: 'eval' }, { id: 'b', kind: 'agent' }];
    const edges = [{ from: 'a', to: 'b', level: 'normal' }];
    expect(nodeRequiresRunConfirmation('b', nodes, edges, {})).toBe(true);
  });

  it('does not require confirmation once the side-effecting predecessor already ran this session', () => {
    const nodes = [{ id: 'a', kind: 'agent' }, { id: 'b', kind: 'noop' }];
    const edges = [{ from: 'a', to: 'b', level: 'normal' }];
    const runMeta = { a: [{ duration_ms: 10 }] };
    expect(nodeRequiresRunConfirmation('b', nodes, edges, runMeta)).toBe(false);
  });

  it('requires confirmation for a human node predecessor', () => {
    const nodes = [{ id: 'a', kind: 'human' }, { id: 'b', kind: 'noop' }];
    const edges = [{ from: 'a', to: 'b', level: 'normal' }];
    expect(nodeRequiresRunConfirmation('b', nodes, edges, {})).toBe(true);
  });
});

describe('deriveActiveNodeIds', () => {
  const nodes = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];

  it('offers only entry when nothing has completed yet', () => {
    const edges = [{ from: 'a', to: 'b', level: 'normal' }];
    expect(deriveActiveNodeIds(nodes, edges, 'a', new Set())).toEqual(new Set(['a']));
  });

  it('advances to the next node once its predecessor completed', () => {
    const edges = [
      { from: 'a', to: 'b', level: 'normal' },
      { from: 'b', to: 'c', level: 'normal' },
    ];
    expect(deriveActiveNodeIds(nodes, edges, 'a', new Set(['a']))).toEqual(new Set(['b']));
  });

  it('offers nothing once every reachable node has completed', () => {
    const edges = [{ from: 'a', to: 'b', level: 'normal' }];
    expect(deriveActiveNodeIds(nodes, edges, 'a', new Set(['a', 'b']))).toEqual(new Set());
  });

  it('offers every branch of a router fork whose source just completed -- honest uncertainty, not a guess', () => {
    const edges = [
      {
        from: 'a',
        level: 'route',
        router: true,
        branches: { yes: 'b', no: 'c' },
      },
    ];
    expect(deriveActiveNodeIds(nodes, edges, 'a', new Set(['a']))).toEqual(new Set(['b', 'c']));
  });

  it('returns nothing for an entry the node list does not contain', () => {
    expect(deriveActiveNodeIds(nodes, [], 'missing', new Set())).toEqual(new Set());
  });
});

// Regression coverage for the router-fork rail bug: the "+ Add branch" slot
// is always appended after every real lane when the fork is editable (see
// ForkBlock in RoutineChain.jsx), so the right rail terminator must land on
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

  it('flags a Reduce node whose source is missing', () => {
    const nodes = [
      { id: 'reduce', kind: 'reduce', config: { source_node: 'gone' } },
    ];
    expect(validateWorkingGraph(nodes, [], 'reduce')).toContain(
      'Reduce step "reduce" references unknown source node "gone".',
    );
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

  // Deleting a withheld step restores the last-saved signature, so no save is
  // queued to clear the chip -- it read "Not saved · 0 steps incomplete".
  it('drops a withheld save once the step holding it back is gone', () => {
    expect(saveStatusWhenInSync('incomplete')).toBe('idle');
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
  it('recognizes Reduce configuration', () => {
    expect(makeNode('reduce').config).toEqual({ source_node: '', operation: 'collect' });
    expect(missingRequiredConfig({ kind: 'reduce', config: {} })).toEqual(['source_node']);
    expect(missingRequiredConfig({ kind: 'reduce', config: { source_node: 'mapped' } })).toEqual([]);
  });

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

  it('requires an expression for switch, same one-of shape as condition/eval', () => {
    expect(missingRequirements({ kind: 'switch', config: {} })).toEqual([['expression', 'expression_file']]);
    expect(missingRequirements({ kind: 'switch', config: { expression: 'category' } })).toEqual([]);
  });

  it('requires nothing for split -- a structural parallel gateway, not an expression node', () => {
    expect(missingRequirements({ kind: 'split', config: {} })).toEqual([]);
  });
});

// The inspector is held open on an incomplete step: it drives the disabled
// close button and the ignored background click, and the same predicate badges
// the card so a step left incomplete by the JSON editor is still findable.
describe('isNodeComplete', () => {
  it('holds the inspector on a step the backend would reject', () => {
    expect(isNodeComplete({ kind: 'prompt', config: { prompt_template: '' } })).toBe(false);
  });

  it('releases it as soon as the requirement is met', () => {
    expect(isNodeComplete({ kind: 'prompt', config: { prompt_template: 'Summarise.' } })).toBe(true);
  });

  it('never holds a step that requires nothing', () => {
    expect(isNodeComplete({ kind: 'noop', config: {} })).toBe(true);
  });

  it('still holds when only some of several requirements are met', () => {
    expect(isNodeComplete({ kind: 'classify', config: { rubric_prompt: 'Rate it' } })).toBe(false);
  });

  it('a fresh split is complete on insert, same as noop', () => {
    expect(isNodeComplete({ kind: 'split', config: {} })).toBe(true);
  });
});

// repo-setup-worker: `classify` forks to `prepare` and `escalate`, and
// `escalate` forks again. The outer fork offered no `+` to join on, because
// the walk gave up the moment a lane branched -- leaving no way at all to add
// a step after it, while the inner fork offered one.
describe('joinTerminals', () => {
  const outer = {
    from: 'classify',
    level: 'route',
    router: 'state_equals',
    state_key: 'setup_state.state',
    branches: { ready: 'prepare', blocked: 'escalate' },
  };
  const inner = {
    from: 'escalate',
    level: 'route',
    router: 'state_equals',
    state_key: 'blocker_severity.severity',
    branches: { minor: 'log_only', critical: 'page_oncall' },
  };

  it('gathers a nested fork\'s own ends for the outer fork', () => {
    expect(joinTerminals([outer, inner], 'classify')).toEqual(['prepare', 'log_only', 'page_oncall']);
  });

  it('still joins the nested fork on its own', () => {
    expect(joinTerminals([outer, inner], 'escalate')).toEqual(['log_only', 'page_oncall']);
  });

  it('walks a multi-step lane to the step that actually ends it', () => {
    const edges = [
      { from: 'gate', level: 'route', router: 'state_equals', state_key: 'k', branches: { a: 'a1', b: 'b1' } },
      { from: 'a1', to: 'a2', level: 'normal' },
    ];
    expect(joinTerminals(edges, 'gate')).toEqual(['a2', 'b1']);
  });

  it('offers nothing once the lanes already converge on a join', () => {
    const edges = [
      { from: 'gate', level: 'route', router: 'state_equals', state_key: 'k', branches: { a: 'a1', b: 'b1' } },
      { from: ['a1', 'b1'], to: 'after', level: 'fan_in' },
    ];
    expect(joinTerminals(edges, 'gate')).toEqual([]);
  });

  it('offers nothing when a lane loops back into the fork', () => {
    const edges = [
      { from: 'gate', level: 'route', router: 'state_equals', state_key: 'k', branches: { a: 'a1', b: 'b1' } },
      { from: 'a1', to: 'gate', level: 'normal' },
    ];
    expect(joinTerminals(edges, 'gate')).toEqual([]);
  });

  it('offers nothing for a node that is not a fork', () => {
    expect(joinTerminals([{ from: 'a', to: 'b', level: 'normal' }], 'a')).toEqual([]);
  });
});

describe('isFlowEdge', () => {
  it('returns true for normal, fan_in, and route edges', () => {
    expect(isFlowEdge({ from: 'a', to: 'b', level: 'normal' })).toBe(true);
    expect(isFlowEdge({ from: ['a', 'b'], to: 'c', level: 'fan_in' })).toBe(true);
    expect(isFlowEdge({ from: 'a', level: 'route', router: 'state_equals', branches: {} })).toBe(true);
  });

  it('returns false for error and hidden foreach-empty edges', () => {
    expect(isFlowEdge({ from: 'a', to: 'b', level: 'error' })).toBe(false);
    expect(isFlowEdge({ from: 'fan', to: 'join', level: 'foreach_empty' })).toBe(false);
    expect(isFlowEdge(null)).toBe(true);
    expect(isFlowEdge(undefined)).toBe(true);
  });

  it('includes hidden foreach-empty destinations in the possible run frontier', () => {
    const edges = [
      { from: 'fan', to: 'body', level: 'normal' },
      { from: 'fan', to: 'join', level: 'foreach_empty' },
    ];
    expect(nodeSuccessors('fan', edges)).toEqual(new Set(['body', 'join']));
  });
});

describe('error edges in validation', () => {
  const makeNode = (id) => ({ id, kind: 'prompt', config: { prompt_template: 'ok' } });

  it('does NOT trip the one-outgoing-edge rule when a node has one flow edge and one error edge', () => {
    const nodes = [makeNode('a'), makeNode('b'), makeNode('c')];
    const edges = [
      { from: 'a', to: 'b', level: 'normal' },
      { from: 'a', to: 'c', level: 'error' },
    ];
    expect(validateWorkingGraph(nodes, edges, 'a')).toEqual([]);
  });

  it('reports a node with two error edges', () => {
    const nodes = [makeNode('a'), makeNode('b'), makeNode('c')];
    const edges = [
      { from: 'a', to: 'b', level: 'error' },
      { from: 'a', to: 'c', level: 'error' },
    ];
    expect(validateWorkingGraph(nodes, edges, 'a')).toContain('"a" has more than one error edge.');
  });

  it('reports a self-loop error edge', () => {
    const nodes = [makeNode('a'), makeNode('b')];
    const edges = [
      { from: 'a', to: 'a', level: 'error' },
      { from: 'a', to: 'b', level: 'normal' },
    ];
    expect(validateWorkingGraph(nodes, edges, 'a')).toContain('"a" cannot route its own errors to itself.');
  });

  it('does not report an error-only-reachable node as unreachable', () => {
    const nodes = [makeNode('a'), makeNode('b')];
    const edges = [
      { from: 'a', to: 'b', level: 'error' },
    ];
    expect(validateWorkingGraph(nodes, edges, 'a')).toEqual([]);
  });

  it('still flags a genuinely missing error edge target', () => {
    const nodes = [makeNode('a')];
    const edges = [{ from: 'a', to: 'missing', level: 'error' }];
    expect(validateWorkingGraph(nodes, edges, 'a')).toContain('Edge target "missing" is missing.');
  });
});

describe('error edges in chain tree building', () => {
  // The chain renderer builds its tree from edgeBySourceMap, which must
  // exclude error edges so they do not corrupt fork/join detection.
  it('builds the same tree whether or not nodes carry error edges', () => {
    const router = {
      from: 'gate',
      level: 'route',
      router: 'state_equals',
      state_key: 'k',
      branches: { a: 'left', b: 'right' },
    };
    const leftToJoin = { from: 'left', to: 'join', level: 'normal' };
    const rightToJoin = { from: 'right', to: 'join', level: 'normal' };
    const errorFromLeft = { from: 'left', to: 'recover', level: 'error' };

    // Both lanes already converge on 'join', so there is nothing to bring
    // together -- joinTerminals returns []. If edgeBySourceMap wrongly
    // includes error edges, 'left' maps to errorFromLeft (the last edge that
    // sources from it), so the walk from 'left' goes to 'recover' instead of
    // 'join', and joinTerminals would incorrectly return ['recover', 'join'].
    const plain = [router, leftToJoin, rightToJoin];
    const withErrorLast = [router, leftToJoin, rightToJoin, errorFromLeft];
    expect(joinTerminals(plain, 'gate')).toEqual([]);
    expect(joinTerminals(withErrorLast, 'gate')).toEqual([]);
  });
});

// Select's response_schema is authored as one "name: type[ #description]"
// line per field rather than hand-written JSON Schema -- textToSchemaFields
// must produce a strict-mode-compatible schema (every field required,
// additionalProperties: false), and schemaFieldsToText must read an existing
// schema back into the same line format so the field round-trips.
describe('textToSchemaFields', () => {
  it('marks every field required and forbids extra properties, per structured-output strict mode', () => {
    expect(textToSchemaFields('category: string\nisUrgent: boolean')).toEqual({
      type: 'object',
      properties: { category: { type: 'string' }, isUrgent: { type: 'boolean' } },
      required: ['category', 'isUrgent'],
      additionalProperties: false,
    });
  });

  it('accepts shorthand type aliases', () => {
    expect(textToSchemaFields('n: int\nx: bool\ny: num\nz: str')).toEqual({
      type: 'object',
      properties: {
        n: { type: 'integer' },
        x: { type: 'boolean' },
        y: { type: 'number' },
        z: { type: 'string' },
      },
      required: ['n', 'x', 'y', 'z'],
      additionalProperties: false,
    });
  });

  it('parses an enum(...) type into a string with an enum constraint', () => {
    const schema = textToSchemaFields('category: enum(billing, technical, other)');
    expect(schema.properties.category).toEqual({ type: 'string', enum: ['billing', 'technical', 'other'] });
  });

  it('parses a trailing "type[]" as an array of that item type', () => {
    expect(textToSchemaFields('tags: string[]').properties.tags).toEqual({
      type: 'array',
      items: { type: 'string' },
    });
  });

  it('keeps the text after the first "#" as the field description', () => {
    expect(textToSchemaFields('summary: string #one sentence explaining why').properties.summary).toEqual({
      type: 'string',
      description: 'one sentence explaining why',
    });
  });

  it('does not let a description split an enum(...) on its internal commas', () => {
    const schema = textToSchemaFields('category: enum(billing, technical, other) #which team owns this');
    expect(schema.properties.category).toEqual({
      type: 'string',
      enum: ['billing', 'technical', 'other'],
      description: 'which team owns this',
    });
  });


  it('falls back an unrecognized type to string rather than emitting an invalid schema', () => {
    expect(textToSchemaFields('x: widget').properties.x).toEqual({ type: 'string' });
  });

  it('ignores blank lines and lines with no name', () => {
    expect(textToSchemaFields('\n: string\ncategory: string\n')).toEqual({
      type: 'object',
      properties: { category: { type: 'string' } },
      required: ['category'],
      additionalProperties: false,
    });
  });
});

describe('schemaFieldsToText', () => {
  it('round-trips a schema produced by textToSchemaFields back to the same field lines', () => {
    const original = 'category: enum(billing, technical)\nisUrgent: boolean\nsummary: string # one sentence explaining why';
    expect(schemaFieldsToText(textToSchemaFields(original))).toBe(original);
  });

  it('renders an array-of-string property back as "type[]"', () => {
    expect(schemaFieldsToText({ type: 'object', properties: { tags: { type: 'array', items: { type: 'string' } } } }))
      .toBe('tags: string[]');
  });

  it('returns empty text for a schema with no properties, e.g. the unset default {}', () => {
    expect(schemaFieldsToText({})).toBe('');
    expect(schemaFieldsToText(undefined)).toBe('');
  });
});

// The Loop construct's own tail edge (see insertAt's `kind === 'loop'`
// branch): a fresh loop starts with a blank `loop_guard.expression`, which
// the backend's LoopGuardConfig rejects outright (min_length=1) -- this is
// the gate that withholds autosave instead of sending an invalid payload.
describe('incompleteLoopEdges', () => {
  it('flags a loop_guard edge with a blank expression', () => {
    const edges = [{ from: 'tail', to: 'loop', level: 'normal', loop_guard: { expression: '', inputs: {} } }];
    expect(incompleteLoopEdges(edges)).toEqual(edges);
  });

  it('flags a loop_guard edge whose expression is only whitespace', () => {
    const edges = [{ from: 'tail', to: 'loop', level: 'normal', loop_guard: { expression: '   ', inputs: {} } }];
    expect(incompleteLoopEdges(edges)).toEqual(edges);
  });

  it('does not flag a loop_guard edge with a real expression', () => {
    const edges = [{ from: 'tail', to: 'loop', level: 'normal', loop_guard: { expression: 'attempt < 3', inputs: {} } }];
    expect(incompleteLoopEdges(edges)).toEqual([]);
  });

  it('ignores plain edges entirely', () => {
    const edges = [{ from: 'a', to: 'b', level: 'normal' }];
    expect(incompleteLoopEdges(edges)).toEqual([]);
  });

  it('handles an empty or missing edge list', () => {
    expect(incompleteLoopEdges([])).toEqual([]);
    expect(incompleteLoopEdges(undefined)).toEqual([]);
  });
});

// A Loop's exit (`loop_guard.on_false`) is a second, real path out of the
// tail -- UnreachableTray must not flag whatever it points at just because
// the primary `to` (the loop's own start) is the only target a plain
// normal edge usually has.
describe('reachableFrom with loop_guard edges', () => {
  it('treats loop_guard.on_false as reachable, not just the primary to', () => {
    const edges = [
      { from: 'a', to: 'tail', level: 'normal' },
      { from: 'tail', to: 'a', level: 'normal', loop_guard: { expression: 'x', inputs: {}, on_false: 'exit' } },
    ];
    const reachable = reachableFrom('a', edges);
    expect(reachable.has('exit')).toBe(true);
  });

  it('still reaches the loop body itself via the primary to', () => {
    const edges = [
      { from: 'a', to: 'tail', level: 'normal' },
      { from: 'tail', to: 'a', level: 'normal', loop_guard: { expression: 'x', inputs: {} } },
    ];
    const reachable = reachableFrom('a', edges);
    expect(reachable.has('a')).toBe(true);
    expect(reachable.has('tail')).toBe(true);
  });

  it('does not choke on a loop_guard with no on_false at all', () => {
    const edges = [
      { from: 'a', to: 'tail', level: 'normal' },
      { from: 'tail', to: 'a', level: 'normal', loop_guard: { expression: 'x', inputs: {} } },
    ];
    expect(() => reachableFrom('a', edges)).not.toThrow();
  });
});

describe('predecessorsOf', () => {
  it('walks a straight chain backward', () => {
    const edges = [
      { from: 'a', to: 'b', level: 'normal' },
      { from: 'b', to: 'c', level: 'normal' },
    ];
    const predecessors = predecessorsOf('c', edges);
    expect(predecessors.has('a')).toBe(true);
    expect(predecessors.has('b')).toBe(true);
  });

  it('never includes the node itself, even inside a loop cycle', () => {
    const edges = [
      { from: 'start', to: 'tail', level: 'normal' },
      { from: 'tail', to: 'start', level: 'normal', loop_guard: { expression: 'x', on_false: 'exit' } },
    ];
    const predecessors = predecessorsOf('start', edges);
    expect(predecessors.has('start')).toBe(false);
    expect(predecessors.has('tail')).toBe(true);
  });

  it('walks backward through a router edges branches', () => {
    const edges = [
      { from: 'router', router: true, branches: { yes: 'a', no: 'b' }, default: 'c' },
    ];
    expect(predecessorsOf('a', edges).has('router')).toBe(true);
    expect(predecessorsOf('c', edges).has('router')).toBe(true);
  });

  it('excludes error-recovery edges from the predecessor set', () => {
    const edges = [{ from: 'a', to: 'b', level: 'error' }];
    expect(predecessorsOf('b', edges).has('a')).toBe(false);
  });

  it('walks backward through a fan-in edge with multiple sources', () => {
    const edges = [{ from: ['a', 'b'], to: 'join', level: 'fan_in' }];
    const predecessors = predecessorsOf('join', edges);
    expect(predecessors.has('a')).toBe(true);
    expect(predecessors.has('b')).toBe(true);
  });
});

describe('buildStatePathSuggestions', () => {
  it('offers background task references under the output key rather than eventual response fields', () => {
    const nodes = [
      { id: 'delegate', output_key: 'task', kind: 'agent', config: {
        execution: 'background', response_schema: { properties: { answer: { type: 'string' } } },
      } },
      { id: 'after', kind: 'eval', config: {} },
    ];
    const paths = buildStatePathSuggestions('after', nodes, [{ from: 'delegate', to: 'after' }], null).map((entry) => entry.path);
    expect(paths).toContain('task.run_id');
    expect(paths).toContain('task.task_id');
    expect(paths).not.toContain('task.answer');
    expect(paths).not.toContain('delegate.run_id');
  });
  const nodes = [
    { id: 'classify_intent', kind: 'classify', label: 'Classify intent', config: { response_schema: { properties: { label: { type: 'string' }, confidence: { type: 'number' } } } } },
    { id: 'compute_score', kind: 'eval', label: 'Compute score', config: {} },
    { id: 'this_node', kind: 'condition', label: 'Gate', config: {} },
  ];
  const edges = [
    { from: 'classify_intent', to: 'compute_score', level: 'normal' },
    { from: 'compute_score', to: 'this_node', level: 'normal' },
  ];

  it('always offers the seeded keys and declared routine inputs first', () => {
    const suggestions = buildStatePathSuggestions('this_node', nodes, edges, { properties: { host: { type: 'string', description: 'target host' } } });
    expect(suggestions.map((s) => s.path).slice(0, 3)).toEqual(['request', 'context', 'host']);
  });

  it('offers a bare key for a predecessor with no declared schema', () => {
    const suggestions = buildStatePathSuggestions('this_node', nodes, edges, null);
    const paths = suggestions.map((s) => s.path);
    expect(paths).toContain('compute_score');
    expect(paths).not.toContain('compute_score.result');
  });

  it('drills into a predecessors response_schema properties', () => {
    const suggestions = buildStatePathSuggestions('this_node', nodes, edges, null);
    const paths = suggestions.map((s) => s.path);
    expect(paths).toContain('classify_intent');
    expect(paths).toContain('classify_intent.label');
    expect(paths).toContain('classify_intent.confidence');
  });

  it('derives the local alias name from the last dotted segment', () => {
    const suggestions = buildStatePathSuggestions('this_node', nodes, edges, null);
    const label = suggestions.find((s) => s.path === 'classify_intent.label');
    expect(label.name).toBe('label');
    const bare = suggestions.find((s) => s.path === 'classify_intent');
    expect(bare.name).toBe('classify_intent');
  });

  it('never suggests the node currently being edited', () => {
    const suggestions = buildStatePathSuggestions('this_node', nodes, edges, null);
    expect(suggestions.map((s) => s.path)).not.toContain('this_node');
  });
});


describe('filterStatePathSuggestions', () => {
  const suggestions = [
    { path: 'classify_intent.label', name: 'label' },
    { path: 'classify_intent', name: 'classify_intent' },
    { path: 'compute_score', name: 'compute_score' },
  ];

  it('filters by substring match on the path, case-insensitively', () => {
    const filtered = filterStatePathSuggestions(suggestions, 'CLASS');
    expect(filtered.map((s) => s.path)).toEqual(['classify_intent.label', 'classify_intent']);
  });

  it('ranks a startsWith match ahead of a mid-string match', () => {
    const withMidMatch = [...suggestions, { path: 'x_compute_score', name: 'x_compute_score' }];
    const filtered = filterStatePathSuggestions(withMidMatch, 'compute');
    expect(filtered.map((s) => s.path)).toEqual(['compute_score', 'x_compute_score']);
  });

  it('keeps the bare object itself in the list on a dangling-dot query, even for a schema-less node', () => {
    const bareOnly = [
      { path: 'request', name: 'request' },
      { path: 'context', name: 'context' },
      { path: 'prompt', name: 'prompt' },
    ];
    const filtered = filterStatePathSuggestions(bareOnly, 'prompt.');
    expect(filtered.map((s) => s.path)).toEqual(['prompt']);
  });

  it('ranks a dangling-dots bare-object fallback after real property matches', () => {
    const filtered = filterStatePathSuggestions(suggestions, 'classify_intent.');
    expect(filtered.map((s) => s.path)).toEqual(['classify_intent.label', 'classify_intent']);
  });

  it('does not fall back to a bare match for a query that is not dangling-dot-terminated', () => {
    const filtered = filterStatePathSuggestions(suggestions, 'compute_score');
    expect(filtered.map((s) => s.path)).toEqual(['compute_score']);
  });
});

describe('getExpressionAutocompleteContext', () => {
  it('returns an empty-query context with nothing typed -- the caller opens the menu on focus, not on this', () => {
    expect(getExpressionAutocompleteContext('', 0)).toEqual({ query: '', tokenStart: 0, tokenEnd: 0 });
  });

  it('returns an empty-query context right after whitespace or an operator', () => {
    expect(getExpressionAutocompleteContext('count + ', 8)).toEqual({ query: '', tokenStart: 8, tokenEnd: 8 });
    expect(getExpressionAutocompleteContext('count + ', 6)).toEqual({ query: '', tokenStart: 6, tokenEnd: 6 });
  });

  it('detects a bare identifier under the caret', () => {
    const context = getExpressionAutocompleteContext('cou', 3);
    expect(context).toEqual({ query: 'cou', tokenStart: 0, tokenEnd: 3 });
  });

  it('detects a dotted attribute chain in progress', () => {
    const text = 'classify_intent.la';
    const context = getExpressionAutocompleteContext(text, text.length);
    expect(context).toEqual({ query: 'classify_intent.la', tokenStart: 0, tokenEnd: text.length });
  });

  it('only considers the identifier chain ending at the caret, not earlier text', () => {
    const text = 'count < 3 and clas';
    const context = getExpressionAutocompleteContext(text, text.length);
    expect(context).toEqual({ query: 'clas', tokenStart: text.indexOf('clas'), tokenEnd: text.length });
  });

  it('returns null right after a closed string or number literal', () => {
    const stringText = 'attempt == "retry"';
    expect(getExpressionAutocompleteContext(stringText, stringText.length)).toBeNull();
    expect(getExpressionAutocompleteContext('count < 3', 9)).toBeNull();
  });

  it('returns null while the caret sits inside an unterminated string literal', () => {
    expect(getExpressionAutocompleteContext('attempt == "re', 14)).toBeNull();
  });

  it('a dangling dot keeps it as part of the query, narrowing to that predecessors own properties', () => {
    const text = 'classify_intent.';
    const context = getExpressionAutocompleteContext(text, text.length);
    expect(context).toEqual({ query: 'classify_intent.', tokenStart: 0, tokenEnd: text.length });
  });

  it('clamps a caret position past the end of the text instead of misreading it', () => {
    expect(getExpressionAutocompleteContext('count', 999)).toEqual({ query: 'count', tokenStart: 0, tokenEnd: 5 });
  });
});

describe('buildLoopMembership', () => {
  const loopEdges = [
    { from: 'start', to: 'tail', level: 'normal' },
    { from: 'tail', to: 'start', level: 'normal', loop_guard: { expression: 'count < 3' } },
  ];

  it("claims every node in the body, excluding the loop's own start card", () => {
    const membership = buildLoopMembership(loopEdges);
    expect(membership.has('start')).toBe(false);
    expect(membership.get('tail')).toBe(loopEdges[1]);
  });

  it('claims a router-branch node inside the loop body too', () => {
    const edges = [
      { from: 'start', to: 'gate', level: 'normal' },
      {
        from: 'gate',
        level: 'route',
        router: 'state_equals',
        state_key: 'x',
        branches: { true: 'left', false: 'right' },
        default: 'tail',
      },
      { from: 'left', to: 'tail', level: 'normal' },
      { from: 'right', to: 'tail', level: 'normal' },
      { from: 'tail', to: 'start', level: 'normal', loop_guard: { expression: 'count < 3' } },
    ];
    const membership = buildLoopMembership(edges);
    expect(membership.get('gate')).toBe(edges[4]);
    expect(membership.get('left')).toBe(edges[4]);
    expect(membership.get('right')).toBe(edges[4]);
  });

  it('returns an empty map for a graph with no guarded loop', () => {
    const edges = [{ from: 'a', to: 'b', level: 'normal' }];
    expect(buildLoopMembership(edges).size).toBe(0);
  });
});

describe('pickEntryForIteration', () => {
  const guardEntries = [{ step: 3 }, { step: 6 }, { step: 9 }];

  it('returns the last entry when no iteration is explicitly selected', () => {
    const entries = [{ step: 2 }, { step: 5 }, { step: 8 }];
    expect(pickEntryForIteration(entries, null, guardEntries)).toBe(entries[2]);
  });

  it("picks the entry whose step falls within the selected iteration's window", () => {
    const entries = [{ step: 2 }, { step: 5 }, { step: 8 }];
    expect(pickEntryForIteration(entries, 1, guardEntries)).toBe(entries[0]);
    expect(pickEntryForIteration(entries, 2, guardEntries)).toBe(entries[1]);
    expect(pickEntryForIteration(entries, 3, guardEntries)).toBe(entries[2]);
  });

  it('falls back to the last entry when nothing ran in that window (node skipped that pass)', () => {
    const entries = [{ step: 2 }, { step: 8 }];
    expect(pickEntryForIteration(entries, 2, guardEntries)).toBe(entries[1]);
  });

  it('returns undefined for an empty entries list', () => {
    expect(pickEntryForIteration([], 1, guardEntries)).toBeUndefined();
    expect(pickEntryForIteration(undefined, 1, guardEntries)).toBeUndefined();
  });
});
