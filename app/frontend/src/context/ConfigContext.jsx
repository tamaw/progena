/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useState } from 'react';
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
      agentProfiles: '/api/ui/agent-profiles',
      workflows: '/api/ui/workflows',
      workflowRuns: '/api/ui/workflow-runs',
      widgets: '/api/ui/widgets',
      mcp: '/api/ui/mcp',
      kanban: '/api/ui/kanban',
      prompts: '/api/ui/prompts',
      skills: '/api/ui/skills',
      schedules: '/api/ui/schedules',
      logs: '/api/ui/logs',
      actionRun: '/api/ui/actions/run',
      actionGenerate: '/api/ui/actions/generate',
      attachments: '/api/attachments',
      preferences: '/api/ui/preferences',
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
    quickChatHandoff: 'progenitor_quick_chat_handoff'
  }
};

// `workspaceHost` is the host[:port] segment of the active /w/<host>/... route
// (see lib/workspace.js). It is the single source of truth for which backend
// this session talks to -- there is no separate user-editable base URL anymore;
// switching backends means navigating to a different workspace URL.
export function ConfigProvider({ children, workspaceHost }) {
  const [config, setConfig] = useState(() => {
    const savedTheme = localStorage.getItem('progenitor_theme');

    return {
      ...defaultConfig,
      api: {
        ...defaultConfig.api,
        baseUrl: resolveWorkspaceOrigin(workspaceHost)
      },
      theme: {
        ...defaultConfig.theme,
        default: savedTheme || defaultConfig.theme.default
      }
    };
  });

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
