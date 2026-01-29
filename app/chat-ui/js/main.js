/**
 * Progenitor Chat UI - Main Application
 *
 * Initializes all modules and coordinates the application.
 */

import { KeyboardManager } from './keyboard.js';
import { SearchManager } from './search.js';
import { SessionManager } from './sessions.js';
import { highlighter } from './syntax.js';
import { DraftsManager } from './drafts.js';
import { PromptTemplates } from './templates.js';
import { ContextMenu } from './context-menu.js';
import { MetricsDashboard } from './metrics.js';
import { LogsViewer } from './logs-viewer.js';
import { AccessibilityManager } from './accessibility.js';
import { ExportImport } from './export-import.js';
import { PerformanceManager } from './performance.js';
import { FileAttachments } from './attachments.js';
import { TypingIndicator } from './typing-indicator.js';
import { MessageReactions } from './reactions.js';

// Global managers
let chat = null;
let status = null;
let notifications = null;
let keyboard = null;
let search = null;
let sessions = null;
let drafts = null;
let templates = null;
let contextMenu = null;
let metrics = null;
let logs = null;
let accessibility = null;
let exportImport = null;
let performance = null;
let attachments = null;
let typing = null;
let reactions = null;

/**
 * Initialize the application.
 */
async function initApp() {
    console.log('Initializing Progenitor Chat UI...');

    // Initialize theme
    initTheme();

    // Initialize modules
    notifications = new NotificationManager();
    window.notifications = notifications;  // Make globally available

    status = new StatusManager();
    window.status = status;

    chat = new ChatManager();
    window.chat = chat;

    // Initialize keyboard manager with all managers
    keyboard = new KeyboardManager(chat, status, notifications);
    window.keyboard = keyboard;

    // Initialize search manager
    search = new SearchManager(chat);
    window.search = search;

    // Initialize session manager
    sessions = new SessionManager(chat);
    window.sessions = sessions;

    // Initialize drafts manager
    drafts = new DraftsManager(chat, sessions);
    window.drafts = drafts;

    // Initialize prompt templates
    templates = new PromptTemplates(chat);
    window.templates = templates;

    // Initialize context menu
    contextMenu = new ContextMenu(chat);
    window.contextMenu = contextMenu;

    // Initialize metrics dashboard
    metrics = new MetricsDashboard();
    window.metrics = metrics;

    // Initialize logs viewer
    logs = new LogsViewer();
    window.logs = logs;

    // Initialize accessibility manager
    accessibility = new AccessibilityManager();
    window.accessibility = accessibility;

    // Initialize export/import
    exportImport = new ExportImport(chat, sessions);
    window.exportImport = exportImport;

    // Initialize performance manager
    performance = new PerformanceManager();
    window.performance = performance;

    // Initialize file attachments
    attachments = new FileAttachments(chat);
    window.attachments = attachments;

    // Initialize typing indicator
    typing = new TypingIndicator();
    window.typing = typing;

    // Initialize message reactions
    reactions = new MessageReactions(chat);
    window.reactions = reactions;

    // Make syntax highlighter available globally
    window.highlighter = highlighter;

    // Setup UI handlers
    setupSettingsModal();
    setupThemeToggle();
    setupConnectionStateListener();

    // Show ready notification
    notifications.show('success', 'Ready', 'Chat UI initialized successfully');

    console.log('Progenitor Chat UI ready');
}

/**
 * Setup listener for API connection state changes.
 */
function setupConnectionStateListener() {
    window.addEventListener('api-connection-change', (event) => {
        const { state, previousState } = event.detail;

        // Update connection indicator
        const indicator = document.getElementById('connection-status');
        if (indicator) {
            indicator.dataset.status = state === 'connected' ? 'connected' :
                                       state === 'connecting' ? 'connecting' :
                                       'disconnected';
            indicator.querySelector('.status-text').textContent =
                state === 'connected' ? 'Connected' :
                state === 'connecting' ? 'Connecting...' :
                state === 'error' ? 'Error' : 'Disconnected';
        }

        // Show notification on significant state changes
        if (previousState === 'connected' && state === 'error') {
            notifications?.show('error', 'Connection Lost',
                'Unable to reach the server. Will retry automatically.');
        } else if (previousState === 'error' && state === 'connected') {
            notifications?.show('success', 'Reconnected',
                'Connection to server restored.');
        }
    });
}

/**
 * Initialize theme based on settings.
 */
function initTheme() {
    const savedTheme = Config.theme.default;
    applyTheme(savedTheme);
}

/**
 * Apply a theme.
 * @param {string} theme - Theme name (system, dark, light)
 */
function applyTheme(theme) {
    const root = document.documentElement;

    if (theme === 'system') {
        // Use system preference
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        root.dataset.theme = prefersDark ? 'dark' : 'light';
    } else {
        root.dataset.theme = theme;
    }

    // Update button icon
    const themeBtn = document.getElementById('btn-theme');
    if (themeBtn) {
        themeBtn.textContent = root.dataset.theme === 'dark' ? '🌙' : '☀️';
    }
}

/**
 * Toggle between dark and light themes.
 */
function toggleTheme() {
    const root = document.documentElement;
    const current = root.dataset.theme;
    const newTheme = current === 'dark' ? 'light' : 'dark';

    applyTheme(newTheme);
    Config.setTheme(newTheme);
}

/**
 * Setup theme toggle button.
 */
function setupThemeToggle() {
    const themeBtn = document.getElementById('btn-theme');
    themeBtn?.addEventListener('click', toggleTheme);

    // Listen for system theme changes
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
        if (Config.theme.default === 'system') {
            applyTheme('system');
        }
    });
}

/**
 * Setup settings modal.
 */
