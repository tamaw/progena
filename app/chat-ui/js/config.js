/**
 * Progenitor Chat UI - Configuration
 *
 * Central configuration for the chat UI application.
 */

const Config = {
    // API Configuration
    api: {
        baseUrl: localStorage.getItem('progenitor_api_url') || 'http://localhost:8000',
        endpoints: {
            chat: '/v1/chat/completions',
            responses: '/v1/responses',
            health: '/health',
            healthDetailed: '/health/detailed',
            models: '/v1/models',
            metrics: '/metrics/json'
        },
        timeout: 120000, // 2 minutes for streaming
        retryAttempts: 3,
        retryDelay: 1000
    },

    // Chat Configuration
    chat: {
        maxMessageLength: 100000,
        maxHistoryLength: 100,
        streamingEnabled: true,
        defaultModel: 'progenitor-v1'
    },

    // Status Polling Configuration
    status: {
        pollingInterval: 5000, // 5 seconds
        healthCheckInterval: 10000 // 10 seconds
    },

    // Notification Configuration
    notifications: {
        enabled: localStorage.getItem('progenitor_notifications') !== 'false',
        soundEnabled: localStorage.getItem('progenitor_sounds') === 'true',
        autoHideDuration: 5000, // 5 seconds
        maxVisible: 5
    },

    // Theme Configuration
    theme: {
        default: localStorage.getItem('progenitor_theme') || 'system',
        options: ['system', 'dark', 'light']
    },

    // Storage Keys
    storage: {
        apiUrl: 'progenitor_api_url',
        theme: 'progenitor_theme',
        notifications: 'progenitor_notifications',
        sounds: 'progenitor_sounds',
        chatHistory: 'progenitor_chat_history',
        settings: 'progenitor_settings',
        inputHistory: 'progenitor_input_history',
        sidebarCollapsed: 'progenitor_sidebar_collapsed'
    },

    // UI State defaults
    ui: {
        sidebarCollapsed: localStorage.getItem('progenitor_sidebar_collapsed') === 'true',
        showTimestamps: localStorage.getItem('progenitor_show_timestamps') !== 'false',
        compactMode: localStorage.getItem('progenitor_compact_mode') === 'true'
    },

    /**
     * Get the full URL for an API endpoint.
     * @param {string} endpoint - The endpoint key or path
     * @returns {string} Full URL
     */
    getApiUrl(endpoint) {
        const path = this.api.endpoints[endpoint] || endpoint;
        return `${this.api.baseUrl}${path}`;
    },

    /**
     * Save a configuration value to localStorage.
     * @param {string} key - Storage key
     * @param {any} value - Value to store
     */
    save(key, value) {
        localStorage.setItem(key, JSON.stringify(value));
    },

    /**
     * Load a configuration value from localStorage.
     * @param {string} key - Storage key
     * @param {any} defaultValue - Default value if not found
     * @returns {any} Stored or default value
     */
    load(key, defaultValue = null) {
        const stored = localStorage.getItem(key);
        if (stored === null) return defaultValue;
        try {
            return JSON.parse(stored);
        } catch {
            return stored;
        }
    },

    /**
     * Update API base URL.
     * @param {string} url - New base URL
     */
    setApiUrl(url) {
        this.api.baseUrl = url;
        localStorage.setItem(this.storage.apiUrl, url);
    },

    /**
     * Update theme setting.
     * @param {string} theme - Theme name
     */
    setTheme(theme) {
        this.theme.default = theme;
        localStorage.setItem(this.storage.theme, theme);
    },

    /**
     * Update notification setting.
     * @param {boolean} enabled - Whether notifications are enabled
     */
    setNotifications(enabled) {
        this.notifications.enabled = enabled;
        localStorage.setItem(this.storage.notifications, enabled.toString());
    },

    /**
     * Update sound setting.
     * @param {boolean} enabled - Whether sounds are enabled
     */
    setSounds(enabled) {
        this.notifications.soundEnabled = enabled;
        localStorage.setItem(this.storage.sounds, enabled.toString());
    },

    /**
     * Update UI state setting.
     * @param {string} key - UI setting key
     * @param {any} value - Value to set
     */
    setUi(key, value) {
        if (this.ui.hasOwnProperty(key)) {
            this.ui[key] = value;
            localStorage.setItem(`progenitor_${key}`, value.toString());
        }
    },

    /**
     * Export all settings as JSON.
     * @returns {object} All settings
     */
    exportSettings() {
        return {
            api: { baseUrl: this.api.baseUrl },
            theme: { default: this.theme.default },
            notifications: {
                enabled: this.notifications.enabled,
                soundEnabled: this.notifications.soundEnabled
            },
            ui: { ...this.ui },
            exportedAt: new Date().toISOString()
        };
    },

    /**
     * Import settings from JSON.
     * @param {object} settings - Settings to import
     */
    importSettings(settings) {
        if (settings.api?.baseUrl) {
            this.setApiUrl(settings.api.baseUrl);
        }
        if (settings.theme?.default) {
            this.setTheme(settings.theme.default);
        }
        if (settings.notifications) {
            if (typeof settings.notifications.enabled === 'boolean') {
                this.setNotifications(settings.notifications.enabled);
            }
            if (typeof settings.notifications.soundEnabled === 'boolean') {
                this.setSounds(settings.notifications.soundEnabled);
            }
        }
        if (settings.ui) {
            Object.keys(settings.ui).forEach(key => {
                if (this.ui.hasOwnProperty(key)) {
                    this.setUi(key, settings.ui[key]);
                }
            });
        }
    },

    /**
     * Reset all settings to defaults.
     */
    resetToDefaults() {
        // Clear all storage
        Object.values(this.storage).forEach(key => {
            localStorage.removeItem(key);
        });

        // Reset runtime values
        this.api.baseUrl = 'http://localhost:8000';
        this.theme.default = 'system';
        this.notifications.enabled = true;
        this.notifications.soundEnabled = false;
        this.ui.sidebarCollapsed = false;
        this.ui.showTimestamps = true;
        this.ui.compactMode = false;
    }
};

// Freeze to prevent accidental modifications
Object.freeze(Config.api.endpoints);

// Export for use in other modules
window.Config = Config;
