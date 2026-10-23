import { CONDITION_BRANCHES } from './graphNodeKinds';

// One edge record can encode a normal transition, a static fan-out, or a
// barrier fan-in. Indexing every listed source lets the same tree walk render
// a fan-in's shared destination once rather than pretending each source owns
// a separate normal edge.
export function edgeSourceIds(edge) {
  return Array.isArray(edge?.from) ? edge.from : [edge?.from];
}

// Flow edges are the ones that shape the chain tree; error edges route
// retries on exhaustion and must NOT participate in chain layout.
export function isFlowEdge(edge) {
  return edge?.level !== 'error' && edge?.level !== 'foreach_empty';
}

function edgeTargetIds(edge) {
  if (!edge?.to) return [];
  return Array.isArray(edge.to) ? edge.to : [edge.to];
}

export function edgeBySourceMap(edges) {
  return new Map(
    edges
      .filter((edge) => edge && edge.from && isFlowEdge(edge))
      .flatMap((edge) => edgeSourceIds(edge).map((source) => [source, edge])),
  );
}

export function routerTargets(edge) {
  return [...Object.values(edge?.branches || {}), ...(edge?.default ? [edge.default] : [])];
}

// nodeId -> the guard edge of its nearest enclosing guarded loop, for every
// node inside a loop's body box -- excludes the loop's own start card, the
// same boundary RoutineChain's own render walk uses (see
// `positions.get(bodyStartId) + 1` skipping past it below). Nested loops
// keep whichever guard's walk reaches a node last; nesting is rare and not
// disambiguated further here.
export function buildLoopMembership(edges) {
  const bySource = edgeBySourceMap(edges || []);
  const membership = new Map();
  (edges || []).forEach((edge) => {
    if (!edge?.loop_guard) return;
    const startId = edgeTargetIds(edge)[0];
    const tailId = edge.from;
    if (!startId || !tailId) return;
    const seen = new Set([startId]);
    const stack = [startId];
    while (stack.length) {
      const id = stack.pop();
      if (id !== startId) membership.set(id, edge);
      if (id === tailId) continue;
      const next = bySource.get(id);
      if (!next) continue;
      const targets = next.router ? routerTargets(next) : edgeTargetIds(next);
      targets.forEach((target) => {
        if (target && !seen.has(target)) {
          seen.add(target);
          stack.push(target);
        }
      });
    }
  });
  return membership;
}

// Every step a join placed after this fork would fan in from: each lane walked
// to its end, descending through a nested fork so a lane that itself branches
// contributes all of its own ends instead of disqualifying the whole fork.
// Empty when there is nothing to bring together -- a lane that loops back,
// rejoins content already on the chain, or converges with a sibling leaves the
// fork without two distinct ends. The offer and the action both read this, so
// a fork that shows a `+` is exactly one `onJoinBranches` can wire up.
export function joinTerminals(edges, sourceId) {
  const router = edges.find((edge) => edge.from === sourceId && edge.router);
  if (!router) return [];
  const bySource = edgeBySourceMap(edges);
  const seen = new Set([sourceId]);
  const walk = (nodeId) => {
    if (!nodeId || seen.has(nodeId)) return null;
    seen.add(nodeId);
    const outgoing = bySource.get(nodeId);
    if (!outgoing) return [nodeId];
    if (outgoing.router) {
      const lanes = routerTargets(outgoing).map(walk);
      return lanes.some((lane) => lane === null) ? null : lanes.flat();
    }
    return walk(edgeTargetIds(outgoing)[0]);
  };
  const lanes = routerTargets(router).map(walk);
  if (lanes.some((lane) => lane === null)) return [];
  const terminals = [...new Set(lanes.flat())];
  return terminals.length > 1 ? terminals : [];
}

// Every node that all paths out of `startId` must pass through, nearest first.
// A plain chain is just its own steps. Where the chain forks, only what all of
// the fork's branches share survives -- which is precisely what makes a node a
// join for everything above it. Without descending, a lane that branches again
// contributed nothing and the outer fork could never find its join.
function dominatingPath(startId, edgeBySource, seen = new Set()) {
  const path = [];
  let current = startId;
  while (current && !seen.has(current)) {
    path.push(current);
    seen.add(current);
    const edge = edgeBySource.get(current);
    if (!edge) return path;
    if (edge.router) {
      // Each branch gets its own `seen`: sharing one would let the first
      // branch claim nodes the others also pass through, and those are exactly
      // the shared nodes the intersection is looking for.
      const branches = routerTargets(edge)
        .map((target) => dominatingPath(target, edgeBySource, new Set(seen)));
      if (!branches.length) return path;
      const [first, ...rest] = branches;
      return [...path, ...first.filter((id) => rest.every((branch) => branch.includes(id)))];
    }
    [current] = edgeTargetIds(edge);
  }
  return path;
}

function findCommonJoin(targetIds, edgeBySource) {
  if (targetIds.length < 2) return null;
  const paths = targetIds.map((targetId) => dominatingPath(targetId, edgeBySource));
  return paths[0].find((candidate) => paths.every((path) => path.includes(candidate))) || null;
}

