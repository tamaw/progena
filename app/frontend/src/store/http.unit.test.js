import { afterEach, describe, expect, it, vi } from 'vitest';
import { requestAcknowledged } from './http';

const requestId = 'f0e1d2c3-b4a5-4678-9abc-def012345678';

function response(status, body) {
  return { status, text: vi.fn().mockResolvedValue(JSON.stringify(body)) };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('requestAcknowledged', () => {
  it('accepts only the canonical 202 acknowledgement and sends the operation id', async () => {
    const acknowledgement = { request_id: requestId, event_types: ['chat.response.started'] };
    const fetchMock = vi.fn().mockResolvedValue(response(202, acknowledgement));
    vi.stubGlobal('fetch', fetchMock);

    await expect(requestAcknowledged('/api/ui/responses', {
      method: 'POST',
      body: { input: 'hello' },
      requestId,
    })).resolves.toEqual(acknowledgement);

    expect(fetchMock).toHaveBeenCalledWith('/api/ui/responses', {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'X-Request-ID': requestId,
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify({ input: 'hello' }),
    });
  });

  it.each([
    [201, { request_id: requestId, event_types: ['widgets.updated'] }, /\(201\)/],
    [202, { accepted: true, request_id: requestId, event_types: ['widgets.updated'] }, /invalid acknowledgement body/],
    [202, { request_id: requestId, event_types: [] }, /invalid event_types/],
  ])('rejects non-canonical acknowledgement status or body', async (status, body, error) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(status, body)));

    await expect(requestAcknowledged('/api/ui/widgets', { requestId })).rejects.toThrow(error);
  });
});
