import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { requestCanvasDocuments } from '@frontend/features/canvases/state';
import { publishUiEvent } from '@frontend/realtime/realtimeEvents';
import { setRealtimeClientReady } from '@frontend/realtime/realtimeClient';

beforeEach(() => {
  setRealtimeClientReady(true);
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => new Response(JSON.stringify({
    request_id: options.headers['X-Operation-Id'], event_types: ['canvas.documents.loaded'],
  }), { status: 202 })));
});
afterEach(() => vi.unstubAllGlobals());

describe('canvas bridge requests', () => {
  it('resolves with the stream reply carrying its request id, ignoring other replies', async () => {
    const reply = requestCanvasDocuments({ canvasesUrl: '/api/ui/canvases', name: 'demo', body: { type: 'kanban_card' } });
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    const requestId = fetch.mock.calls[0][1].headers['X-Operation-Id'];

    publishUiEvent({ type: 'canvas.documents.loaded', payload: { request_id: 'someone-else', items: [{ key: 'other' }] } });
    publishUiEvent({ type: 'canvas.documents.loaded', payload: { request_id: requestId, items: [{ key: 'kanban_card:a' }], cursor: null } });

    await expect(reply).resolves.toMatchObject({ items: [{ key: 'kanban_card:a' }], cursor: null });
  });
});
