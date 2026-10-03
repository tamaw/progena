import React, { useContext, useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { GraphEditContext, EMPTY_MISSING } from './graphEditorContext';
import { pickEntryForIteration, formatSeconds } from './graphRunState';
import { isNodeRunValid } from '../../lib/routineGraphDiff';
import { isNodeComplete } from './graphValidation';
import { GRAPH_KIND_COLORS, GRAPH_KIND_LABELS, GRAPH_NODE_KINDS } from './graphNodeKinds';
import { KindIcon } from './RoutineGraphIcons';
import {
  splitForeachBody,
  chainTerminus,
  joinTerminals,
  forkLaneEdgePosition,
  edgeBySourceMap,
  buildChainTree,
} from './graphTopology';


// Ticks once a second while a node is presumed active (see
// deriveActiveNodeIds/estimatedActiveStartMs) -- isolated in its own
// component so only this one card re-renders each tick, not the whole
// canvas. This is a client-side estimate, not a real measured duration: it
// starts from the latest known finish time across the whole run (or the
// Run click itself, before anything has finished), since LangGraph's
// checkpointer only ever records a node's real duration_ms AFTER it
function RunningTimer({ startedAtMs }) {
  const [elapsedMs, setElapsedMs] = useState(0);
  useEffect(() => {
    let timer;
    const tick = () => {
      const next = Math.max(0, Date.now() - startedAtMs);
      setElapsedMs(next);
      // Sub-second precision (formatSeconds' tenths) only shows below the
      // 10s cutover -- tick fast enough to actually see it count up to
      // then, and back off to once a second after, so a long-running node
      // doesn't re-render 10x/sec for a display that no longer shows it.
      timer = setTimeout(tick, next < 10000 ? 100 : 1000);
    };
    tick();
    return () => clearTimeout(timer);
  }, [startedAtMs]);
  return <>{formatSeconds(elapsedMs)}…</>;
}

// Right-justified status glyph for the step card's run row (see
// StepRunSummary) -- one shared slot, and a node is only ever in exactly
// one state there: waiting (not yet reached), running (executing now),
// success, failed, or stale. Stale replaces success/failed rather than
// joining it -- a stale entry's own pass/fail no longer means much once
// the graph it ran against is gone, so showing one glyph, not two, is
// truer to "this cached result may not be trustworthy" than layering a
// tick or cross underneath it.
function RunStatusWaitingIcon() {
  return (
    <svg className="routine-step-card__status-icon routine-step-card__status-icon--waiting" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeOpacity="0.4" />
    </svg>
  );
}

function RunStatusRunningIcon() {
  return (
    <svg className="routine-step-card__status-icon routine-step-card__status-icon--running" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeOpacity="0.25" />
      <path d="M14 8a6 6 0 0 0-6-6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function RunStatusSuccessIcon() {
  return (
    <svg className="routine-step-card__status-icon routine-step-card__status-icon--success" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path d="M3.5 8.4 6.5 11.4 12.5 5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function RunStatusErrorIcon() {
  return (
    <svg className="routine-step-card__status-icon routine-step-card__status-icon--error" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path d="M4.5 4.5 11.5 11.5M11.5 4.5 4.5 11.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function RunStatusStaleIcon() {
  return (
    <svg className="routine-step-card__status-icon routine-step-card__status-icon--stale" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeDasharray="2 2" />
    </svg>
  );
}

function StepRunSummary({ nodeId }) {
  const { runMeta, activeNodeIds, estimatedActiveStartMs, staleNodeIds, warmingUp, isRunInProgress, loopMembership, loopIterations } = useContext(GraphEditContext);
  const entries = runMeta?.[nodeId];
  const isActive = activeNodeIds?.has(nodeId);
  if (!entries?.length) {
    // `isActive` (see deriveActiveNodeIds) is only ever true while a run is
    // actually in flight, so it already implies `isRunInProgress` -- no
    // separate check needed here.
    if (!isRunInProgress) return null;
    if (isActive && !warmingUp) {
      return (
        <div className="routine-step-card__run routine-step-card__run--active">
          <span className="routine-step-card__run-time"><RunningTimer startedAtMs={estimatedActiveStartMs} /></span>
          <span className="routine-step-card__run-status" aria-hidden="true">
            <RunStatusRunningIcon />
          </span>
        </div>
      );
    }
    // Covers both: every node still ahead of the current frontier, AND
    // the entry node itself during warm-up (no thread found yet, so no
    // reliable start time to anchor a running spinner's timer against --
    // see RunningTimer/estimatedActiveStartMs) -- it reads the same as
    // "queued" rather than the blank the entry used to show while Start's
    // own pulsing glow (see ChainCap) was the only warm-up signal.
    return (
      <div className="routine-step-card__run routine-step-card__run--waiting">
        <span className="routine-step-card__run-status" aria-hidden="true">
          <RunStatusWaitingIcon />
        </span>
      </div>
    );
  }
  // A node inside a loop body follows that loop's own selected iteration
  // (see LoopBlock's prev/next nav) instead of always showing its latest
  // execution -- null (no loop, or nothing explicitly selected yet) keeps
  // today's "last entry" default.
  const guardEdge = loopMembership?.get(nodeId);
  const guardEntries = guardEdge ? runMeta?.[guardEdge.from] : null;
  const selectedIteration = guardEdge ? (loopIterations?.[guardEdge.from] ?? null) : null;
  const last = pickEntryForIteration(entries, selectedIteration, guardEntries);
  const tokens = last.tokens?.total_tokens;
  // Cached run details predate an edit made to this step (or something
  // upstream of it) since -- still shown (still real data, just no longer
  // guaranteed to reflect the current graph), just visually muted with a
  // note instead of read as current. See lib/routineGraphDiff.js.
  const stale = !isNodeRunValid(nodeId, staleNodeIds || EMPTY_MISSING);
  // A raised node still gets an entry (see factory.py's
  // _instrument_node_callable): real duration, plus `error` instead of
  // `tokens` -- surfaced as the red cross variant of the status icon rather
  // than a separate element, so a step's run state is always in one place.
  const failed = Boolean(last.error);
  return (
    <div className={`routine-step-card__run${stale ? ' routine-step-card__run--stale' : ''}${failed ? ' routine-step-card__run--error' : ''}`}>
      <span className="routine-step-card__run-time">{formatSeconds(last.duration_ms)}</span>
      {!failed && typeof tokens === 'number' ? <span className="routine-step-card__run-tokens">{tokens} tok</span> : null}
      <span className="routine-step-card__run-status" aria-hidden="true">
        {stale ? <RunStatusStaleIcon /> : failed ? <RunStatusErrorIcon /> : <RunStatusSuccessIcon />}
      </span>
    </div>
  );
}
// Compact map card. Editing lives in NodeInspector so the chain stays readable.
function StepCard({ node }) {
  const { selectNode, selectedNodeId, edges, readOnly } = useContext(GraphEditContext);
  // A step can arrive incomplete from the JSON editor or the runtime, not just
  // from the picker, so the chain has to say which one is holding the save.
  const incomplete = !isNodeComplete(node);
  // A loop's start is a plain `noop` on the wire (see insertAt's `kind ===
  // 'loop'` comment) -- styled as its own kind here purely for legibility,
  // the same way it already gets its own picker entry and inspector badge.
  const isLoopStart = edges.some((edge) => edge.loop_guard && edge.to === node.id);
  const displayKind = isLoopStart ? 'loop' : node.kind;
  return (
    <div
      className={`canvas-node agents-graph-node agents-graph-node--${displayKind} routine-step-card${selectedNodeId === node.id ? ' selected' : ''}${incomplete ? ' routine-step-card--incomplete' : ''}${readOnly ? ' routine-step-card--readonly' : ''}`}
      data-node-id={node.id}
      style={{ '--node-color': GRAPH_KIND_COLORS[displayKind] || '#8b949e' }}
      role={readOnly ? undefined : 'button'}
      tabIndex={readOnly ? undefined : 0}
      aria-label={`${node.label || node.id} ${isLoopStart ? 'Loop' : (GRAPH_KIND_LABELS[node.kind] || node.kind)} step${incomplete ? ', incomplete' : ''}`}
      onClick={readOnly ? undefined : (event) => selectNode(node.id, event)}
      onKeyDown={readOnly ? undefined : (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          selectNode(node.id, event);
        }
      }}
    >
      <div className="node-title-bar routine-step-card__title">
        <span className="node-type-icon"><KindIcon kind={displayKind} /></span>
        <span className="routine-step-card__copy">
          <strong className="routine-step-card__label">{node.label || node.id}</strong>
        </span>
        {incomplete ? <span className="routine-step-card__incomplete" title="Required fields are empty">!</span> : null}
      </div>
      <StepRunSummary nodeId={node.id} />
    </div>
  );
}

// Fixed Start/End chrome. Never a schema node -- Start is purely "the chain
// begins here" (points at `entry`), End is purely "this path terminates
// here" (a node with no outgoing edge/branch). Rendered once per terminus,
// so a branching routine can show multiple Ends -- that's real, not a bug.
// Pulses while the run has kicked off but nothing anywhere has completed
// yet (see `warmingUp` in GraphEditContext) -- covers the client discovery
// poll plus backend routine-compile/model-connect latency before the
// entry node's own real duration exists, so the entry's card itself stays
// quiet (see StepRunSummary) instead of showing a click-time-anchored
// guess. Stops the instant the first run_meta entry lands anywhere.
function ChainCap({ kind }) {
  const { warmingUp, setRunModalOpen, setOutputModalOpen } = useContext(GraphEditContext);
  const pulsing = kind === 'start' && warmingUp;
  // Both always interactive, schema declared or not -- RunParametersModal/
  // RoutineOutputModal show the declared fields when present, or an
  // explicit "nothing declared" message otherwise. Never a silent no-op:
  // clicking either cap always tells you something.
  const openModal = kind === 'start' ? setRunModalOpen : setOutputModalOpen;
  return (
    <div
      className={`routine-cap routine-cap--${kind} routine-cap--clickable${pulsing ? ' routine-cap--warming' : ''}`}
      role="button"
      tabIndex={0}
      aria-label={kind === 'start' ? 'View or edit routine parameters' : 'View routine output'}
      onClick={() => openModal(true)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openModal(true);
        }
      }}
    >
      {kind === 'start' ? 'Start' : 'End'}
    </div>
  );
}

// A cycle (retry loop) or a rejoin (two branches converging on the same
// node) both terminate the run they're rendered in rather than expanding
// forever or duplicating a subtree. Loops are real (retry patterns); rejoins
// are real (branches meeting back up) -- both are schema-legal edge targets.
function ChainChip({ kind, label }) {
  return <div className={`routine-chip routine-chip--${kind}`}>{label}</div>;
}

// The `+` on every connector: click to open a kind picker and insert a step
// at exactly that point in the chain.
function ConnectorInsert({ slot }) {
  const { insertAt, onJoinBranches, readOnly } = useContext(GraphEditContext);
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const handlePointer = (event) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) {
        setOpen(false);
      }
    };
    const handleKey = (event) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointer);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handlePointer);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  return (
    <div className="routine-connector" ref={wrapRef}>
      <span className="routine-connector__line" aria-hidden="true" />
      {readOnly ? null : (
        <>
          <button
            type="button"
            className="routine-connector__btn"
            onClick={() => setOpen((current) => !current)}
            aria-label="Insert step here"
            aria-expanded={open}
          >
            <span className="routine-connector__btn-glyph" aria-hidden="true">+</span>
          </button>
          {open ? (
            <div className="routine-connector__menu" role="menu">
              {GRAPH_NODE_KINDS.map((item) => (
                <button
                  key={item.kind}
                  type="button"
                  role="menuitem"
                  className="routine-connector__menu-item"
                  onClick={() => {
                    if (slot.kind === 'join') onJoinBranches(slot.sourceId, item.kind);
                    else insertAt(slot, item.kind);
                    setOpen(false);
                  }}
                >
                  <span className={`canvas-dock-icon ${item.kind}`}><KindIcon kind={item.kind} /></span>
                  {item.label}
                </button>
              ))}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

// Editable branch-case chip: the column header for one fork arm. Mirrors
// Power Automate's per-case label -- double-click (or just focus) to rename.
function BranchHeader({
  value,
  isDefault,
  onRename,
  onRemove,
  collapsed,
  onToggleCollapse,
  readOnly = false,
  hideLabel = false,
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  function commit() {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== value) onRename(trimmed);
    else setDraft(value);
  }
  return (
    <div className={`routine-fork__header ${isDefault ? 'routine-fork__header--default' : ''}`}>
      <button
        type="button"
        className="routine-fork__header-collapse"
        onClick={onToggleCollapse}
        aria-expanded={!collapsed}
        aria-label={collapsed ? 'Expand branch' : 'Collapse branch'}
        title={collapsed ? 'Expand branch' : 'Collapse branch'}
      >
        {collapsed ? '▸' : '▾'}
      </button>
      {hideLabel ? null : isDefault || readOnly ? (
        <span className="routine-fork__header-label">{isDefault ? 'Default' : value}</span>
      ) : editing ? (
        <input
          autoFocus
          className="routine-fork__header-input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => { if (event.key === 'Enter') commit(); }}
        />
      ) : (
        <button type="button" className="routine-fork__header-label" onClick={() => setEditing(true)} title="Rename branch">
          {value}
        </button>
      )}
      {!readOnly ? (
        <button type="button" className="routine-fork__header-remove" onClick={onRemove} title="Remove branch" aria-label="Remove branch">
          ×
        </button>
      ) : null}
    </div>
  );
}

function renderRun(tree, initialSlot, suppressJoinChip = false) {
  const out = [];
  // nodeId -> index into `out` right before that node's own content (its
  // StepCard/ForeachBlock) was pushed, i.e. after its incoming connector.
  // A cycle back to an ancestor in *this same* linear pass can then splice
  // out its whole body and box it, mirroring how a foreach gets a scope --
  // a cycle reached through a fork branch or foreach body (a separate
  // renderRun call, separate `positions` map) just falls back to the plain
  // "loops back to" chip below.
  const positions = new Map();
  let current = tree;
  let slot = initialSlot;
  let guard = 0;
  while (guard++ < 2000) {
    out.push(<ConnectorInsert key={`c-${slot.sourceId ?? 'root'}-${slot.kind}-${slot.value ?? ''}`} slot={slot} />);
    if (current === null) {
      out.push(<ChainCap key="end" kind="end" />);
      break;
    }
    if (current.type === 'loop') {
      const bodyStart = positions.get(current.id);
      if (bodyStart !== undefined) {
        const body = out.splice(bodyStart);
        // The connector just pushed above (top of this same iteration) is
        // body's last element -- it's the one whose edge is the back-edge
        // itself. Render it after the box, not swept inside: same "+ after
        // the block" affordance every other construct (fork, foreach)
        // offers, and functionally unchanged -- inserting there still
        // splices a new step onto the tail before it loops back, which the
        // next render sweeps into a now-larger box.
        const trailing = body.pop();
        out.push(<LoopBlock key={`loop-${current.id}`} targetLabel={current.label} body={body} />);
        if (trailing) out.push(trailing);
      } else {
        out.push(<ChainChip key={`loop-${current.id}`} kind="loop" label={`↺ loops back to “${current.label}”`} />);
      }
      break;
    }
    if (current.type === 'rejoin') {
      out.push(<ChainChip key={`rejoin-${current.id}`} kind="rejoin" label={`↳ continues at “${current.label}”`} />);
      break;
    }
    if (current.type === 'join') {
      if (!suppressJoinChip) {
        out.push(<ChainChip key={`join-${current.id}`} kind="rejoin" label={`↳ joins at “${current.label}”`} />);
      }
      break;
    }
    positions.set(current.id, out.length);
    if (current.node.kind === 'foreach' && current.node.config?.mode === 'send') {
      const [bodyTree, afterTree] = splitForeachBody(current.next);
      out.push(
        <ForeachBlock
          key={`foreach-${current.id}`}
          node={current.node}
          body={renderRun(bodyTree, { sourceId: current.id, kind: 'foreach' })}
        />,
      );
      slot = { sourceId: current.id, kind: 'plain' };
      current = afterTree;
      continue;
    }

    if (current.fork) {
      out.push(<ForkBlock key={`fork-${current.id}`} fork={current.fork} node={current.node} />);
      break;
    }
    out.push(<StepCard key={current.id} node={current.node} />);
    if (current.loop) {
      // Reaching the tail: the body is everything pushed since the loop's
      // own start's card and connector -- the start itself stays outside
      // the box, rendered as a plain step above it (matching ForkBlock's
      // own router card, e.g. "Classify Item"), and is where its
      // properties are actually edited (NodeInspector, via `guardEdge` --
      // see there for why). `positions.get(bodyStartId) + 1` skips past
      // the start's own card, keeping only the connector onward. `exitTree`
      // continues the OUTER chain past the box -- the guard's own
      // on_false -- exactly like a foreach's stop marker hands off to
      // whatever comes after the fan-out.
      const bodyStart = positions.get(current.loop.bodyStartId);
      const body = bodyStart !== undefined ? out.splice(bodyStart + 1) : [];
      out.push(<LoopBlock key={`loop-${current.id}`} edge={current.loop.edge} body={body} />);
      slot = { sourceId: current.id, kind: 'loop-exit' };
      current = current.loop.exitTree;
      continue;
    }
    slot = { sourceId: current.id, kind: 'plain' };
    current = current.next;
  }
  return out;
}

// Retry loop: boxes the cycle's body -- everything after the node it loops
// back to, through the back-edge -- like ForeachBlock does for a fan-out.
// One box, not a box nested inside a box, and the loop-start's own card
// (e.g. "Loop") renders OUTSIDE/above it, exactly like a router's own card
// sits above ForkBlock's lanes -- this section is purely the body. `edge`
// is the guarded case (a real Loop construct's tail, `loop_guard` present);
// its properties are edited by clicking the loop-start card itself
// (NodeInspector), not here. `targetLabel` alone (no `edge`) is the
// fallback for a hand-authored, unguarded cycle the JSON editor can still
// produce. Either way there's no separate "back to X" line -- the box's
// own closing edge already says that.
function LoopBlock({ edge, targetLabel, body }) {
  const { settings } = useContext(GraphEditContext);
  const recursionLimit = settings?.recursion_limit;
  const maxRetries = settings?.retry?.max_retries;
  const guard = edge?.loop_guard;
  return (
    <div className="routine-loop-frame">
      {(recursionLimit || maxRetries) ? (
        <div className="routine-loop__toolbar">
          {recursionLimit ? <span className="routine-loop__badge">Recursion limit {recursionLimit}</span> : null}
          {maxRetries ? <span className="routine-loop__badge">retries {maxRetries}x per step</span> : null}
        </div>
      ) : null}
      <div className="routine-loop__outer">
        <span
          className={`routine-loop__label${guard ? ' routine-loop__label--guarded' : ''}${guard && !guard.expression ? ' routine-loop__label--unset' : ''}`}
          title={guard?.expression || undefined}
        >
          {guard ? (guard.expression ? `↺ Loop while ${guard.expression}` : '↺ Loop · set condition') : '↺ Retry loop'}
        </span>
        <section className="routine-loop" aria-label={guard ? 'Loop' : `Retry loop back to ${targetLabel}`}>
          <div className="routine-loop__body">{body}</div>
        </section>
      </div>
    </div>
  );
}

function ForeachBlock({ node, body }) {
  const itemKey = node.config?.item_key || 'item';
  return (
    <section className="routine-foreach" aria-label={`For each ${itemKey}`}>
      <div className="routine-foreach__head">
        <StepCard node={node} />
      </div>
      <div className="routine-foreach__body">
        {body}
      </div>
    </section>
  );
}

// A router node and its branch lanes form one CSS-only switch box. Connector
// rails are pseudo-elements, never DOM measurements: centring the box makes
// the router stem meet the lane rail even when lanes have different widths.
function ForkBlock({ fork, node }) {
  const {
    onAddBranch,
    onAddDefault,
    onRemoveBranch,
    onRemoveDefault,
    onRenameBranch,
    edges,
    readOnly,
  } = useContext(GraphEditContext);
  const [collapsed, setCollapsed] = useState(() => new Set());
  const { sourceId, edge, branches, defaultTree, joinTree, editable = true, fixedBranches = false, unconditional = false } = fork;
  // A fixed fork's arms are dictated by the node kind, so it grows no lanes
  // and its headers are labels rather than editable chips. It can still be
  // joined -- that's about what happens *after* the fork, not its shape.
  const canGrowLanes = editable && !fixedBranches && !readOnly;
  const lanes = [
    ...branches.map(({ value, tree }) => ({
      value,
      tree,
      isDefault: false,
      slot: { sourceId, kind: 'branch', value },
    })),
    ...(edge.default ? [{
      value: 'default',
      tree: defaultTree,
      isDefault: true,
      slot: { sourceId, kind: 'default' },
    }] : []),
  ];
  const joining = joinTree ? lanes.filter((lane) => chainTerminus(lane.tree) === 'join') : [];
  const joiningValues = new Set(joining.map(({ value }) => value));
  // Asks the wiring itself whether a join is possible rather than reading it
  // off the rendered tree, so the `+` appears exactly when the action behind
  // it would do something -- including on a fork whose lane branches again.
  const canOfferJoin = useMemo(() => joinTerminals(edges, sourceId).length > 0, [edges, sourceId]);
  const toggleCollapse = useCallback((value) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }, []);

  return (
    <section className={`routine-fork ${joining.length ? 'routine-fork--joined' : ''}`}>
      <div className="routine-fork__head">
        <StepCard node={node} />
      </div>
      <div className="routine-fork__branches">
        <div className="routine-fork__lanes">
          {lanes.map((lane, index) => {
            const isCollapsed = collapsed.has(lane.value);
            const { isFirst, isLast } = forkLaneEdgePosition(index, lanes.length, canGrowLanes);
            const classes = [
              'routine-fork__lane',
              isCollapsed && 'routine-fork__lane--collapsed',
              lane.isDefault && 'routine-fork__lane--default',
              isFirst && 'routine-fork__lane--fanout-first',
              isLast && 'routine-fork__lane--fanout-last',
              joiningValues.has(lane.value) && 'routine-fork__lane--fanin',
              joiningValues.has(lane.value) && lane.value === joining[0]?.value && 'routine-fork__lane--fanin-first',
              joiningValues.has(lane.value) && lane.value === joining.at(-1)?.value && 'routine-fork__lane--fanin-last',
            ].filter(Boolean).join(' ');
            return (
              <div key={lane.value} className={classes}>
                <span className="routine-fork__wire" aria-hidden="true" />
                <BranchHeader
                  value={lane.value}
                  isDefault={lane.isDefault}
                  onRename={(next) => onRenameBranch(sourceId, lane.value, next)}
                  onRemove={() => (lane.isDefault ? onRemoveDefault(sourceId) : onRemoveBranch(sourceId, lane.value))}
                  collapsed={isCollapsed}
                  readOnly={readOnly || !editable || fixedBranches}
                  hideLabel={unconditional}
                  onToggleCollapse={() => toggleCollapse(lane.value)}
                />
                {isCollapsed ? null : renderRun(lane.tree, lane.slot, Boolean(joinTree))}
              </div>
            );
          })}
          {canGrowLanes && !edge.default ? (
            <div className="routine-fork__lane routine-fork__lane--add routine-fork__lane--collapsed">
              <span className="routine-fork__wire" aria-hidden="true" />
              <div className="routine-fork__add-slot">
                <button type="button" className="routine-fork__add-slot-action" onClick={() => onAddDefault(sourceId)}>
                  + Add default
                </button>
              </div>
            </div>
          ) : null}
          {canGrowLanes ? (
            <div className="routine-fork__lane routine-fork__lane--add routine-fork__lane--collapsed routine-fork__lane--fanout-last">
              <span className="routine-fork__wire" aria-hidden="true" />
              <div className="routine-fork__add-slot">
                <button type="button" className="routine-fork__add-slot-action" onClick={() => onAddBranch(sourceId)}>
                  + Add branch
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>
      {joinTree ? (
        <div className="routine-fork__join">
          {renderRun(joinTree, { sourceId, kind: 'join' })}
        </div>
      ) : null}
      <div className="routine-fork__actions">
        {editable && !joinTree && canOfferJoin ? (
          <ConnectorInsert slot={{ sourceId, kind: 'join' }} />
        ) : null}
      </div>
    </section>
  );
}

// Nodes not reachable from `entry` (e.g. left behind after deleting whatever
// pointed at them) never silently vanish -- they surface here so the author
// can rewire or delete them explicitly instead of losing data invisibly.
function UnreachableTray({ nodes }) {
  const { deleteNode, readOnly } = useContext(GraphEditContext);
  if (!nodes.length) return null;
  return (
    <div className="routine-unreachable">
      <div className="routine-unreachable__head">Unreachable steps ({nodes.length})</div>
      <div className="routine-unreachable__list">
        {nodes.map((node) => (
          <div key={node.id} className="routine-unreachable__item">
            <span className={`canvas-dock-icon ${node.kind}`}><KindIcon kind={node.kind} /></span>
            <span>{node.label || node.id}</span>
            {readOnly ? null : (
              <button type="button" onClick={() => deleteNode(node.id)} title="Delete" aria-label="Delete">×</button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function RoutineChain({ nodes, edges, entry }) {
  const nodesById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const edgeBySource = useMemo(() => edgeBySourceMap(edges), [edges]);
  // Build the whole reachable tree (forks included) fully, up front, in one
  // pure pass -- see buildChainTree's comment for why this can't be deferred
  // into lazily-invoked React children. Memoized: without this, every
  // re-render of the page (e.g. an unrelated realtime/SSE update bubbling
  // through a parent selector) walked and re-built the whole tree from
  // scratch, even though nodes/edges/entry hadn't changed.
  const { tree, unreachable } = useMemo(() => {
    const rendered = new Set();
    const builtTree = buildChainTree(entry, nodesById, edgeBySource, new Set(), rendered);
    return { tree: builtTree, unreachable: nodes.filter((node) => !rendered.has(node.id)) };
  }, [entry, nodesById, edgeBySource, nodes]);
  const body = useMemo(() => renderRun(tree, { sourceId: null, kind: 'entry' }), [tree]);
  return (
    <div className="routine-chain">
      <ChainCap kind="start" />
      {body}
      <UnreachableTray nodes={unreachable} />
    </div>
  );
}