async function setupSettingsModal() {
    const settingsBtn = document.getElementById('btn-settings');
    const settingsModal = document.getElementById('settings-modal');
    const settingsClose = document.getElementById('settings-close');
    const settingsSave = document.getElementById('settings-save');
    const backdrop = settingsModal?.querySelector('.modal-backdrop');

    // Open settings
    settingsBtn?.addEventListener('click', async () => {
        try {
            // Fetch current settings from server
            const response = await fetch('/api/ui/settings');
            if (response.ok) {
                const settings = await response.json();

                // Populate UI settings
                document.getElementById('setting-theme').value = settings.ui?.theme || 'system';
                document.getElementById('setting-notifications').checked = settings.ui?.notifications !== false;
                document.getElementById('setting-sounds').checked = settings.ui?.sounds || false;
                document.getElementById('setting-api-url').value = settings.ui?.api_url || 'http://localhost:8000';

                // Populate Progenitor settings
                document.getElementById('setting-timezone').value = settings.progenitor?.timezone || '';
                document.getElementById('setting-idle-backoff').value = settings.progenitor?.idle_backoff_seconds || '';
                document.getElementById('setting-max-backoff').value = settings.progenitor?.max_backoff_seconds || '';
                document.getElementById('setting-max-actions').value = settings.progenitor?.max_actions_per_cycle || '';
                document.getElementById('setting-checkpoint-interval').value = settings.progenitor?.checkpoint_interval_seconds || '';
                document.getElementById('setting-input-timeout').value = settings.progenitor?.input_timeout_seconds || '';
                document.getElementById('setting-log-level').value = settings.progenitor?.log_level || 'info';

                // Populate Environment settings
                document.getElementById('setting-mock-llm').checked = settings.environment?.mock_llm || false;
                document.getElementById('setting-azure-endpoint').value = settings.environment?.azure_openai_endpoint || '';
                document.getElementById('setting-azure-key').value = settings.environment?.azure_openai_api_key || '';
            }
        } catch (error) {
            console.warn('Failed to load settings from server, using defaults:', error);
        }

        // Show modal regardless of fetch success
        settingsModal?.classList.remove('hidden');
        settingsModal?.classList.add('visible');
    });

    // Close settings
    settingsClose?.addEventListener('click', () => {
        settingsModal?.classList.remove('visible');
        settingsModal?.classList.add('hidden');
    });

    backdrop?.addEventListener('click', () => {
        settingsModal?.classList.remove('visible');
        settingsModal?.classList.add('hidden');
    });

    // Save settings
    settingsSave?.addEventListener('click', async () => {
        try {
            const uiSettings = {
                theme: document.getElementById('setting-theme').value,
                notifications: document.getElementById('setting-notifications').checked,
                sounds: document.getElementById('setting-sounds').checked,
                api_url: document.getElementById('setting-api-url').value.trim()
            };

            const progenitorSettings = {
                timezone: document.getElementById('setting-timezone').value.trim(),
                idle_backoff_seconds: parseInt(document.getElementById('setting-idle-backoff').value) || 30,
                max_backoff_seconds: parseInt(document.getElementById('setting-max-backoff').value) || 300,
                max_actions_per_cycle: parseInt(document.getElementById('setting-max-actions').value) || 10,
                checkpoint_interval_seconds: parseInt(document.getElementById('setting-checkpoint-interval').value) || 30,
                input_timeout_seconds: parseInt(document.getElementById('setting-input-timeout').value) || 300,
                log_level: document.getElementById('setting-log-level').value
            };

            const environmentSettings = {
                mock_llm: document.getElementById('setting-mock-llm').checked,
                azure_openai_endpoint: document.getElementById('setting-azure-endpoint').value.trim(),
                azure_openai_api_key: document.getElementById('setting-azure-key').value.trim()
            };

            // Send to server
            const response = await fetch('/api/ui/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ui: uiSettings,
                    progenitor: progenitorSettings,
                    environment: environmentSettings
                })
            });

            if (response.ok) {
                // Apply UI settings locally
                Config.setTheme(uiSettings.theme);
                applyTheme(uiSettings.theme);
                Config.setNotifications(uiSettings.notifications);
                Config.setSounds(uiSettings.sounds);

                if (uiSettings.api_url && uiSettings.api_url !== Config.api.baseUrl) {
                    Config.setApiUrl(uiSettings.api_url);
                    // Reinitialize API
                    window.api = new ProgenitorAPI(Config);
                    // Refresh status
                    status.refreshStatus();
                }

                settingsModal?.classList.remove('visible');
                settingsModal?.classList.add('hidden');
                notifications.show('success', 'Settings Saved', 'Your preferences have been updated.');
            } else {
                throw new Error('Failed to save settings');
            }
        } catch (error) {
            console.error('Failed to save settings:', error);
            // Still close the modal but show error
            settingsModal?.classList.remove('visible');
            settingsModal?.classList.add('hidden');
            notifications.show('error', 'Settings Error', 'Failed to save settings to server.');
        }
    });
}

/**
 * Handle visibility changes (tab focus).
 */
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        // Refresh status when tab becomes visible
        status?.refreshStatus();
    }
});

/**
 * Handle online/offline status.
 */
window.addEventListener('online', () => {
    notifications?.show('success', 'Online', 'Connection restored');
    status?.refreshStatus();
});

window.addEventListener('offline', () => {
    notifications?.show('error', 'Offline', 'Network connection lost');
    status?.updateConnectionStatus(false);
});

/**
 * Handle unload - cleanup.
 */
window.addEventListener('beforeunload', () => {
    // Cancel any ongoing streams
    api?.cancelStream();

    // Stop polling
    status?.stopHealthCheck();
});

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
} else {
    initApp();
}
