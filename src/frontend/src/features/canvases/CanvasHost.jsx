import { UI_EVENT } from '../../realtime/eventVocabulary';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../../context/ConfigContext';
import { CANVAS_LIBRARY_SOURCES } from './vendor/libraries';
import { clearCanvasSubscription, reportCanvasStatus, requestCanvasData, requestCanvasDocuments, updateCanvasSubscription } from './state';
import { fetchDocumentTypes, selectDocumentTypes, selectDocumentTypesState } from '../documents/state';
import { subscribeUiEvent } from '../../realtime/realtimeEvents';
import { buildCanvasSrcdoc, normalizeCanvasTheme } from './canvasHostModel';
import { CANVAS_PROTOCOL } from './canvasProtocol';
import styles from './CanvasHost.module.css';

const { channel: CHANNEL, message: MESSAGE, method: METHOD } = CANVAS_PROTOCOL;

function hostTheme() {
  const computed = getComputedStyle(document.documentElement);
  const tokens = {};
  for (const name of Array.from(computed)) {
    if (name.startsWith('--')) tokens[name] = computed.getPropertyValue(name).trim();
  }
  return normalizeCanvasTheme({ mode: document.documentElement.dataset.theme, tokens });
}

export default function CanvasHost({ summary, files, canvasesUrl }) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const documentTypesUrl = getApiUrl(METHOD.DOCUMENT_TYPES);
  const iframeRef = useRef(null);
  const subscribedRef = useRef(false);
  const [srcdoc, setSrcdoc] = useState('');
  const [buildErrors, setBuildErrors] = useState([]);
  const documentTypes = useSelector(selectDocumentTypes);
  const documentTypesState = useSelector(selectDocumentTypesState);
  const name = summary.name;
  const revision = summary.revision;

  const report = useCallback((state, errors = []) => {
    const saved = summary.status;
    if (saved?.state === state && saved?.revision === revision) return;
    dispatch(reportCanvasStatus({ canvasesUrl, name, state, revision, errors }));
  }, [dispatch, canvasesUrl, name, revision, summary.status]);

  useEffect(() => {
    if (!documentTypesState.loaded && !documentTypesState.loading) {
      dispatch(fetchDocumentTypes({ documentTypesUrl }));
    }
  }, [dispatch, documentTypesState.loaded, documentTypesState.loading, documentTypesUrl]);

  useEffect(() => {
    let active = true;
    setSrcdoc('');
    buildCanvasSrcdoc({
      name,
      entry: summary.entry,
      files: files || {},
      librarySources: CANVAS_LIBRARY_SOURCES,
      theme: hostTheme(),
    }).then(({ srcdoc: nextSrcdoc, errors }) => {
      if (!active) return;
      setBuildErrors(errors);
      if (errors.length) report('error', errors);
      else setSrcdoc(nextSrcdoc);
    }).catch((error) => {
      if (!active) return;
      const errors = [{ message: error.message, stack: error.stack }];
      setBuildErrors(errors);
      report('error', errors);
    });
    return () => { active = false; };
  }, [files, name, report, revision, summary.entry]); // The revision key deliberately remounts on hot reload.

  // The srcdoc carries the theme at build time; later light/dark switches are
  // pushed live so canvases restyle without remounting (and losing their state).
  const postTheme = useCallback(() => {
    iframeRef.current?.contentWindow?.postMessage({ channel: CHANNEL, type: MESSAGE.THEME, theme: hostTheme() }, '*');
  }, []);
  useEffect(() => {
    const observer = new MutationObserver(postTheme);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, [postTheme]);

  useEffect(() => subscribeUiEvent((event) => {
    if (event?.type !== UI_EVENT.CANVAS_DOCUMENT_EVENT || event?.payload?.name !== name) return;
    iframeRef.current?.contentWindow?.postMessage({ channel: CHANNEL, type: MESSAGE.EVENT, event: event.payload.event }, '*');
  }), [name]);

  useEffect(() => () => {
    if (subscribedRef.current) {
      subscribedRef.current = false;
      void clearCanvasSubscription({ canvasesUrl, name }).catch(() => {});
    }
  }, [canvasesUrl, name]);

  useEffect(() => {
    const handleMessage = async (event) => {
      if (event.source !== iframeRef.current?.contentWindow || event.data?.channel !== CHANNEL) return;
      const message = event.data;
      const reply = (payload, error) => iframeRef.current?.contentWindow?.postMessage({
        channel: CHANNEL, type: MESSAGE.REPLY, requestId: message.requestId, payload, error: error ? String(error.message || error) : undefined,
      }, '*');
      if (message.type === MESSAGE.MOUNTED) {
        report('loaded');
        return;
      }
      if (message.type === MESSAGE.ERROR) {
        report('error', [message.error || { message: 'Canvas execution failed' }]);
        return;
      }
      if (message.type !== MESSAGE.REQUEST) return;
      try {
        if (message.method === METHOD.DOCUMENTS) {
          reply(await requestCanvasDocuments({ canvasesUrl, name, body: message.payload }));
        } else if (message.method === METHOD.DATA) {
          reply(await requestCanvasData({ canvasesUrl, name, body: message.payload }));
        } else if (message.method === METHOD.DOCUMENT_TYPES) {
          reply(documentTypes);
        } else if (message.method === METHOD.SUBSCRIBE) {
          const events = Array.isArray(message.payload?.events) ? message.payload.events : [];
          if (!subscribedRef.current) subscribedRef.current = true;
          reply(await updateCanvasSubscription({ canvasesUrl, name, body: { events } }));
        } else {
          throw new Error(`Unknown canvas host request: ${message.method}`);
        }
      } catch (error) {
        reply(undefined, error);
      }
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [documentTypes, canvasesUrl, name, report, revision]);

  if (buildErrors.length) {
    return <div className={styles.error} role="alert">{buildErrors.map((error) => <p key={`${error.path || ''}:${error.message}`}>{error.path ? `${error.path}: ` : ''}{error.message}</p>)}</div>;
  }
  return srcdoc ? <iframe ref={iframeRef} className={styles.frame} sandbox="allow-scripts" srcDoc={srcdoc} title={summary.title} onLoad={postTheme} /> : <div className={styles.loading}>Loading canvas…</div>;
}
