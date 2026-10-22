import { useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  EditActionIcon,
  ReconnectActionIcon,
} from '../../components/layout/DetailHeader';
import { PaneEmptyState } from '../../components/layout/PaneList';
import InlineFailure from '../../components/InlineFailure';
import MarkdownContent from '../../components/MarkdownContent';
import PopupModal from '../../components/PopupModal';
import { renderSafeMarkdown } from '../../lib/safeMarkdown';
import useConfirmAction from '../../hooks/useConfirmAction';
import useUiAction from '../../hooks/useUiAction';
import { formatCompactRelativeAge, formatShortLocalDateTime } from '../../lib/displayTime';
import { selectRealtime } from '../../realtime/realtimeSlice';
import { deleteModule, fetchModuleFiles, fetchModuleRevision, fetchModuleRevisions, restoreModuleRevision, selectModule, selectModuleFiles, selectModuleRevision, selectModuleRevisions } from './state';
import ModuleHost from './ModuleHost';
import {
  MODULE_STATE_LABEL, MODULE_STATE_TONE, groupModuleRevisions, moduleFileMarkdown, moduleProblems, moduleState,
} from './modulesPageModel';
import styles from './ModulePage.module.css';

const TABS = [
  { id: 'preview', label: 'Preview' },
  { id: 'files', label: 'Files' },
  { id: 'revisions', label: 'Revisions' },
];

function FileView({ path, content }) {
  if (content == null) return <p className="muted">Loading…</p>;
  return (
    <div className={styles.viewer}>
      <MarkdownContent content={moduleFileMarkdown(path, content)} renderMarkdown={renderSafeMarkdown} />
    </div>
  );
}