// The fan_in a fork's lanes already converge on, if any. `findCommonJoin`
// only accepts a node every lane reaches, so adding or dropping a lane has to
// keep that edge in step: one unjoined lane drops the common node to null and
// the join stops rendering below the fork at all. At runtime it is worse than
// cosmetic -- the factory gives any node with no outgoing edge an edge
// straight to the terminal, so taking that branch ends the routine early
// instead of continuing past the fork.
export function forkJoin(edges, router) {
  const edgeBySource = edgeBySourceMap(edges);
  const joinId = findCommonJoin(routerTargets(router), edgeBySource);
  if (!joinId) return null;
  const edge = edges.find((candidate) => candidate.level === 'fan_in' && candidate.to === joinId);
  return edge ? { joinId, edge, edgeBySource } : null;
}

// Every node the graph can still get to from `entry`. Shared by validation and
// by lane removal, which decides what a dropped lane owned by diffing this
// before and after the drop.
//
// This walks BOTH flow edges and error edges: a node reachable only as an
// error edge target must NOT be reported unreachable. Chain-shape walks
// (buildChainTree, dominatingPath) exclude error edges via edgeBySourceMap.
export function reachableFrom(entry, edges) {
  const reachable = new Set();
  const visit = (id) => {
    if (!id || reachable.has(id)) return;
    reachable.add(id);
    edges.filter((edge) => edgeSourceIds(edge).includes(id)).forEach((edge) => {
      if (edge.router) {
        Object.values(edge.branches || {}).forEach(visit);
        visit(edge.default);
      } else {
        visit(edge.to);
        if (edge.loop_guard) visit(edge.loop_guard.on_false);
      }
    });
  };
  visit(entry);
  return reachable;
}

// The reverse of `reachableFrom`: every node whose output could have run
// before `nodeId` on SOME path through the graph -- used to offer Inputs
// autocomplete candidates. Deliberately excludes error edges (isFlowEdge)
// unlike `reachableFrom`: a node only reachable via a recovery path isn't a
// normal-path predecessor an author should be pointed at by an autocomplete
// menu. This is a superset, not a soundness proof -- graph reachability
// can't tell "always ran before you" from "reachable on some branch/loop
// path", so a branch sibling or a not-yet-taken loop pass can still show up
// even though it may not actually be populated at runtime. Cheaper to over-
// suggest a sometimes-absent name than to hide a valid one.
export function predecessorsOf(nodeId, edges) {
  const flowEdges = (edges || []).filter(isFlowEdge);
  const predecessors = new Set();
  const visit = (id) => {
    flowEdges.forEach((edge) => {
      const targets = edge.router
        ? routerTargets(edge)
        : [...edgeTargetIds(edge), ...(edge.loop_guard?.on_false ? [edge.loop_guard.on_false] : [])];
      if (!targets.includes(id)) return;
      edgeSourceIds(edge).forEach((src) => {
        if (!src || src === nodeId || predecessors.has(src)) return;
        predecessors.add(src);
        visit(src);
      });
    });
  };
  visit(nodeId);
  return predecessors;
}

// One step forward: every node `nodeId`'s own outgoing flow edges could
// hand off to next, router branches included. The reverse of
// `predecessorsOf`'s single-hop core, factored out because
// `deriveActiveNodeIds` below only ever needs one hop at a time (it starts
// from a whole *set* of completed nodes, not a single id to walk outward
// from).
export function nodeSuccessors(nodeId, edges) {
  const targets = new Set();
  (edges || []).filter((edge) => isFlowEdge(edge) || edge?.level === 'foreach_empty').forEach((edge) => {
    if (!edgeSourceIds(edge).includes(nodeId)) return;
    if (edge.router) {
      routerTargets(edge).forEach((target) => target && targets.add(target));
    } else {
      edgeTargetIds(edge).forEach((target) => target && targets.add(target));
      if (edge.loop_guard?.on_false) targets.add(edge.loop_guard.on_false);
    }
  });
  return targets;
}

