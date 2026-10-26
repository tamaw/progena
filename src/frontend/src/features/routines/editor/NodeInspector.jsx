import React, { useContext, useState, useRef, useEffect } from 'react';
import { GraphEditContext, EMPTY_MISSING, MissingFieldsContext } from './graphEditorContext';
import { pickEntryForIteration, nodeRequiresRunConfirmation, formatSeconds } from './graphRunState';
import { isNodeRunValid } from '../models/routineGraphDiff';
import { missingRequirements } from './graphValidation';
import { GRAPH_KIND_COLORS, GRAPH_KIND_LABELS } from './graphNodeKinds';
import { KindIcon, ChevronLeftSmall, ChevronRightPointSmall } from './RoutineGraphIcons';
import { ExpressionField, NodeConfigBody } from './NodeConfigFields';
import PopupModal from '../../../components/PopupModal';
import { entityDraftKey } from '../../../store/editorDraftsSlice';
import useEntityDraft from '../../../hooks/useEntityDraft';
import { HumanApprovalControls } from './HumanApprovalControls';

// Routing controls belong to the inspected node; fixed condition/split lanes
// have no state key to author. New branching is chosen at insertion time.
function RoutingFooter({ id, kind, routerEdge }) {
  const { onChangeStateKey, onRemoveBranching } = useContext(GraphEditContext);
  if (kind === 'condition' || kind === 'split' || !routerEdge) return null;
  return (
    <div className="agents-graph-node__routing">
      <input
        className="agents-graph-node__toolinput"
        placeholder="route on <key>"
        onChange={(event) => onChangeStateKey(id, event.target.value)}
      />
      <button type="button" className="agents-graph-node__unsplit" onClick={() => onRemoveBranching(id)} title="Remove branching">
        Remove branching
      </button>
    </div>
  );
}

