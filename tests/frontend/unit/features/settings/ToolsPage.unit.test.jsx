// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  dispatch: vi.fn(() => ({ unwrap: () => Promise.resolve() })),
  fetchMcp: vi.fn((payload) => ({ type: 'mcp/fetch', payload })),
  realtime: { connected: true, streamReady: false },
}));


vi.mock('react-redux', () => ({
  useDispatch: () => mocks.dispatch,
  useSelector: (selector) => selector({}),
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn(), useParams: () => ({}) }));
vi.mock('@frontend/lib/routes', () => ({ buildEntityPath: () => '/tools', useEntityUrlSync: () => {} }));
vi.mock('@frontend/lib/browserContracts', () => ({
  dispatchQuickChatOpen: vi.fn(),
  QUICK_CHAT_MODE: { MCP_CREATE: 'mcp-create' },
}));
vi.mock('@frontend/context/ConfigContext', () => ({
  useConfig: () => ({ getApiUrl: (endpoint) => `/api/${endpoint}` }),
}));
vi.mock('@frontend/context/NotificationContext', () => ({ useNotifications: () => ({ show: vi.fn() }) }));
vi.mock('@frontend/realtime/realtimeSlice', () => ({ selectRealtime: () => mocks.realtime }));
vi.mock('@frontend/hooks/useStreamTopic', () => ({ default: () => {} }));
vi.mock('@frontend/hooks/useConfirmAction', () => ({
  default: () => ({ busy: false, close: vi.fn(), confirm: vi.fn(), error: null, open: false, request: vi.fn() }),
}));
vi.mock('@frontend/hooks/useMissingEntityRoute', () => ({ default: () => {} }));
vi.mock('@frontend/hooks/useEntityDraft', () => ({
  default: (_key, draft) => [draft, vi.fn(), {
    beginSave: vi.fn(), clear: vi.fn(), error: null, failSave: vi.fn(), saving: false, trackSave: vi.fn(),
  }],
}));
vi.mock('@frontend/hooks/useUiAction', () => ({ default: () => ({ busy: false, error: null, run: vi.fn() }) }));
vi.mock('@frontend/features/schedules/useWidgetsApi', () => ({
  useWidgetsApi: () => ({ deleteWidget: vi.fn(), updateWidget: vi.fn() }),
}));
vi.mock('@frontend/features/settings/state/mcp', () => ({
  fetchMcp: mocks.fetchMcp,
  fetchMcpServerDetail: vi.fn(),
  selectMcpServers: () => [],
  selectMcpServerDetail: () => null,
  selectMcpServerDetailRequest: () => ({ error: null, loading: false }),
  selectMcpState: () => ({ error: null, loaded: true }),
}));
vi.mock('@frontend/features/settings/state/preferences', () => ({ selectAgentName: () => 'Progena' }));
vi.mock('@frontend/lib/failures', () => ({ reportFailure: vi.fn() }));
vi.mock('@frontend/components/layout/PaneList', () => ({
  PaneEmptyState: ({ children }) => children ?? null,
  PaneList: ({ children }) => children ?? null,
  PaneListFilters: () => null,
  PaneListFooter: ({ children }) => children ?? null,
  PaneListRows: () => null,
  PaneListSearch: () => null,
  PaneListSortButton: () => null,
  PaneListToolbar: ({ children }) => children ?? null,
}));
vi.mock('@frontend/components/layout/PageShell', () => ({ default: ({ children }) => children ?? null }));
vi.mock('@frontend/components/layout/CappedScrollList', () => ({ default: ({ children }) => children ?? null }));
vi.mock('@frontend/components/layout/DetailPane', () => ({ default: ({ children }) => children ?? null }));
vi.mock('@frontend/components/layout/DetailHeader', () => ({
  default: () => null,
  DeleteActionIcon: () => null,
  DetailHeaderAction: () => null,
  DetailHeaderActions: ({ children }) => children ?? null,
  DetailStatus: ({ children }) => children ?? null,
  PauseActionIcon: () => null,
  PlayActionIcon: () => null,
}));
vi.mock('@frontend/components/DetailCards', () => ({ MetricCard: ({ children }) => children ?? null }));
vi.mock('@frontend/components/InlineFailure', () => ({ default: () => null }));
vi.mock('@frontend/components/PopupModal', () => ({ default: () => null }));

import ToolsPage from '@frontend/features/settings/ToolsPage';

describe('ToolsPage MCP hydration', () => {
  let host;
  let root;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    mocks.dispatch.mockClear();
    mocks.fetchMcp.mockClear();
    mocks.realtime = { connected: true, streamReady: false };
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
    await act(async () => root.render(<ToolsPage />));
    expect(mocks.fetchMcp).not.toHaveBeenCalled();

    mocks.realtime = { connected: true, streamReady: true };
    await act(async () => root.render(<ToolsPage />));
    expect(mocks.fetchMcp).toHaveBeenCalledTimes(1);

    await act(async () => root.render(<ToolsPage />));
    expect(mocks.fetchMcp).toHaveBeenCalledTimes(1);

    await act(async () => root.render(null));
    await act(async () => root.render(<ToolsPage />));
    expect(mocks.fetchMcp).toHaveBeenCalledTimes(2);
  });
});
