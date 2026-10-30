import { describe, expect, it } from 'vitest';
import reducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import {
  fetchChannels, patchChannel, testChannel,
} from '@frontend/features/channels/state';

const channel = {
  id: 'telegram', name: 'Telegram', status: 'connected', enabled: true,
};
const snapshot = (channels, requestId) => applyUiEvent({
  type: 'channels.snapshot',
  payload: { channels, credential_storage: { database: true }, request_id: requestId },
});

describe('channel request lifecycle', () => {
  it('keeps GET pending until its correlated snapshot arrives', () => {
    let state = reducer(undefined, fetchChannels.pending('load-a', {}));
    state = reducer(state, fetchChannels.fulfilled({ request_id: 'load-a', event_types: ['channels.snapshot'] }, 'load-a', {}));
    expect(state.channels.loading).toBe(true);
    expect(state.channels.loaded).toBe(false);

    state = reducer(state, snapshot([channel], 'load-a'));
    expect(state.channels.loading).toBe(false);
    expect(state.channels.items).toEqual([channel]);
    expect(state.channels.credentialStorage).toEqual({ database: true });
  });

  it('uses unsolicited snapshots as authoritative data without settling unrelated requests', () => {
    const args = { channelsUrl: '/api/ui/channels', channelId: channel.id, patch: { enabled: false } };
    let state = reducer(undefined, patchChannel.pending('patch-a', args));
    state = reducer(state, snapshot([{ ...channel, enabled: false }]));

    expect(state.channels.items).toEqual([{ ...channel, enabled: false }]);
    expect(state.channels.requests['patch-a']).toMatchObject({ loading: true, error: null });
  });

  it('records rejected channel patches without changing canonical channel data', () => {
    const args = { channelsUrl: '/api/ui/channels', channelId: channel.id, patch: { settings: { chat_id: '' } } };
    let state = reducer(undefined, patchChannel.pending('patch-a', args));
    state = reducer(state, patchChannel.rejected(new Error('chat_id is required'), 'patch-a', args));

    expect(state.channels.items).toEqual([]);
    expect(state.channels.requests['patch-a']).toMatchObject({ loading: false, error: 'chat_id is required' });
  });

  it('stores test results by channel and settles only the correlated test request', () => {
    const args = { channelsUrl: '/api/ui/channels', channelId: channel.id };
    let state = reducer(undefined, testChannel.pending('test-a', args));
    state = reducer(state, applyUiEvent({
      type: 'channel.tested',
      payload: { channel_id: channel.id, ok: false, error: 'Token rejected', request_id: 'test-a' },
    }));

    expect(state.channels.testsByChannelId[channel.id]).toEqual({
      ok: false, error: 'Token rejected', requestId: 'test-a',
    });
    expect(state.channels.requests['test-a']).toMatchObject({ loading: false, error: null });
  });

  it('clears a channel’s prior test result when another test starts', () => {
    const args = { channelsUrl: '/api/ui/channels', channelId: channel.id };
    let state = reducer(undefined, applyUiEvent({
      type: 'channel.tested',
      payload: { channel_id: channel.id, ok: true, request_id: 'old-test' },
    }));
    state = reducer(state, testChannel.pending('test-a', args));

    expect(state.channels.testsByChannelId[channel.id]).toBeUndefined();
  });
});
