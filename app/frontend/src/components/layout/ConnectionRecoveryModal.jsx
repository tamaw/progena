import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useDispatch } from 'react-redux';
import { useConfig } from '../../context/ConfigContext';
import { subscribeUiEvent } from '../../store/realtimeEvents';
import { startRealtimeStream } from '../../store/realtimeListener';
import Modal from '../common/Modal';

const STATUS_GRACE_MS = 20000;
const RECONNECT_INTERVAL_MS = 10000;

export default function ConnectionRecoveryModal() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const streamUrl = getApiUrl('stream');
  const titleId = useId();
  const reconnectRef = useRef(null);
  const timingRef = useRef({ lastStatusAt: 0, retryAt: null });
  const [countdown, setCountdown] = useState(null);

  const reconnect = useCallback(() => {
    timingRef.current.retryAt = Date.now() + RECONNECT_INTERVAL_MS;
    setCountdown(RECONNECT_INTERVAL_MS / 1000);
    dispatch(startRealtimeStream({ streamUrl, force: true }));
  }, [dispatch, streamUrl]);

  useEffect(() => {
    const timing = timingRef.current;
    timing.lastStatusAt = Date.now();
    timing.retryAt = null;
    const unsubscribe = subscribeUiEvent((event) => {
      // Headers, connection.ready and unrelated events do not prove that the
      // backend's periodic status publisher is running again.
      if (event.type !== 'status.updated') return;
      timing.lastStatusAt = Date.now();
      timing.retryAt = null;
      setCountdown(null);
    });
    const timer = setInterval(() => {
      const now = Date.now();
      if (now - timing.lastStatusAt < STATUS_GRACE_MS) return;
      timing.retryAt ??= now + RECONNECT_INTERVAL_MS;
      if (now >= timing.retryAt) reconnect();
      else setCountdown(Math.ceil((timing.retryAt - now) / 1000));
    }, 1000);
    return () => {
      unsubscribe();
      clearInterval(timer);
    };
  }, [reconnect]);

  if (countdown === null) return null;

  return (
    <Modal
      busy
      className="sidebar-modal popup-modal"
      backdropClassName="sidebar-modal-backdrop popup-modal-backdrop"
      ariaLabelledby={titleId}
      initialFocusRef={reconnectRef}
    >
      <header className="sidebar-modal__head">
        <div>
          <span className="sidebar-modal__eyebrow">Connection interrupted</span>
          <h3 id={titleId}>Waiting to reconnect</h3>
        </div>
      </header>
      <p className="sidebar-modal__field">
        No status updates have arrived for at least 20 seconds. This dialog will close when updates resume.
      </p>
      <p className="sidebar-modal__field">Next reconnect attempt in {countdown}s.</p>
      <p className="sidebar-modal__field">Refreshing the page discards unsaved changes.</p>
      <footer className="sidebar-modal__foot">
        <button type="button" className="task-btn task-btn--secondary" onClick={() => window.location.reload()}>
          Refresh page
        </button>
        <button ref={reconnectRef} type="button" className="task-btn task-btn--primary" onClick={reconnect}>
          Reconnect now
        </button>
      </footer>
    </Modal>
  );
}
