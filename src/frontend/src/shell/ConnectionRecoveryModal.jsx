import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useDispatch } from 'react-redux';
import { useConfig } from '../context/ConfigContext';
import { subscribeUiEvent } from '../realtime/realtimeEvents';
import { startRealtimeStream, stopRealtimeStream } from '../realtime/realtimeListener';
import { createOperationRequestId, requestAcknowledged, subscribeSessionExpiry } from '../store/http';
import useIdleTimeout from '../hooks/useIdleTimeout';
import Modal from '../components/Modal';

const STATUS_GRACE_MS = 20000;
const RECONNECT_INTERVAL_MS = 10000;

export default function ConnectionRecoveryModal() {
  const dispatch = useDispatch();
  const { config, getApiUrl } = useConfig();
  const streamUrl = getApiUrl('stream');
  const titleId = useId();
  const reconnectRef = useRef(null);
  const pausedRef = useRef(false);
  const timingRef = useRef({ lastStatusAt: 0, retryAt: null });
  const preferencesUrl = getApiUrl('preferences');
  const [countdown, setCountdown] = useState(null);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [checking, setChecking] = useState(false);
  const [paused, resumeIdle] = useIdleTimeout(config.session.idleTimeoutMinutes * 60_000);

  const reconnect = useCallback(() => {
    timingRef.current.retryAt = Date.now() + RECONNECT_INTERVAL_MS;
    setCountdown(RECONNECT_INTERVAL_MS / 1000);
    dispatch(startRealtimeStream({ streamUrl, force: true }));
  }, [dispatch, streamUrl]);

  // The proxy's sign-in lapsed: requests fail while the open stream still looks
  // healthy. Retrying is a real request; success clears the dialog and resyncs.
  const retrySignIn = useCallback(async () => {
    setChecking(true);
    try {
      await requestAcknowledged(preferencesUrl, { requestId: createOperationRequestId() });
      dispatch(startRealtimeStream({ streamUrl, force: true }));
    } catch {
      // Still rejected or offline; the dialog stays open and reports it.
    } finally {
      setChecking(false);
    }
  }, [dispatch, preferencesUrl, streamUrl]);

  // Idle: stop the stream so nothing updates behind the dialog. Resuming opens
  // a fresh stream; RealtimeBridge refetches its snapshots once it connects.
  useEffect(() => {
    pausedRef.current = paused;
    if (paused) dispatch(stopRealtimeStream());
  }, [dispatch, paused]);

  const resume = useCallback(() => {
    timingRef.current.lastStatusAt = Date.now();
    timingRef.current.retryAt = null;
    setCountdown(null);
    resumeIdle();
    dispatch(startRealtimeStream({ streamUrl }));
  }, [dispatch, resumeIdle, streamUrl]);

  useEffect(() => subscribeSessionExpiry(setSessionExpired), []);

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
      if (pausedRef.current) return;
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

  if (paused) {
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
            <span className="sidebar-modal__eyebrow">Session timed out</span>
            <h3 id={titleId}>Updates paused</h3>
          </div>
        </header>
        <p className="sidebar-modal__field">
          You have been inactive for {config.session.idleTimeoutMinutes} minutes, so live updates are paused.
          Resume to catch up; nothing you have entered is lost.
        </p>
        <footer className="sidebar-modal__foot">
          <button ref={reconnectRef} type="button" className="task-btn task-btn--primary" onClick={resume}>
            Resume
          </button>
        </footer>
      </Modal>
    );
  }

  if (countdown === null && !sessionExpired) return null;

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
          <span className="sidebar-modal__eyebrow">{sessionExpired ? 'Sign-in expired' : 'Connection interrupted'}</span>
          <h3 id={titleId}>{sessionExpired ? 'Sign in again' : 'Waiting to reconnect'}</h3>
        </div>
      </header>
      {sessionExpired ? (
        <p className="sidebar-modal__field">
          The server rejected a request because your sign-in has expired, so changes cannot be saved
          and the page may be out of date. Reload to sign in again, or retry if you have signed in elsewhere.
        </p>
      ) : (
        <>
          <p className="sidebar-modal__field">
            No status updates have arrived for at least 20 seconds. This dialog will close when updates resume.
          </p>
          <p className="sidebar-modal__field">Next reconnect attempt in {countdown}s.</p>
        </>
      )}
      <p className="sidebar-modal__field">Refreshing the page discards unsaved changes.</p>
      <footer className="sidebar-modal__foot">
        <button type="button" className="task-btn task-btn--secondary" onClick={() => window.location.reload()}>
          Refresh page
        </button>
        <button
          ref={reconnectRef}
          type="button"
          className="task-btn task-btn--primary"
          disabled={checking}
          onClick={sessionExpired ? retrySignIn : reconnect}
        >
          {sessionExpired ? 'Retry' : 'Reconnect now'}
        </button>
      </footer>
    </Modal>
  );
}
