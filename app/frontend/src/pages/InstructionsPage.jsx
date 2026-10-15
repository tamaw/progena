import { useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useMermaidRender } from '../hooks/useMermaidRender';
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
  selectInstructionRevision,
  selectInstructionRevisions,
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
import { formatRelativeTime } from './knowledgePageModel';
import { formatRevisionTime } from './knowledgePagesModel';
import {
  INSTRUCTION_FILTERS, groupInstructions, groupLabel, instructionHref,
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
  const html = useMemo(() => renderSafeMarkdown(body), [body]);
  const containerRef = useRef(null);
  useMermaidRender(containerRef, body);
  return <div ref={containerRef} className="markdown-content" dangerouslySetInnerHTML={{ __html: html }} />;
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
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState('');
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
  const revisions = useSelector((state) => selectInstructionRevisions(state, selectedPath));
  const revision = useSelector((state) => selectInstructionRevision(state, selectedPath, viewingRevisionId));
  const editing = draft !== null;
  const dirty = editing && draft !== instruction?.content;

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

  useEffect(() => {
    setDraft(null);
    setActionError('');
  }, [selectedPath]);

  const selectInstructionPath = (path) => {
    if (path === selectedPath) return;
    if (dirty && !window.confirm('Discard unsaved changes?')) return;
    navigate(instructionHref(path));
  };

  const toggleRevision = (event, revisionId) => {
    event.preventDefault();
    setParams(viewingRevisionId === revisionId ? {} : { revision: revisionId });
  };

  const startEdit = () => {
    setParams({});
    setActionError('');
    setDraft(instruction?.content || '');
  };

  const cancelEdit = () => {
    if (dirty && !window.confirm('Discard unsaved changes?')) return;
    setDraft(null);
    setActionError('');
  };

  const save = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    setActionError('');
    try {
      await dispatch(saveInstruction({ instructionsUrl, path: selectedPath, content: draft })).unwrap();
      setDraft(null);
    } catch (error) {
      setActionError(error?.message || 'Failed to save the instruction.');
    } finally {
      setSaving(false);
    }
  };

  const restore = async () => {
    if (!viewingRevisionId || saving) return;
    if (!window.confirm('Restoring saves this revision as the live instruction.')) return;
    setSaving(true);
    setActionError('');
    try {
      await dispatch(restoreInstructionRevision({ instructionsUrl, path: selectedPath, revisionId: viewingRevisionId })).unwrap();
      setParams({});
    } catch (error) {
      setActionError(error?.message || 'Failed to restore the revision.');
    } finally {
      setSaving(false);
    }
  };

  const viewingCurrent = revisions.find((rev) => rev.revision_id === viewingRevisionId)?.current;
  const shown = viewingRevisionId ? revision : instruction;
  const variables = viewingRevisionId
    ? (Array.isArray(revision?.front_matter?.variables) ? revision.front_matter.variables.map(String) : [])
    : selected?.variables || [];
  const editableCount = instructions.filter((item) => item.editable).length;

  const actions = !selected?.editable ? null : editing ? (
    <DetailHeaderActions menuLabel="Instruction actions">
      <DetailHeaderAction icon={<RejectActionIcon />} label="Cancel" tooltip="Discard changes" onClick={cancelEdit} disabled={saving} />
      <DetailHeaderAction
        icon={<SaveActionIcon />}
        label="Save"
        tooltip={dirty ? 'Save instruction' : 'No changes to save'}
        variant="success"
        onClick={save}
        disabled={!dirty || saving || !realtime.connected}
      />
    </DetailHeaderActions>
  ) : viewingRevisionId && !viewingCurrent ? (
    <DetailHeaderActions menuLabel="Instruction actions">
      <DetailHeaderAction
        icon={<ReconnectActionIcon />}
        label="Restore"
        tooltip="Restore this revision"
        onClick={restore}
        disabled={saving || !revision || !realtime.connected}
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
            <PaneListFilters filters={INSTRUCTION_FILTERS} activeFilter={filter} onFilterChange={setFilter} />
            <PaneListToolbar>
              <PaneListSearch value={query} placeholder="Search instructions" onChange={setQuery} />
            </PaneListToolbar>
            {listError ? <div className="task-alert">{listError}</div> : null}
            {loaded && !groups.length ? (
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
                  : selected.updated_at ? [<span key="updated" className="muted">updated {formatRelativeTime(selected.updated_at)} ago</span>] : []),
              ]}
            />
            <div className="task-detail__body instructions-detail__body">
              {actionError ? <div className="task-alert" role="alert">{actionError}</div> : null}
              {editing ? (
                <textarea
                  className="instructions-editor"
                  aria-label="Instruction file"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
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
                  disabled={saving}
                />
              ) : !shown ? <p className="muted">Loading…</p> : (
                <article className="instructions-reader">
                  <p className="instructions-reader__description">{shown.front_matter?.description}</p>
                  <InstructionBody body={shown.body} />
                  {variables.length > 0 && (
                    <section className="instructions-section">
                      <h3>Variables</h3>
                      <p className="muted">Filled in by the code that sends this instruction.</p>
                      <ul className="instructions-variables">
                        {variables.map((name) => <li key={name}><code>{`{${name}}`}</code></li>)}
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
