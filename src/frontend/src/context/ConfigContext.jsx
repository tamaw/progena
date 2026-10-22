/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useLayoutEffect, useState } from 'react';
import { resolveWorkspaceOrigin } from '../lib/workspace';

const ConfigContext = createContext(null);

const defaultConfig = {
  api: {
    baseUrl: '',
    endpoints: {
      responses: '/api/ui/responses',
      stream: '/api/ui/stream',
      chats: '/api/ui/chats',
      inbox: '/api/ui/inbox',
      policies: '/api/ui/policies',
      agentProfiles: '/api/ui/agent-profiles',
      workflows: '/api/ui/workflows',
      workflowRuns: '/api/ui/workflow-runs',
      documentTypes: '/api/ui/document-types',
      widgets: '/api/ui/widgets',
      mcp: '/api/ui/mcp',
      kanban: '/api/ui/kanban',
      instructions: '/api/ui/instructions',
      marketplaces: '/api/ui/marketplaces',
      plugins: '/api/ui/plugins',
      schedules: '/api/ui/schedules',
      repos: '/api/ui/repos',
      modelProfiles: '/api/ui/model-profiles',
      providers: '/api/ui/providers',
      logs: '/api/ui/logs',
      tokenUsage: '/api/ui/insights/token-usage',
      actionRun: '/api/ui/actions/run',
      actionGenerate: '/api/ui/actions/generate',
      attachments: '/api/attachments',
      preferences: '/api/ui/preferences',
      canvases: '/api/ui/canvases',
    },
    timeout: 120000,
    retryAttempts: 3,
    retryDelay: 1000
  },
  chat: {
    maxMessageLength: 100000,
    maxHistoryLength: 100,
    streamingEnabled: true,
    defaultModel: 'progenitor-v1'
  },
  sse: {
    reconnectDelay: 2000,
    maxReconnectAttempts: 10,
    keepaliveTimeout: 35000
  },
  session: {
    idleTimeoutMinutes: 30
  },
  density: {
    default: 'comfortable',
    options: ['comfortable', 'compact', 'presentation']
  },
  theme: {
    default: 'system',
    options: ['system', 'dark', 'light']
  },
  storage: {
    theme: 'progenitor_theme',
    chatHistory: 'progenitor_chat_history',
    settings: 'progenitor_settings',
    inputHistory: 'progenitor_input_history',
    sidebarCollapsed: 'progenitor_sidebar_collapsed',
    pinnedPanel: 'progenitor_pinned_panel',
    streamFilters: 'progenitor_stream_filters',
    quickChatHandoff: 'progenitor_quick_chat_handoff',
    idleTimeoutMinutes: 'progenitor_idle_timeout_minutes',
    density: 'progenitor_density'
  }
};

// `workspaceHost` is the active workspace's host[:port] -- either the plain
// default workspace (the host that served this page, no URL prefix) or an
// explicit /w/<host>/... route for a different backend (see lib/workspace.js).
// It is the single source of truth for which backend this session talks to
// -- there is no separate user-editable base URL anymore; switching backends
// means navigating to a different workspace URL.
export function ConfigProvider({ children, workspaceHost }) {
  const [config, setConfig] = useState(() => {
    const savedTheme = localStorage.getItem('progenitor_theme');
    const savedDensity = localStorage.getItem(defaultConfig.storage.density);
    const savedTimeout = Number(localStorage.getItem(defaultConfig.storage.idleTimeoutMinutes));

    return {
      ...defaultConfig,
      api: {
        ...defaultConfig.api,
        baseUrl: resolveWorkspaceOrigin(workspaceHost)
      },
      theme: {
        ...defaultConfig.theme,
        default: savedTheme || defaultConfig.theme.default
      },
      density: {
        ...defaultConfig.density,
        default: defaultConfig.density.options.includes(savedDensity) ? savedDensity : defaultConfig.density.default
      },
      session: {
        idleTimeoutMinutes: savedTimeout >= 5 && savedTimeout <= 120 ? savedTimeout : defaultConfig.session.idleTimeoutMinutes
      }
    };
  });

  const density = config.density.default;
  useLayoutEffect(() => {
    document.documentElement.dataset.density = density;
  }, [density]);

  const getApiUrl = (endpoint) => {
    const path = config.api.endpoints[endpoint] || endpoint;
    const base = config.api.baseUrl.endsWith('/') ? config.api.baseUrl.slice(0, -1) : config.api.baseUrl;
    const normalizedPath = path.startsWith('/') ? path : `/${path}`;
    return `${base}${normalizedPath}`;
  };

  const setTheme = (theme) => {
    setConfig(prev => ({
      ...prev,
      theme: { ...prev.theme, default: theme }
    }));
    localStorage.setItem('progenitor_theme', theme);
  };

  const setDensity = (density) => {
    setConfig(prev => ({ ...prev, density: { ...prev.density, default: density } }));
    localStorage.setItem(defaultConfig.storage.density, density);
  };

  const setIdleTimeoutMinutes = (minutes) => {
    setConfig(prev => ({ ...prev, session: { ...prev.session, idleTimeoutMinutes: minutes } }));
    localStorage.setItem(defaultConfig.storage.idleTimeoutMinutes, String(minutes));
  };

  const resetSettings = () => {
    setConfig({
      ...defaultConfig,
      api: { ...defaultConfig.api, baseUrl: resolveWorkspaceOrigin(workspaceHost) },
    });
    Object.values(defaultConfig.storage).forEach((key) => localStorage.removeItem(key));
  };

  return (
    <ConfigContext.Provider value={{
      config,
      workspaceHost,
      getApiUrl,
      setTheme,
      setIdleTimeoutMinutes,
      setDensity,
      resetSettings
    }}>
      {children}
    </ConfigContext.Provider>
  );
}

export function useConfig() {
  const context = useContext(ConfigContext);
  if (!context) {
    throw new Error('useConfig must be used within a ConfigProvider');
  }
  return context;
}