/** Detail pane for one module: shared header, live preview, files, revisions. */
export default function ModulePage({ name, onAskProgenitor }) {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { getApiUrl } = useConfig();
  const modulesUrl = getApiUrl('modules');
  const realtime = useSelector(selectRealtime);
  const summary = useSelector((state) => selectModule(state, name));
  const fileEntry = useSelector((state) => selectModuleFiles(state, name));
  const revisions = useSelector((state) => selectModuleRevisions(state, name));
  const [tab, setTab] = useState('preview');
  const [selectedPath, setSelectedPath] = useState(null);
  const sessions = useMemo(() => groupModuleRevisions(revisions), [revisions]);
  const [selectedSessionId, setSelectedSessionId] = useState(null);
  const [sessionPath, setSessionPath] = useState(null);
  const session = sessions.find((item) => item.id === selectedSessionId) || null;
  const selectedRevision = session ? session.latestByPath[sessionPath ?? session.paths[0]] : null;
  const revision = useSelector((state) => selectedRevision ? selectModuleRevision(state, name, selectedRevision.id) : null);
  const deleteConfirmation = useConfirmAction(`module:${name}`);
  const restoreAction = useUiAction(`module:${name}`);

  useEffect(() => {
    if (realtime.connected && summary && fileEntry?.revision !== summary.revision) dispatch(fetchModuleFiles({ modulesUrl, name }));
  }, [dispatch, fileEntry?.revision, modulesUrl, name, realtime.connected, summary]);
  useEffect(() => {
    if (realtime.connected && tab === 'revisions') dispatch(fetchModuleRevisions({ modulesUrl, name }));
  }, [dispatch, modulesUrl, name, realtime.connected, tab, summary?.revision]);
  useEffect(() => {
    if (realtime.connected && selectedRevision) dispatch(fetchModuleRevision({ modulesUrl, name, id: selectedRevision.id }));
  }, [dispatch, modulesUrl, name, realtime.connected, selectedRevision]);

  if (!summary) return <PaneEmptyState detail>Loading module…</PaneEmptyState>;

  const state = moduleState(summary);
  const files = fileEntry?.files || {};
  const paths = Object.keys(files).sort();
  const shownPath = selectedPath && paths.includes(selectedPath) ? selectedPath : (summary.entry || paths[0]);

  const reload = () => dispatch(fetchModuleFiles({ modulesUrl, name }));
  const requestDelete = () => deleteConfirmation.request('Delete module', async ({ isCurrent }) => {
    await dispatch(deleteModule({ modulesUrl, name })).unwrap();
    if (isCurrent()) navigate('/modules', { replace: true });
  });
  const restore = () => {
    if (!session) return;
    void restoreAction.run('Restore module revision', async () => {
      await dispatch(restoreModuleRevision({ modulesUrl, name, capturedAt: session.capturedAt })).unwrap();
      setSelectedSessionId(null);
      setTab('preview');
    });
  };

  const actions = (
    <DetailHeaderActions menuLabel="Module actions">
      <DetailHeaderAction
        icon={<EditActionIcon />}
        label="Ask Progenitor"
        tooltip="Ask Progenitor to change this module"
        onClick={() => onAskProgenitor(`Update the /modules/${name}/ module: `)}
      />
      {tab === 'revisions' && session ? (
        <DetailHeaderAction
          icon={<ReconnectActionIcon />}
          label="Restore"
          tooltip="Restore the module to this revision"
          variant="primary"
          onClick={restore}
          disabled={restoreAction.busy || !realtime.connected}
        />
      ) : (
        <DetailHeaderAction
          icon={<ReconnectActionIcon />}
          label="Reload"
          tooltip="Reload module"
          onClick={reload}
          disabled={!realtime.connected}
        />
      )}
      <DetailHeaderAction
        icon={<DeleteActionIcon />}
        label="Delete"
        tooltip="Delete module"
        onClick={requestDelete}
        disabled={deleteConfirmation.busy}
      />
    </DetailHeaderActions>
  );

  return (
    <>
      <DetailHeader
        title={summary.title || name}
        subtitle={summary.description}
        actions={actions}
        tabs={TABS}
        activeTab={tab}
        onTabChange={setTab}
        tabLabel="Module views"
        meta={[
          <DetailStatus key="status" tone={MODULE_STATE_TONE[state]}>{MODULE_STATE_LABEL[state]}</DetailStatus>,
          <span key="files" className="muted">{summary.files?.length || 0} files</span>,
          summary.libraries?.length ? <span key="libs" className="muted">{summary.libraries.join(', ')}</span> : null,
          <span key="rev" className={`muted ${styles.mono}`}>rev {summary.revision}</span>,
          summary.updated_at ? <span key="updated" className="muted">updated {formatCompactRelativeAge(summary.updated_at)} ago</span> : null,
        ]}
      />
      {restoreAction.error ? <InlineFailure failure={restoreAction.error} operation="Restore module revision" /> : null}
      {state === 'error' && moduleProblems(summary).length ? (
        <ul className={styles.problem} role="alert">
          {moduleProblems(summary).map((problem) => <li key={problem}>{problem}</li>)}
        </ul>
      ) : null}

      {tab === 'preview' ? (
        <div className={styles.preview}>
          {fileEntry?.files
            ? <ModuleHost key={summary.revision} summary={summary} files={fileEntry.files} modulesUrl={modulesUrl} />
            : <PaneEmptyState detail>Loading files…</PaneEmptyState>}
        </div>
      ) : null}

      {tab === 'files' ? (
        <div className={styles.split}>
          <nav className={styles.list} aria-label="Module files">
            {paths.map((path) => (
              <button key={path} type="button" className={`sess-row ${path === shownPath ? 'is-selected' : ''}`} onClick={() => setSelectedPath(path)}>
                <span className={`sess-row__agent ${styles.mono}`}>{path}</span>
                <span className="sess-row__title">{(new TextEncoder().encode(files[path]).length / 1024).toFixed(1)} KiB{path === summary.entry ? ' · entry' : ''}</span>
              </button>
            ))}
          </nav>
          {shownPath ? <FileView path={shownPath} content={files[shownPath]} /> : <PaneEmptyState detail>No files.</PaneEmptyState>}
        </div>
      ) : null}

      {tab === 'revisions' ? (
        <div className={styles.split}>
          <nav className={styles.list} aria-label="Module revisions">
            {sessions.length ? sessions.map((item) => (
              <button key={item.id} type="button" className={`sess-row ${session?.id === item.id ? 'is-selected' : ''}`} onClick={() => { setSelectedSessionId(item.id); setSessionPath(null); }}>
                <span className="sess-row__top"><span className={`sess-row__agent ${styles.mono}`}>{item.paths.join(', ')}</span></span>
                <span className="sess-row__age">{item.revisions.length === 1 ? (item.revisions[0].op === 'delete' ? 'deleted' : '1 edit') : `${item.revisions.length} edits`}</span>
                <span className="sess-row__title">{formatShortLocalDateTime(item.capturedAt)}</span>
              </button>
            )) : <PaneEmptyState>No revisions yet.</PaneEmptyState>}
          </nav>
          {session ? (
            <div className={styles.revisionView}>
              {session.paths.length > 1 ? (
                <div className={styles.pathSwitch} role="group" aria-label="Files changed in this session">
                  {session.paths.map((path) => (
                    <button key={path} type="button" className={`dtab detail-tab ${path === selectedRevision?.path ? 'is-active' : ''}`} aria-pressed={path === selectedRevision?.path} onClick={() => setSessionPath(path)}>{path}</button>
                  ))}
                </div>
              ) : null}
              {revision?.op === 'delete'
                ? <PaneEmptyState detail>{selectedRevision.path} was deleted in this session.</PaneEmptyState>
                : <FileView path={selectedRevision.path} content={revision?.content} />}
            </div>
          ) : <PaneEmptyState detail>Each entry is one editing session; select it to see the files as it left them. Restore returns the whole module to that point.</PaneEmptyState>}
        </div>
      ) : null}

      {deleteConfirmation.open ? (
        <PopupModal
          title="Delete module?"
          message={`Delete “${summary.title || name}” and its saved data? This cannot be undone.`}
          confirmLabel="Delete module"
          busy={deleteConfirmation.busy}
          error={deleteConfirmation.error}
          onClose={deleteConfirmation.close}
          onConfirm={() => { void deleteConfirmation.confirm(); }}
        />
      ) : null}
    </>
  );
}
