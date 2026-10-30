// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UI_EVENT } from '@frontend/realtime/eventVocabulary';
import { CANVAS_PROTOCOL } from '@frontend/features/canvases/canvasProtocol';

const canvas = vi.hoisted(() => ({
  dispatch: vi.fn(),
  state: {
    uiData: {
      documentTypes: { items: [{ name: 'artifact' }], loaded: true, loading: false },
    },
  },
  buildCanvasSrcdoc: vi.fn(() => Promise.resolve({ srcdoc: '<!doctype html><html><body></body></html>', errors: [] })),
  reportCanvasStatus: vi.fn((payload) => ({ type: 'canvas/status', payload })),
  requestCanvasData: vi.fn(() => Promise.resolve({ value: 'stored' })),
  requestCanvasDocuments: vi.fn(() => Promise.resolve({ items: [{ id: 'report.md' }], cursor: null })),
  updateCanvasSubscription: vi.fn(() => Promise.resolve({ events: ['artifact.updated'] })),
  clearCanvasSubscription: vi.fn(() => Promise.resolve()),
  subscribeUiEvent: vi.fn(),
  uiEventHandler: null,
}));

vi.mock('react-redux', () => ({
  useDispatch: () => canvas.dispatch,
  useSelector: (selector) => selector(canvas.state),
}));

vi.mock('@frontend/context/ConfigContext', () => ({
  useConfig: () => ({ getApiUrl: (endpoint) => `/api/${endpoint}` }),
}));

vi.mock('@frontend/features/canvases/canvasHostModel', () => ({
  buildCanvasSrcdoc: canvas.buildCanvasSrcdoc,
  normalizeCanvasTheme: (theme) => theme,
}));

vi.mock('@frontend/features/canvases/state', () => ({
  clearCanvasSubscription: canvas.clearCanvasSubscription,
  reportCanvasStatus: canvas.reportCanvasStatus,
  requestCanvasData: canvas.requestCanvasData,
  requestCanvasDocuments: canvas.requestCanvasDocuments,
  updateCanvasSubscription: canvas.updateCanvasSubscription,
}));

vi.mock('@frontend/features/documents/state', () => ({
  fetchDocumentTypes: vi.fn((payload) => ({ type: 'documents/fetch', payload })),
  selectDocumentTypes: (state) => state.uiData.documentTypes.items,
  selectDocumentTypesState: (state) => state.uiData.documentTypes,
}));

vi.mock('@frontend/realtime/realtimeEvents', () => ({
  subscribeUiEvent: canvas.subscribeUiEvent.mockImplementation((handler) => {
    canvas.uiEventHandler = handler;
    return () => { canvas.uiEventHandler = null; };
  }),
}));

import CanvasHost from '@frontend/features/canvases/CanvasHost';

const { channel, message: MESSAGE, method: METHOD } = CANVAS_PROTOCOL;

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
}

describe('CanvasHost bridge', () => {
  let host;
  let root;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    document.documentElement.dataset.theme = 'dark';
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    canvas.dispatch.mockClear();
    canvas.reportCanvasStatus.mockClear();
    canvas.requestCanvasData.mockClear();
    canvas.requestCanvasDocuments.mockClear();
    canvas.updateCanvasSubscription.mockClear();
    canvas.subscribeUiEvent.mockClear();
  });

  afterEach(async () => {
    if (root) await act(async () => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  });

  it('isolates parent/frame messages while routing correlated host data, document, event, and theme traffic', async () => {
    await act(async () => {
      root.render(<CanvasHost
        summary={{ name: 'demo', title: 'Demo', revision: 'r1', entry: 'index.mjs', status: null }}
        files={{ 'index.mjs': 'export default () => {}' }}
        canvasesUrl="/api/canvases"
      />);
      await flush();
    });
    const iframe = host.querySelector('iframe');
    const postMessage = vi.spyOn(iframe.contentWindow, 'postMessage').mockImplementation(() => {});
    const foreign = document.createElement('iframe');
    document.body.append(foreign);

    await act(async () => {
      window.dispatchEvent(new MessageEvent('message', {
        source: foreign.contentWindow,
        data: { channel, type: MESSAGE.MOUNTED },
      }));
      window.dispatchEvent(new MessageEvent('message', {
        source: iframe.contentWindow,
        data: { channel: 'foreign-channel', type: MESSAGE.MOUNTED },
      }));
      await flush();
    });
    expect(canvas.reportCanvasStatus).not.toHaveBeenCalled();

    await act(async () => {
      window.dispatchEvent(new MessageEvent('message', {
        source: iframe.contentWindow,
        data: { channel, type: MESSAGE.MOUNTED },
      }));
      window.dispatchEvent(new MessageEvent('message', {
        source: iframe.contentWindow,
        data: { channel, type: MESSAGE.REQUEST, requestId: 'documents-1', method: METHOD.DOCUMENTS, payload: { type: 'artifact' } },
      }));
      window.dispatchEvent(new MessageEvent('message', {
        source: iframe.contentWindow,
        data: { channel, type: MESSAGE.REQUEST, requestId: 'data-2', method: METHOD.DATA, payload: { op: 'get', key: 'layout' } },
      }));
      window.dispatchEvent(new MessageEvent('message', {
        source: iframe.contentWindow,
        data: { channel, type: MESSAGE.REQUEST, requestId: 'subscription-3', method: METHOD.SUBSCRIBE, payload: { events: ['artifact.updated'] } },
      }));
      await flush();
    });

    expect(canvas.reportCanvasStatus).toHaveBeenCalledWith(expect.objectContaining({ state: 'loaded', revision: 'r1' }));
    expect(canvas.requestCanvasDocuments).toHaveBeenCalledWith({ canvasesUrl: '/api/canvases', name: 'demo', body: { type: 'artifact' } });
    expect(canvas.requestCanvasData).toHaveBeenCalledWith({ canvasesUrl: '/api/canvases', name: 'demo', body: { op: 'get', key: 'layout' } });
    expect(canvas.updateCanvasSubscription).toHaveBeenCalledWith({ canvasesUrl: '/api/canvases', name: 'demo', body: { events: ['artifact.updated'] } });
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      channel,
      type: MESSAGE.REPLY,
      requestId: 'documents-1',
      payload: { items: [{ id: 'report.md' }], cursor: null },
    }), '*');
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      channel,
      type: MESSAGE.REPLY,
      requestId: 'data-2',
      payload: { value: 'stored' },
    }), '*');
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      channel,
      type: MESSAGE.REPLY,
      requestId: 'subscription-3',
      payload: { events: ['artifact.updated'] },
    }), '*');

    await act(async () => {
      iframe.dispatchEvent(new Event('load'));
      canvas.uiEventHandler({
        type: UI_EVENT.CANVAS_DOCUMENT_EVENT,
        payload: { name: 'demo', event: { document_type: 'artifact', op: 'updated', id: 'report.md' } },
      });
    });
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({ channel, type: MESSAGE.THEME }), '*');
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      channel,
      type: MESSAGE.EVENT,
      event: { document_type: 'artifact', op: 'updated', id: 'report.md' },
    }), '*');
    foreign.remove();
  });
});
