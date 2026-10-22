import { useEffect } from 'react';
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
import PopupModal from '../../components/PopupModal';
import useConfirmAction from '../../hooks/useConfirmAction';
import { formatCompactRelativeAge } from '../../lib/displayTime';
import { selectRealtime } from '../../realtime/realtimeSlice';
import { deleteCanvas, fetchCanvasFiles, selectCanvas, selectCanvasFiles } from './state';
import CanvasHost from './CanvasHost';
import { CANVAS_STATE_LABEL, CANVAS_STATE_TONE, canvasProblems, canvasState } from './canvasesPageModel';
import styles from './CanvasPage.module.css';

/** Detail pane for one canvas: shared header and the live preview. */
export default function CanvasPage({ name, onAskProgenitor }) {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { getApiUrl } = useConfig();
  const canvasesUrl = getApiUrl('canvases');
  const realtime = useSelector(selectRealtime);
  const summary = useSelector((state) => selectCanvas(state, name));
  const fileEntry = useSelector((state) => selectCanvasFiles(state, name));
  const deleteConfirmation = useConfirmAction(`canvas:${name}`);

  useEffect(() => {
    if (realtime.connected && summary && fileEntry?.revision !== summary.revision) dispatch(fetchCanvasFiles({ canvasesUrl, name }));
  }, [dispatch, fileEntry?.revision, canvasesUrl, name, realtime.connected, summary]);

  if (!summary) return <PaneEmptyState detail>Loading canvas…</PaneEmptyState>;

  const state = canvasState(summary);
  const reload = () => dispatch(fetchCanvasFiles({ canvasesUrl, name }));
  const requestDelete = () => deleteConfirmation.request('Delete canvas', async ({ isCurrent }) => {
    await dispatch(deleteCanvas({ canvasesUrl, name })).unwrap();
    if (isCurrent()) navigate('/canvases', { replace: true });
  });

  const actions = (
    <DetailHeaderActions menuLabel="Canvas actions">
      <DetailHeaderAction
        icon={<EditActionIcon />}
        label="Ask Progenitor"
        tooltip="Ask Progenitor to change this canvas"
        onClick={() => onAskProgenitor(`Update the /canvases/${name}/ canvas: `)}
      />
      <DetailHeaderAction
        icon={<ReconnectActionIcon />}
        label="Reload"
        tooltip="Reload canvas"
        onClick={reload}
        disabled={!realtime.connected}
      />
      <DetailHeaderAction
        icon={<DeleteActionIcon />}
        label="Delete"
        tooltip="Delete canvas"
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
        meta={[
          <DetailStatus key="status" tone={CANVAS_STATE_TONE[state]}>{CANVAS_STATE_LABEL[state]}</DetailStatus>,
          <span key="files" className="muted">{summary.files?.length || 0} files</span>,
          summary.libraries?.length ? <span key="libs" className="muted">{summary.libraries.join(', ')}</span> : null,
          summary.artifacts?.length ? <span key="artifacts" className={`muted ${styles.mono}`} title="Artifacts this canvas visualises">data: {summary.artifacts.join(', ')}</span> : null,
          <span key="rev" className={`muted ${styles.mono}`}>rev {summary.revision}</span>,
          summary.updated_at ? <span key="updated" className="muted">updated {formatCompactRelativeAge(summary.updated_at)} ago</span> : null,
        ]}
      />
      {state === 'error' && canvasProblems(summary).length ? (
        <ul className={styles.problem} role="alert">
          {canvasProblems(summary).map((problem) => <li key={problem}>{problem}</li>)}
        </ul>
      ) : null}

      <div className={styles.preview}>
        {fileEntry?.files
          ? <CanvasHost key={summary.revision} summary={summary} files={fileEntry.files} canvasesUrl={canvasesUrl} />
          : <PaneEmptyState detail>Loading files…</PaneEmptyState>}
      </div>

      {deleteConfirmation.open ? (
        <PopupModal
          title="Delete canvas?"
          message={`Delete “${summary.title || name}” and its saved data? This cannot be undone.`}
          confirmLabel="Delete canvas"
          busy={deleteConfirmation.busy}
          error={deleteConfirmation.error}
          onClose={deleteConfirmation.close}
          onConfirm={() => { void deleteConfirmation.confirm(); }}
        />
      ) : null}
    </>
  );
}