// Full configuration for the selected node. It floats inside the canvas and
// is deliberately separate from the chain so large prompts never resize it.
export function NodeInspector({ node, routerEdge, guardEdge, onClose, onDismiss, isNew, style, panelRef }) {
  const editContext = useContext(GraphEditContext);
  const { updateNode, deleteNode, updateLoopGuard, runMeta, runData, staleNodeIds, edges: graphEdges, nodes: graphNodes, readOnly, runStatus, isRunInProgress, startRun, hasInteractiveSession, resetInteractiveSession, loopMembership, loopIterations, setLoopIteration, pendingApprovals, answerApproval, draftScope } = editContext;
  const [isEditingLabel, setIsEditingLabel] = useState(false);
  const [confirmRunOpen, setConfirmRunOpen] = useState(false);
  const labelKey = entityDraftKey('routine-label', JSON.stringify([draftScope, node?.id]));
  const [labelDraft, setLabelDraft, labelControls] = useEntityDraft(labelKey, node?.label || '');
  const inspectedNodeIdRef = useRef(node?.id);
  const labelInputRef = useRef(null);

  // A different node was selected: close only the transient editing UI. The
  // label source itself is keyed by routine and node in the draft registry, so
  // it remains available when this inspector returns without leaking to the
  // newly selected step.
  if (inspectedNodeIdRef.current !== node?.id) {
    inspectedNodeIdRef.current = node?.id;
    if (isEditingLabel) setIsEditingLabel(false);
    if (confirmRunOpen) setConfirmRunOpen(false);
  }

  useEffect(() => {
    if (!isEditingLabel) return undefined;
    labelInputRef.current?.focus();
    labelInputRef.current?.select();
    return undefined;
  }, [isEditingLabel]);

  if (!node) return null;
  const runEntries = runMeta?.[node.id];
  // Follows the enclosing loop's own selected iteration, same as every
  // other body node's badge (see StepRunSummary/pickEntryForIteration) --
  // this panel is also where that iteration gets *changed*: the canvas
  // carries no loop chrome beyond the plain condition label, since every
  // attempt to place a prev/next control there read as bolted-on. Paging
  // through iterations happens here instead, the same place their
  // duration/tokens/route/output already render.
  const inspectorGuardEdge = loopMembership?.get(node.id);
  const inspectorGuardEntries = inspectorGuardEdge ? runMeta?.[inspectorGuardEdge.from] : null;
  const inspectorIterationCount = inspectorGuardEntries?.length ?? 0;
  const inspectorSelectedIteration = inspectorGuardEdge ? (loopIterations?.[inspectorGuardEdge.from] ?? null) : null;
  const inspectorEffectiveIteration = inspectorSelectedIteration ?? inspectorIterationCount;
  const lastRunEntry = pickEntryForIteration(runEntries, inspectorSelectedIteration, inspectorGuardEntries);
  const isRunStale = !isNodeRunValid(node.id, staleNodeIds || EMPTY_MISSING);
  const outputKey = node.output_key || node.id;
  const approval = pendingApprovals?.find((item) => item.node_id === node.id);
  // Prefers this execution's own recorded write (see factory.py's
  // `entry.output`) over the cumulative `data` channel, which only ever
  // holds the latest write per key -- falls back to `runData` for older
  // runs recorded before that field existed, or a node with no data write
  // at all this execution.
  const runOutputValue = lastRunEntry?.output && outputKey in lastRunEntry.output
    ? lastRunEntry.output[outputKey]
    : runData?.[outputKey];
  const runOutputText = runOutputValue === undefined
    ? undefined
    : (typeof runOutputValue === 'string' ? runOutputValue : JSON.stringify(runOutputValue, null, 2));
  const handleConfig = (nodeId, patch) => updateNode(nodeId, { config: { ...node.config, ...patch } });
  // A loop's own guard expression lives on its tail's edge (`loop_guard`),
  // not on this node's own config -- there's no separate node kind for it
  // (see insertAt's `kind === 'loop'` comment), so `config`/`updateNodeConfig`
  // here are a shim over `updateLoopGuard` instead of `node.config`. Reuses
  // ExpressionField exactly as Eval/Condition do; the guard just isn't
  // state this node's own config actually holds.
  const guardConfig = guardEdge?.loop_guard;
  const handleGuardConfig = (_nodeId, patch) => updateLoopGuard(guardEdge.from, patch);
  const unmet = missingRequirements(node);
  const guardIncomplete = guardEdge && !guardConfig.expression?.trim();
  // One requirement, not one field: a one-of group ("a template or a file")
  // is a single unmet requirement offering two ways to satisfy it -- counting
  // every alternative name via a flattened list told the author "2 fields
  // are required" when only one alternative (the other is a legacy field
  // never shown in this UI) was even visible to fill in.
  const missingCount = unmet.length + (guardIncomplete ? 1 : 0);
  const missingSet = missingCount ? new Set([...unmet.flat(), ...(guardIncomplete ? ['expression'] : [])]) : EMPTY_MISSING;
  // Held open until the step can actually be saved. Closing here would strand
  // an unsaveable step somewhere in the chain with the whole graph withheld
  // behind it, so the only ways out are filling the fields or deleting it.
  const locked = unmet.length > 0 || guardIncomplete;
  const startLabelEdit = () => {
    setIsEditingLabel(true);
  };
  const cancelLabelEdit = () => setIsEditingLabel(false);
  const saveLabelEdit = () => {
    updateNode(node.id, { label: labelDraft.replace(/\s+/g, ' ').trim() });
    labelControls.clear();
    setIsEditingLabel(false);
  };
  return (
    <aside
      ref={panelRef}
      className={`routine-node-inspector${locked ? ' routine-node-inspector--locked' : ''}`}
      style={{ ...style, '--node-color': GRAPH_KIND_COLORS[guardEdge ? 'loop' : node.kind] || '#8b949e' }}
      aria-label={`${node.label || node.id} details`}
    >
      <header className="routine-node-inspector__head">
        <span className="node-type-icon"><KindIcon kind={guardEdge ? 'loop' : node.kind} /></span>
        <div>
          <span className="routine-node-inspector__title">
            {isEditingLabel ? (
              <input
                ref={labelInputRef}
                type="text"
                className="routine-node-inspector__label-input"
                value={labelDraft}
                placeholder={node.id}
                aria-label="Step label"
                onChange={(event) => setLabelDraft(event.target.value)}
                onBlur={cancelLabelEdit}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    saveLabelEdit();
                  } else if (event.key === 'Escape') {
                    event.preventDefault();
                    labelControls.clear();
                    cancelLabelEdit();
                  }
                }}
              />
            ) : (
              <strong className="routine-node-inspector__title-text">{node.label || node.id}</strong>
            )}
            <button
              type="button"
              className="routine-node-inspector__rename"
              data-tooltip={isEditingLabel ? 'Save step label' : 'Rename step'}
              aria-label={isEditingLabel ? 'Save step label' : 'Rename step'}
              onMouseDown={isEditingLabel ? (event) => event.preventDefault() : undefined}
              onClick={isEditingLabel ? saveLabelEdit : startLabelEdit}
            >
              {isEditingLabel ? (
                <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
                  <path d="M3.25 8.25 6.5 11.5l6.25-6.75" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              ) : (
                <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
                  <path d="M2 14h3l8-8-3-3-8 8z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
                </svg>
              )}
            </button>
          </span>
          <span className="routine-node-inspector__kind">{guardEdge ? 'Loop' : (GRAPH_KIND_LABELS[node.kind] || node.kind)}</span>
        </div>
        <button
          type="button"
          className="routine-node-inspector__close"
          onClick={onClose}
          title={locked ? (isNew ? 'Close and discard this step' : 'Close and discard changes') : undefined}
          aria-label="Close node details"
        >
          ×
        </button>
      </header>
      <MissingFieldsContext.Provider value={missingSet}>
      <div className="routine-node-inspector__body">
        {approval ? <HumanApprovalControls key={approval.interrupt_id} approval={approval} onAnswer={answerApproval} disabled={isRunInProgress || runStatus === 'stopped' || runStatus === 'cancelled'} /> : null}
        {missingCount ? (
          <p className="routine-node-inspector__required-hint" role="status">
            {missingCount === 1
              ? 'One field is required before this step can be saved.'
              : `${missingCount} fields are required before this step can be saved.`}
            {' '}
            {isNew ? 'Fill it in, or close to discard this step.' : 'Fill it in, or close to discard your changes.'}
          </p>
        ) : null}
        {guardEdge ? (
          <ExpressionField id={guardEdge.from} config={guardConfig} field="expression" label="Continue while" placeholder="e.g. attempt < 3" updateNodeConfig={handleGuardConfig} autocomplete />
        ) : null}
        <NodeConfigBody
          id={node.id}
          kind={node.kind}
          config={node.config}
          updateNodeConfig={handleConfig}
          replaceNodeConfig={(config) => updateNode(node.id, { config })}
        />
        <RoutingFooter id={node.id} kind={node.kind} routerEdge={routerEdge} />
        {runEntries?.length ? (
          <div className={`routine-node-inspector__run${isRunStale ? ' routine-node-inspector__run--stale' : ''}${lastRunEntry.error ? ' routine-node-inspector__run--error' : ''}`}>
            {isRunStale ? (
              <p className="routine-node-inspector__run-stale-notice" role="status">
                Graph changed since this ran -- details below may no longer be accurate.
              </p>
            ) : null}
            <div className="routine-node-inspector__run-head">
              {inspectorGuardEdge && inspectorIterationCount > 0 ? (
                <span className="routine-node-inspector__iteration-nav">
                  <button
                    type="button"
                    className="routine-node-inspector__iteration-btn"
                    aria-label="Previous iteration"
                    data-tooltip="Previous iteration"
                    disabled={inspectorEffectiveIteration <= 1}
                    onClick={() => setLoopIteration(inspectorGuardEdge.from, Math.max(1, inspectorEffectiveIteration - 1))}
                  >
                    <ChevronLeftSmall />
                  </button>
                  <span>{inspectorSelectedIteration != null ? `Iteration ${inspectorSelectedIteration} run` : 'Last run'}</span>
                  <button
                    type="button"
                    className="routine-node-inspector__iteration-btn"
                    aria-label="Next iteration"
                    data-tooltip="Next iteration"
                    disabled={inspectorEffectiveIteration >= inspectorIterationCount}
                    onClick={() => setLoopIteration(inspectorGuardEdge.from, Math.min(inspectorIterationCount, inspectorEffectiveIteration + 1))}
                  >
                    <ChevronRightPointSmall />
                  </button>
                </span>
              ) : (
                <span>{inspectorSelectedIteration != null ? `Iteration ${inspectorSelectedIteration} run` : 'Last run'}</span>
              )}
              {runEntries.length > 1 ? (
                <span className="routine-node-inspector__run-count">{runEntries.length} executions this run</span>
              ) : null}
            </div>
            {lastRunEntry.error ? (
              <p className="routine-node-inspector__run-error" role="alert">{lastRunEntry.error}</p>
            ) : null}
            <dl className="routine-node-inspector__run-stats">
              <div>
                <dt>Duration</dt>
                <dd>{formatSeconds(lastRunEntry.duration_ms)}</dd>
              </div>
              {typeof lastRunEntry.step === 'number' ? (
                <div>
                  <dt>Step</dt>
                  <dd>{lastRunEntry.step}</dd>
                </div>
              ) : null}
              {lastRunEntry.routine ? (
                <div className="routine-node-inspector__run-revision">
                  <dt>Child revision</dt>
                  <dd title={lastRunEntry.routine.revision_id || undefined}>
                    {lastRunEntry.routine.revision_id || 'Unavailable'}
                  </dd>
                </div>
              ) : null}
              {lastRunEntry.tokens ? (
                <div>
                  <dt>Tokens</dt>
                  <dd>{lastRunEntry.tokens.total_tokens} ({lastRunEntry.tokens.input_tokens} in / {lastRunEntry.tokens.output_tokens} out)</dd>
                </div>
              ) : null}
              {lastRunEntry.route != null ? (
                <div>
                  <dt>Routed to</dt>
                  <dd>
                    {(Array.isArray(lastRunEntry.route) ? lastRunEntry.route : [lastRunEntry.route])
                      .map((targetId) => (graphNodes || []).find((n) => n.id === targetId)?.label || targetId)
                      .join(', ')}
                  </dd>
                </div>
              ) : null}
            </dl>
            {runOutputText !== undefined ? (
              <div className="routine-node-inspector__run-output">
                <span className="routine-node-inspector__run-output-label">Output written to &quot;{outputKey}&quot;</span>
                <pre>{runOutputText}</pre>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
      </MissingFieldsContext.Provider>
      <footer className="routine-node-inspector__foot">
        {readOnly ? null : (
          <div className="routine-node-inspector__run-actions">
            <button
              type="button"
              className="routine-node-inspector__run-to-here"
              title="Execute the routine from wherever this editor session last stopped, through this step"
              disabled={isRunInProgress || runStatus === 'stopped'}
              onClick={() => {
                if (nodeRequiresRunConfirmation(node.id, graphNodes, graphEdges, runMeta)) {
                  setConfirmRunOpen(true);
                } else {
                  startRun(node.id);
                }
              }}
            >
              Run to here
            </button>
            {hasInteractiveSession ? (
              <button
                type="button"
                className="routine-node-inspector__reset-run"
                title={'Abandon this editor session\u2019s progress -- the next "Run to here" click starts over from the beginning instead of resuming'}
                disabled={isRunInProgress || runStatus === 'stopped'}
                onClick={resetInteractiveSession}
              >
                Reset run
              </button>
            ) : null}
          </div>
        )}
        <button type="button" className="agents-graph-node__delete" onClick={() => { deleteNode(node.id); onDismiss(); }}>
          Delete step
        </button>
      </footer>
      {confirmRunOpen ? (
        <PopupModal
          eyebrow="Run to here"
          title="This will call a live agent"
          message="Reaching this step runs at least one step with a real side effect (a live agent/routine delegation, an A2A call, or a human approval gate) that hasn't run yet this session. Continue?"
          confirmLabel="Run"
          onClose={() => setConfirmRunOpen(false)}
          onConfirm={() => { setConfirmRunOpen(false); startRun(node.id); }}
        />
      ) : null}
    </aside>
  );
}
