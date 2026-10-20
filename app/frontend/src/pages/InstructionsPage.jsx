import { useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import MarkdownContent from '../components/common/MarkdownContent';
import { renderSafeMarkdown } from '../components/chat/messageRenderUtils';
import { selectRealtime } from '../store/realtimeSlice';
import {
  fetchInstruction,
  fetchInstructionRevision,
  fetchInstructionRevisions,
  fetchInstructions,
  restoreInstructionRevision,
  saveInstruction,
  selectInstruction,
  selectInstructionContentRequest,
  selectInstructionRevision,
  selectInstructionRevisionRequest,
  selectInstructionRevisions,
  selectInstructionRevisionsRequest,
  selectInstructions,
  selectInstructionsState,
} from '../store/uiDataSlice';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import DetailHeader, {
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  EditActionIcon,
  ReconnectActionIcon,
  RejectActionIcon,
  SaveActionIcon,
} from '../components/layout/DetailHeader';
import {
  PaneCollapseSeam,
  PaneEmptyState,
  PaneExpandRail,
  PaneListFilters,
  PaneListFooter,
  PaneListRows,
  PaneListSearch,
  PaneListToolbar,
  PaneSection,
} from '../components/layout/PaneList';
import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import InlineFailure from '../components/common/InlineFailure';
import useEntityDraft from '../hooks/useEntityDraft';
import useUiAction from '../hooks/useUiAction';
import { entityDraftKey } from '../store/editorDraftsSlice';
import { formatCompactRelativeAge } from '../lib/displayTime';
import { formatRevisionTime } from './knowledgePagesModel';
import {
  INSTRUCTION_FILTERS, groupInstructions, groupLabel, instructionHref, placeholderNote, placeholderRoots,
} from './instructionsPageModel';

function InstructionCard({ instruction, selected, onSelect }) {
  return (
    <article className={`task-card ${selected ? 'is-selected' : ''}`}>
      <button type="button" className="task-card__button" onClick={() => onSelect(instruction.path)}>
        <div className="task-card__title">{instruction.title}</div>
        <div className="task-card__meta">
          <span className="instructions-card__desc">{instruction.description}</span>
        </div>
        <div className="task-card__meta instructions-card__info">
          <span className="instructions-card__path">{instruction.path.split('/').at(-1)}</span>
        </div>
      </button>
    </article>
  );
}

function InstructionBody({ body }) {
  return <MarkdownContent content={body} renderMarkdown={renderSafeMarkdown} renderMermaid />;
}

export default function InstructionsPage() {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { getApiUrl } = useConfig();
  const instructionsUrl = getApiUrl('instructions');
  const realtime = useSelector(selectRealtime);
  const instructions = useSelector(selectInstructions);
  const { loaded, error: listError } = useSelector(selectInstructionsState);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('tasks-list');
  const [filter, setFilter] = useState('All');
  const [query, setQuery] = useState('');
  const routePath = useParams()['*'] || '';
  const [params, setParams] = useSearchParams();
  const viewingRevisionId = params.get('revision');

  const groups = useMemo(() => groupInstructions(instructions, filter, query), [instructions, filter, query]);
  const selected = useMemo(
    () => instructions.find((item) => item.path === routePath) || null,
    [instructions, routePath],
  );
  const selectedPath = selected?.path || null;
  const instruction = useSelector((state) => selectInstruction(state, selectedPath));
  const contentRequest = useSelector((state) => selectInstructionContentRequest(state, selectedPath));
  const revisions = useSelector((state) => selectInstructionRevisions(state, selectedPath));
  const revisionsRequest = useSelector((state) => selectInstructionRevisionsRequest(state, selectedPath));
  const revision = useSelector((state) => selectInstructionRevision(state, selectedPath, viewingRevisionId));
  const revisionRequest = useSelector((state) => selectInstructionRevisionRequest(state, selectedPath, viewingRevisionId));
  const [draft, setDraft, draftControls] = useEntityDraft(
    entityDraftKey('instruction', selectedPath, 'content'),
    { editing: false, content: instruction?.content || '' },
  );
  const {
    beginSave: beginDraftSave,
    clear: clearDraft,
    error: draftSaveError,
    failSave: failDraftSave,
    saving: draftSaving,
    trackSave: trackDraftSave,
  } = draftControls;
  const restoreAction = useUiAction(`${selectedPath || 'instructions'}:${viewingRevisionId || 'current'}`);
  const editing = Boolean(draft.editing);
  const dirty = editing && draft.content !== instruction?.content;

  useEffect(() => {
    if (realtime.connected) {
      dispatch(fetchInstructions({ instructionsUrl })).unwrap().catch(() => {});
    }
  }, [dispatch, instructionsUrl, realtime.connected]);

  // Open the first instruction when the URL names none, or one that no longer exists.
  useEffect(() => {
    if (!loaded || selected) return;
    const first = groups[0]?.items[0];
    if (first) navigate(instructionHref(first.path), { replace: true });
  }, [groups, loaded, navigate, selected]);

  // Reload the open instruction and its revisions whenever it changes (edits
  // here, restores, or a runtime sync), which the list snapshot reports.
  const updatedAt = selected?.updated_at;
  useEffect(() => {
    if (!selectedPath || !realtime.connected) return;
    dispatch(fetchInstruction({ instructionsUrl, path: selectedPath }));
    dispatch(fetchInstructionRevisions({ instructionsUrl, path: selectedPath }));
  }, [dispatch, instructionsUrl, realtime.connected, selectedPath, updatedAt]);

  useEffect(() => {
    if (selectedPath && viewingRevisionId) {
      dispatch(fetchInstructionRevision({ instructionsUrl, path: selectedPath, revisionId: viewingRevisionId }));
    }
  }, [dispatch, instructionsUrl, selectedPath, viewingRevisionId]);

  const selectInstructionPath = (path) => {
    if (path === selectedPath) return;
    navigate(instructionHref(path));
  };

  const toggleRevision = (event, revisionId) => {
    event.preventDefault();
    setParams(viewingRevisionId === revisionId ? {} : { revision: revisionId });
  };

  const startEdit = () => {
    setParams({});
    setDraft({ editing: true, content: instruction?.content || '' });
  };

  const cancelEdit = () => {
    if (draftSaving) return;
    clearDraft();
  };

  const save = () => {
    if (!dirty || draftSaving || !selectedPath) return;
    const content = draft.content;
    const ticket = beginDraftSave();
    if (!ticket) return;
    const operation = dispatch(saveInstruction({ instructionsUrl, path: selectedPath, content }));
    trackDraftSave(ticket, {
      requestId: operation.requestId,
      events: [],
    });
    void operation.unwrap().then((acknowledgement) => {
      trackDraftSave(ticket, {
        requestId: acknowledgement.request_id,
        events: acknowledgement.event_types,
      });
    }).catch((error) => {
      failDraftSave(ticket, error);
    });
  };

  const restore = () => {
    if (!viewingRevisionId) return;
    if (!window.confirm('Restoring saves this revision as the live instruction.')) return;
    restoreAction.clearError();
    const revisionId = viewingRevisionId;
    void restoreAction.run('Restore instruction revision', async ({ isCurrent }) => {
      await dispatch(restoreInstructionRevision({ instructionsUrl, path: selectedPath, revisionId })).unwrap();
      if (isCurrent()) setParams({});
    });
  };

  const viewingCurrent = revisions.find((rev) => rev.revision_id === viewingRevisionId)?.current;
  const shown = viewingRevisionId ? revision : instruction;
  const placeholders = viewingRevisionId ? placeholderRoots(revision?.body || '') : selected?.placeholders || [];
  const inputTypes = shown?.front_matter?.inputs || {};
  const editableCount = instructions.filter((item) => item.editable).length;

  const actions = !selected?.editable ? null : editing ? (
    <DetailHeaderActions menuLabel="Instruction actions">
      <DetailHeaderAction icon={<RejectActionIcon />} label="Cancel" tooltip="Discard changes" onClick={cancelEdit} disabled={draftSaving} />
      <DetailHeaderAction
        icon={<SaveActionIcon />}
        label="Save"
        tooltip={dirty ? 'Save instruction' : 'No changes to save'}
        variant="success"
        onClick={save}
        disabled={!dirty || draftSaving || !realtime.connected}
      />
    </DetailHeaderActions>
  ) : viewingRevisionId && !viewingCurrent ? (
    <DetailHeaderActions menuLabel="Instruction actions">
      <DetailHeaderAction
        icon={<ReconnectActionIcon />}
        label="Restore"
        tooltip="Restore this revision"
        onClick={restore}
        disabled={restoreAction.busy || !revision || !realtime.connected}
      />
    </DetailHeaderActions>
  ) : (
    <DetailHeaderActions menuLabel="Instruction actions">
      <DetailHeaderAction icon={<EditActionIcon />} label="Edit" tooltip="Edit instruction" onClick={startEdit} disabled={!instruction} />
    </DetailHeaderActions>
  );

  return (
    <PageShell className="tasks-page instructions-page">
      <PaneSection className={paneClassName} aria-label="Instructions" onItemSelect={() => setCollapsed(true)}>
        {collapsed ? (
          <PaneExpandRail label="Instructions" onExpand={() => setCollapsed(false)} />
        ) : (
          <>
            <PaneCollapseSeam label="Instructions" onCollapse={() => setCollapsed(true)} />
            <button type="button" className="pane-mobile-scrim" aria-label="Close instructions list" onClick={() => setCollapsed(true)} />
            <PaneListFilters
              filters={INSTRUCTION_FILTERS}
              activeFilter={filter}
              onFilterChange={setFilter}
              actionLabel="+ New"
              onAction={() => window.dispatchEvent(
                new CustomEvent('progenitor:open-quick-chat', { detail: { mode: 'instruction-create' } }),
              )}
            />
            <PaneListToolbar>
              <PaneListSearch value={query} placeholder="Search instructions" onChange={setQuery} />
            </PaneListToolbar>
            {listError ? (
              <InlineFailure
                failure={listError}
                operation="Load instructions"
                onRetry={() => dispatch(fetchInstructions({ instructionsUrl }))}
              />
            ) : loaded && !groups.length ? (
              <PaneEmptyState>{instructions.length ? 'No matching instructions.' : 'No instructions.'}</PaneEmptyState>
            ) : null}
            {groups.length ? (
              <PaneListRows
                groups={groups}
                getItemKey={(item) => item.path}
                renderItem={(item) => (
                  <InstructionCard instruction={item} selected={item.path === selectedPath} onSelect={selectInstructionPath} />
                )}
              />
            ) : null}
            <PaneListFooter>
              <span><span className="num">{instructions.length}</span> instructions</span>
              <span>{editableCount} editable</span>
            </PaneListFooter>
          </>
        )}
      </PaneSection>

      <DetailPane flush className="task-detail instructions-detail">
        {selected ? (
          <>
            <DetailHeader
              title={selected.title}
              subtitle={selected.path}
              actions={actions}
              meta={[
                selected.editable
                  ? <DetailStatus key="status" tone="ok">Editable</DetailStatus>
                  : <DetailStatus key="status" tone="muted">Bundled with {groupLabel(selected.group)} · read-only</DetailStatus>,
                ...(viewingRevisionId
                  ? [<span key="revision" className="muted">revision {formatRevisionTime(revisions.find((rev) => rev.revision_id === viewingRevisionId))}</span>]
                  : selected.updated_at ? [<span key="updated" className="muted">updated {formatCompactRelativeAge(selected.updated_at)} ago</span>] : []),
              ]}
            />
            <div className="task-detail__body instructions-detail__body">
              {editing ? <InlineFailure failure={draftSaveError} operation="Save instruction" /> : null}
              {!editing && restoreAction.error ? <InlineFailure failure={restoreAction.error} operation="Restore instruction revision" /> : null}
              {contentRequest.error ? (
                <InlineFailure
                  failure={contentRequest.error}
                  operation="Load instruction content"
                  onRetry={() => dispatch(fetchInstruction({ instructionsUrl, path: selectedPath }))}
                />
              ) : null}
              {revisionsRequest.error ? (
                <InlineFailure
                  failure={revisionsRequest.error}
                  operation="Load instruction revisions"
                  onRetry={() => dispatch(fetchInstructionRevisions({ instructionsUrl, path: selectedPath }))}
                />
              ) : null}
              {viewingRevisionId && revisionRequest.error ? (
                <InlineFailure
                  failure={revisionRequest.error}
                  operation="Load instruction revision"
                  onRetry={() => dispatch(fetchInstructionRevision({ instructionsUrl, path: selectedPath, revisionId: viewingRevisionId }))}
                />
              ) : null}
              {editing ? (
                <textarea
                  className="instructions-editor"
                  aria-label="Instruction file"
                  value={draft.content}
                  onChange={(event) => {
                    setDraft((current) => ({ ...current, content: event.target.value }));
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 's' && (event.metaKey || event.ctrlKey)) {
                      event.preventDefault();
                      save();
                    } else if (event.key === 'Escape') {
                      event.preventDefault();
                      cancelEdit();
                    }
                  }}
                  spellCheck={false}
                  disabled={draftSaving}
                />
              ) : !shown ? (
                (viewingRevisionId ? revisionRequest.error : contentRequest.error)
                  ? null
                  : <p className="muted">Loading…</p>
              ) : (
                <article className="instructions-reader">
                  <p className="instructions-reader__description">{shown.front_matter?.description}</p>
                  <InstructionBody body={shown.body} />
                  {placeholders.length > 0 && (
                    <section className="instructions-section">
                      <h3>Placeholders</h3>
                      <p className="muted">{placeholderNote(selected)}</p>
                      <ul className="instructions-variables">
                        {placeholders.map((name) => (
                          <li key={name}>
                            <code>{`{${name}}`}</code>
                            {typeof inputTypes[name] === 'string' ? <span className="muted"> {inputTypes[name]}</span> : null}
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}
                  {revisions.length > 0 && (
                    <section className="instructions-section instructions-revisions">
                      <h3>Revisions</h3>
                      <ul>
                        {revisions.map((rev) => (
                          <li key={rev.revision_id}>
                            <a
                              href={`?${new URLSearchParams(viewingRevisionId === rev.revision_id ? {} : { revision: rev.revision_id })}`}
                              aria-current={viewingRevisionId === rev.revision_id ? 'true' : undefined}
                              onClick={(event) => toggleRevision(event, rev.revision_id)}
                            >
                              {formatRevisionTime(rev)}
                            </a>
                            {rev.current ? <span className="muted"> · current</span> : null}
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}
                </article>
              )}
            </div>
          </>
        ) : (
          <PaneEmptyState detail>{loaded ? 'Select an instruction.' : 'Loading instructions…'}</PaneEmptyState>
        )}
      </DetailPane>
    </PageShell>
  );
}
