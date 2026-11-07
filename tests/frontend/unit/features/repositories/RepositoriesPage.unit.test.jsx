// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  dispatch: vi.fn(),
  fetchRepos: vi.fn((payload) => ({ type: 'repositories/fetch', payload })),
  navigate: vi.fn(),
  streamReady: false,
}));

vi.mock('react-redux', () => ({
  useDispatch: () => mocks.dispatch,
  useSelector: (selector) => selector({}),
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => mocks.navigate }));
vi.mock('@frontend/context/ConfigContext', () => ({
  useConfig: () => ({ getApiUrl: (endpoint) => `/api/${endpoint}` }),
}));
vi.mock('@frontend/realtime/realtimeSlice', () => ({
  selectRealtimeStreamReady: () => mocks.streamReady,
}));
vi.mock('@frontend/hooks/useStreamTopic', () => ({ default: () => {} }));
vi.mock('@frontend/hooks/useUiAction', () => ({ default: () => ({ busy: false, error: null }) }));
vi.mock('@frontend/hooks/useConfirmAction', () => ({
  default: () => ({ busy: false, close: vi.fn(), confirm: vi.fn(), error: null, open: false, request: vi.fn() }),
}));
vi.mock('@frontend/hooks/useEntityDraft', () => ({ default: () => [null, vi.fn()] }));
vi.mock('@frontend/lib/browserContracts', () => ({
  dispatchQuickChatOpen: vi.fn(),
  QUICK_CHAT_MODE: { REPOSITORY_CREATE: 'repository-create' },
}));
vi.mock('@frontend/features/repositories/state', () => ({
  deleteRepo: vi.fn(),
  fetchRepoDetail: vi.fn(),
  fetchRepoUpstream: vi.fn(),
  fetchRepos: mocks.fetchRepos,
  selectRepoDetail: () => null,
  selectRepos: () => [],
  selectReposState: () => ({ error: null, loading: false }),
  selectWorkspaceSignal: () => ({}),
}));
vi.mock('@frontend/components/layout/PaneList', () => ({
  PaneEmptyState: ({ children }) => <div>{children}</div>,
  PaneList: ({ children }) => <div>{children}</div>,
  PaneListFilters: () => null,
  PaneListFooter: ({ children }) => <div>{children}</div>,
  PaneListRows: () => null,
  PaneListSearch: () => null,
  PaneListToolbar: ({ children }) => <div>{children}</div>,
}));
vi.mock('@frontend/components/layout/DetailHeader', () => ({
  default: () => null,
  DeleteActionIcon: () => null,
  DetailHeaderAction: () => null,
  DetailHeaderActions: ({ children }) => <div>{children}</div>,
  DetailStatus: ({ children }) => <span>{children}</span>,
  OpenActionIcon: () => null,
  ReconnectActionIcon: () => null,
}));
vi.mock('@frontend/components/layout/PageShell', () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock('@frontend/components/layout/DetailPane', () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock('@frontend/components/PopupModal', () => ({ default: () => null }));
vi.mock('@frontend/components/InlineFailure', () => ({ default: () => null }));

import RepositoriesPage from '@frontend/features/repositories/RepositoriesPage';

function renderPage() {
  return <RepositoriesPage />;
}

describe('RepositoriesPage roster hydration', () => {
  let host;
  let root;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    mocks.dispatch.mockClear();
    mocks.fetchRepos.mockClear();
    mocks.navigate.mockClear();
    mocks.streamReady = false;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  });

  it('hydrates once after readiness and once on a later mount', async () => {
    await act(async () => root.render(renderPage()));
    expect(mocks.fetchRepos).not.toHaveBeenCalled();

    mocks.streamReady = true;
    await act(async () => root.render(renderPage()));
    expect(mocks.fetchRepos).toHaveBeenCalledTimes(1);

    await act(async () => root.render(renderPage()));
    expect(mocks.fetchRepos).toHaveBeenCalledTimes(1);

    await act(async () => root.render(null));
    await act(async () => root.render(renderPage()));
    expect(mocks.fetchRepos).toHaveBeenCalledTimes(2);
  });
});
