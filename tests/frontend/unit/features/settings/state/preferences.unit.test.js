import { afterEach, describe, expect, it, vi } from 'vitest';
import reducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import {
  DEFAULT_AGENT_NAME,
  savePreferences,
  selectAgentName,
  selectPreferencesSaveRequest,
} from '@frontend/features/settings/state/preferences';

const REQUEST_ID = '11111111-1111-4111-8111-111111111111';

function preferencesSnapshot(agentName) {
  return applyUiEvent({
    type: 'preferences.snapshot',
    payload: {
      display_name: '',
      agent_name: agentName,
      timezone: 'UTC',
      theme: 'system',
      nav_collapsed: false,
      pinned_panel: null,
      notification_sounds: false,
      quick_chat_thread_id: null,
      last_open_chat_thread_id: null,
    },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe('agent name preferences', () => {
  it('normalizes a missing or blank agent_name to the default', () => {
    let state = reducer(undefined, preferencesSnapshot('  '));
    expect(selectAgentName({ uiData: state })).toBe(DEFAULT_AGENT_NAME);

    state = reducer(state, preferencesSnapshot('  Atlas  '));
    expect(selectAgentName({ uiData: state })).toBe('Atlas');
    expect(selectPreferencesSaveRequest({ uiData: state }).agentName).toBe('Atlas');
  });

  it('sends agent_name in the full preferences document', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      status: 202,
      text: async () => JSON.stringify({ request_id: REQUEST_ID, event_types: ['preferences.snapshot'] }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await savePreferences({
      requestId: REQUEST_ID,
      preferencesUrl: '/api/ui/preferences',
      displayName: '',
      agentName: 'Atlas',
      timezone: 'UTC',
      theme: 'system',
      navCollapsed: false,
      pinnedPanel: null,
      notificationSounds: false,
      quickChatThreadId: null,
      lastOpenChatId: null,
    })(vi.fn(), vi.fn(), undefined);

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ agent_name: 'Atlas' });
  });
});
