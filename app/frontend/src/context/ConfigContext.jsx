import { createContext, useContext, useState, useEffect } from 'react';

const ConfigContext = createContext(null);

const defaultConfig = {
  api: {
    baseUrl: 'http://localhost:8000',
    endpoints: {
      chat: '/v1/chat/completions',
      responses: '/v1/responses',
      health: '/health',
      healthDetailed: '/health/detailed',
      events: '/api/ui/events',
      models: '/v1/models'
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
  notifications: {
    enabled: true,
    soundEnabled: false,
    autoHideDuration: 5000,
    maxVisible: 5
  },
  theme: {
    default: 'system',
    options: ['system', 'dark', 'light']
  },
  storage: {
    apiUrl: 'progenitor_api_url',
    theme: 'progenitor_theme',
    notifications: 'progenitor_notifications',
    sounds: 'progenitor_sounds',
    chatHistory: 'progenitor_chat_history',
    settings: 'progenitor_settings',
    inputHistory: 'progenitor_input_history',
    sidebarCollapsed: 'progenitor_sidebar_collapsed'
  }
};

export function ConfigProvider({ children }) {
  const [config, setConfig] = useState(() => {
    const savedTheme = localStorage.getItem('progenitor_theme');
    const savedApiUrl = localStorage.getItem('progenitor_api_url');
    const savedNotifications = localStorage.getItem('progenitor_notifications');
    const savedSounds = localStorage.getItem('progenitor_sounds');

    return {
      ...defaultConfig,
      api: {
        ...defaultConfig.api,
        baseUrl: savedApiUrl || defaultConfig.api.baseUrl
      },
      theme: {
        ...defaultConfig.theme,
        default: savedTheme || defaultConfig.theme.default
      },
      notifications: {
        ...defaultConfig.notifications,
        enabled: savedNotifications !== 'false',
        soundEnabled: savedSounds === 'true'
      }
    };
  });

  const getApiUrl = (endpoint) => {
    const path = config.api.endpoints[endpoint] || endpoint;
    return `${config.api.baseUrl}${path}`;
  };

  const setTheme = (theme) => {
    setConfig(prev => ({
      ...prev,
      theme: { ...prev.theme, default: theme }
    }));
    localStorage.setItem('progenitor_theme', theme);
  };

  const setApiUrl = (url) => {
    setConfig(prev => ({
      ...prev,
      api: { ...prev.api, baseUrl: url }
    }));
    localStorage.setItem('progenitor_api_url', url);
  };

  const setNotifications = (enabled) => {
    setConfig(prev => ({
      ...prev,
      notifications: { ...prev.notifications, enabled }
    }));
    localStorage.setItem('progenitor_notifications', enabled.toString());
  };

  const setSounds = (enabled) => {
    setConfig(prev => ({
      ...prev,
      notifications: { ...prev.notifications, soundEnabled: enabled }
    }));
    localStorage.setItem('progenitor_sounds', enabled.toString());
  };

  return (
    <ConfigContext.Provider value={{
      config,
      getApiUrl,
      setTheme,
      setApiUrl,
      setNotifications,
      setSounds
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
