import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { requestModuleDocuments } from '@frontend/features/modules/state';
import { publishUiEvent } from '@frontend/realtime/realtimeEvents';
import { setRealtimeClientReady } from '@frontend/realtime/realtimeClient';

beforeEach(() => {
  setRealtimeClientReady(true);
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => new Response(JSON.stringify({
    request_id: options.headers['X-Request-ID'], event_types: ['module.documents.loaded'],
  }), { status: 202 })));
});
afterEach(() => vi.unstubAllGlobals());

describe('module bridge requests', () => {
  it('resolves with the stream reply carrying its request id, ignoring other replies', async () => {
    const reply = requestModuleDocuments({ modulesUrl: '/api/ui/modules', name: 'demo', body: { type: 'kanban_card' } });
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    const requestId = fetch.mock.calls[0][1].headers['X-Request-ID'];

    publishUiEvent({ type: 'module.documents.loaded', payload: { request_id: 'someone-else', items: [{ key: 'other' }] } });
    publishUiEvent({ type: 'module.documents.loaded', payload: { request_id: requestId, items: [{ key: 'kanban_card:a' }], cursor: null } });

    await expect(reply).resolves.toMatchObject({ items: [{ key: 'kanban_card:a' }], cursor: null });
  });
});