// Builds the whole reachable chain as a plain data tree in one synchronous,
// side-effect-free pass -- BEFORE any JSX is produced. This has to happen up
// front rather than by mutating shared state from inside lazily-invoked
// React children (a fork's branches used to be rendered by calling this
// recursively from within <ForkBlock>): React may invoke a function
// component twice per commit (StrictMode), and a mutable `rendered`/
// `visiting` Set mutated as a side effect of rendering would leak state from
// a discarded first invocation into the real one, corrupting rejoin/cycle
// detection. Building the full tree as pure data first, then rendering that
// already-computed tree, sidesteps the whole class of bug.
export function buildChainTree(nodeId, nodesById, edgeBySource, visiting, rendered, stopAt = null) {
  if (!nodeId) return null;
  const node = nodesById.get(nodeId);
  if (!node) return null;
  if (nodeId === stopAt) return { type: 'join', id: nodeId, label: node.label || nodeId };
  if (visiting.has(nodeId)) return { type: 'loop', id: nodeId, label: node.label || nodeId };
  if (rendered.has(nodeId)) return { type: 'rejoin', id: nodeId, label: node.label || nodeId };
  rendered.add(nodeId);
  visiting.add(nodeId);
  const edge = edgeBySource.get(nodeId);
  let next = null;
  let fork = null;
  let loop = null;
  if (edge && edge.router) {
    const targetIds = routerTargets(edge);
    // A nested fork whose lanes converge on an ancestor's join does not own
    // that join -- it belongs to the fork above. Claiming it here would render
    // the shared step tucked inside one lane of the outer fork instead of
    // below the fork it actually closes.
    const commonJoin = findCommonJoin(targetIds, edgeBySource);
    const joinId = commonJoin === stopAt ? null : commonJoin;
    const branchStop = joinId || stopAt;
    // A condition's two arms are fixed, so render them in a stable
    // true-then-false order rather than whatever key order the document
    // happens to carry.
    const fixedBranches = node.kind === 'condition';
    const branches = fixedBranches
      ? CONDITION_BRANCHES.filter((value) => value in (edge.branches || {}))
        .map((value) => [value, edge.branches[value]])
      : Object.entries(edge.branches || {});
    fork = {
      sourceId: nodeId,
      edge,
      editable: true,
      fixedBranches,
      // `split`'s branches don't route on a matched value -- they all fire
      // unconditionally -- so their lane headers have nothing meaningful to
      // label or rename (see ForkBlock/BranchHeader).
      unconditional: node.kind === 'split',
      branches: branches.map(([value, targetId]) => ({
        value,
        tree: buildChainTree(targetId, nodesById, edgeBySource, visiting, rendered, branchStop),
      })),
      defaultTree: edge.default
        ? buildChainTree(edge.default, nodesById, edgeBySource, visiting, rendered, branchStop)
        : null,
      joinTree: joinId
        ? buildChainTree(joinId, nodesById, edgeBySource, visiting, rendered)
        : null,
    };
  } else if (edge && edge.loop_guard) {
    // The reached-while-`visiting` ancestor check above is what actually
    // resolves the cycle -- `edge.to` always leads back to this loop's own
    // start, which is necessarily still on the stack here, so there is
    // nothing to compute for that half. What IS new: the guard's own
    // on_false, a second, structural way out of this exact position -- the
    // loop's ordinary forward continuation -- computed the same way any
    // other "next" is.
    loop = {
      sourceId: nodeId,
      bodyStartId: edge.to,
      edge,
      exitTree: edge.loop_guard.on_false
        ? buildChainTree(edge.loop_guard.on_false, nodesById, edgeBySource, visiting, rendered, stopAt)
        : null,
    };
  } else {
    next = buildChainTree(edgeTargetIds(edge)[0], nodesById, edgeBySource, visiting, rendered, stopAt);
  }
  visiting.delete(nodeId);
  return { type: 'node', id: nodeId, node, next, fork, loop };
}

// Where a branch's chain ends once followed all the way down: a genuine graph
// END ('end'), a loop back ('loop'), a rejoin onto already-rendered content
// ('rejoin'), or an existing join ('join'). Used to decide which lanes a
// rendered join draws its fan-in rail from.
//
// A lane that itself branches ends wherever its own branches end, so descend:
// past the nested fork's join if it has one, otherwise into its lanes, which
// agree on an answer or leave the outer lane genuinely forked.
export function chainTerminus(tree) {
  let current = tree;
  while (current && current.type === 'node') {
    if (current.fork) {
      if (current.fork.joinTree) return chainTerminus(current.fork.joinTree);
      const inner = [
        ...current.fork.branches.map((branch) => branch.tree),
        ...(current.fork.defaultTree ? [current.fork.defaultTree] : []),
      ].map(chainTerminus);
      return inner.length && inner.every((terminus) => terminus === inner[0]) ? inner[0] : 'fork';
    }
    current = current.next;
  }
  return current === null ? 'end' : current.type;
}

// Renders an already-computed chain-tree run: a connector before each step,
// the step itself, and either the next connector, a fork, or a terminus.
// Pure -- safe to invoke from a React child any number of times.
export function splitForeachBody(tree) {
  if (!tree || tree.type !== 'node') return [tree, null];
  if (tree.node.kind === 'noop') return [null, tree];
  const [bodyNext, after] = splitForeachBody(tree.next);
  return [{ ...tree, next: bodyNext }, after];
}

// Which lane owns the left/right rail terminator (`--fanout-first` /
// `--fanout-last`, chrome-frame.css) that clips the horizontal connector
// stub to the lane's own centre instead of running off the fork's edge.
// The "+ Add branch" slot is appended after every real lane whenever the
// fork can grow one (see the block below the lanes.map call), so it -- not
// the last real lane -- owns the right terminator in that case; otherwise
// the last real lane is genuinely the rightmost rendered lane.
// Exported for unit coverage: a fork with N branches plus an "+ Add branch"
// slot must clip the rail at the slot, not one lane early.
export function forkLaneEdgePosition(index, lanesLength, hasAddSlot) {
  return {
    isFirst: index === 0,
    isLast: index === lanesLength - 1 && !hasAddSlot,
  };
}
