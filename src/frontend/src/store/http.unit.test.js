import { afterEach, describe, expect, it, vi } from 'vitest';
import { miniSerializeError } from '@reduxjs/toolkit';
import { failureMessage } from '../lib/failures';
import { requestAcknowledged, subscribeSessionExpiry } from './http';

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
    [201, { request_id: requestId, event_types: ['widgets.updated'] }, 'HTTP_201'],
    [202, { accepted: true, request_id: requestId, event_types: ['widgets.updated'] }, 'INVALID_ACKNOWLEDGEMENT'],
    [202, { request_id: requestId, event_types: [] }, 'INVALID_EVENT_TYPES'],
  ])('rejects non-canonical acknowledgement status or body', async (status, body, code) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(status, body)));

    await expect(requestAcknowledged('/api/ui/widgets', { requestId })).rejects.toMatchObject({ code, requestId });
  });
  it('rejects a 202 acknowledgement correlated to a different request', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(202, {
      request_id: '11111111-2222-4333-8444-555555555555',
      event_types: ['chat.response.started'],
    })));

    await expect(requestAcknowledged('/api/ui/responses', {
      method: 'POST',
      requestId,
    })).rejects.toMatchObject({ code: 'REQUEST_ID_MISMATCH', requestId });
  });

  it('retains HTTP identity and readable field failures through Redux serialization', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(422, {
      detail: [{ loc: ['body', 'name'], msg: 'Field required', type: 'missing' }],
    })));
    const error = await requestAcknowledged('/api/ui/widgets', { requestId }).catch((failure) => failure);
    expect(error).toMatchObject({ status: 422, code: 'HTTP_422', requestId });
    expect(error.message).toContain('name');
    expect(error.message).not.toContain('"loc"');
    expect(miniSerializeError(error)).toMatchObject({ code: 'HTTP_422', message: error.message });
  });

  it('keeps authorization policy identifiable after Redux serializes an error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(403, { detail: 'Private server detail' })));
    const error = await requestAcknowledged('/api/ui/widgets', { requestId }).catch((failure) => failure);
    const serialized = miniSerializeError(error);
    expect(serialized.code).toBe('HTTP_403');
    expect(failureMessage(serialized, 'Save record')).toEqual(failureMessage(error, 'Save record'));
  });

  it('reports a lapsed sign-in on 401 until the proxy accepts a request again', async () => {
    const states = [];
    const unsubscribe = subscribeSessionExpiry((expired) => states.push(expired));
    const acknowledgement = { request_id: requestId, event_types: ['preferences.snapshot'] };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(401, {}))
      .mockResolvedValueOnce(response(500, { detail: 'boom' }))
      .mockResolvedValueOnce(response(202, acknowledgement));
    vi.stubGlobal('fetch', fetchMock);

    await expect(requestAcknowledged('/api/ui/preferences', { requestId })).rejects.toMatchObject({ code: 'HTTP_401' });
    await expect(requestAcknowledged('/api/ui/preferences', { requestId })).rejects.toMatchObject({ code: 'HTTP_500' });
    expect(states).toEqual([true]);
    await requestAcknowledged('/api/ui/preferences', { requestId });
    expect(states).toEqual([true, false]);
    unsubscribe();
  });

  it('distinguishes network errors from cancellation', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(requestAcknowledged('/api/ui/widgets', { requestId })).rejects.toMatchObject({ code: 'NETWORK_ERROR', requestId });
    const abort = new DOMException('Request aborted', 'AbortError');
    fetch.mockRejectedValueOnce(abort);
    await expect(requestAcknowledged('/api/ui/widgets', { requestId })).rejects.toBe(abort);
  });
});
