// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  dispatch: vi.fn(),
  fetchAgentProfiles: vi.fn((payload) => ({ type: 'agents/fetchProfiles', payload })),
  navigate: vi.fn(),
  streamReady: true,
}));

vi.mock('react-redux', () => ({
  useDispatch: () => mocks.dispatch,
  useSelector: (selector) => selector({}),
}));
vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
  useParams: () => ({}),
}));
vi.mock('@frontend/context/ConfigContext', () => ({
  useConfig: () => ({ getApiUrl: (endpoint) => `/api/${endpoint}` }),
}));
vi.mock('@frontend/realtime/realtimeSlice', () => ({
  selectRealtimeStreamReady: () => mocks.streamReady,
}));
vi.mock('@frontend/features/agents/state/agents', () => ({
  fetchAgentProfiles: mocks.fetchAgentProfiles,
  requestAgentDetail: vi.fn((payload) => ({ type: 'agents/requestDetail', payload })),
  selectActiveAgentProfile: () => null,
  selectActiveAgentProfileState: () => ({ error: null, loading: false, agentId: null }),
  selectAgentProfiles: () => [{ id: 'agent-1', kind: 'object' }],
  selectAgentProfilesState: () => ({ loaded: true, error: null }),
}));

import useAgentCatalogue from '@frontend/features/agents/useAgentCatalogue';

function Probe({ renderTick }) {
  useAgentCatalogue({ kind: 'agents', routeBase: '/agents' });
  return <div>{renderTick}</div>;
}

describe('useAgentCatalogue roster hydration', () => {
  let host;
  let root;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    mocks.dispatch.mockClear();
    mocks.fetchAgentProfiles.mockClear();
    mocks.navigate.mockClear();
    mocks.streamReady = true;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  });

  it('requests a roster snapshot on every ready mount, not every render', async () => {
    await act(async () => root.render(<Probe renderTick={0} />));
    expect(mocks.fetchAgentProfiles).toHaveBeenCalledTimes(1);

    await act(async () => root.render(<Probe renderTick={1} />));
    expect(mocks.fetchAgentProfiles).toHaveBeenCalledTimes(1);

    await act(async () => root.render(null));
    await act(async () => root.render(<Probe renderTick={2} />));
    expect(mocks.fetchAgentProfiles).toHaveBeenCalledTimes(2);
  });
});
