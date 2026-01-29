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
function setupSettingsModal() {
    const settingsBtn = document.getElementById('btn-settings');
    const settingsModal = document.getElementById('settings-modal');
    const settingsClose = document.getElementById('settings-close');
    const settingsSave = document.getElementById('settings-save');
    const backdrop = settingsModal?.querySelector('.modal-backdrop');

    // Open settings
    settingsBtn?.addEventListener('click', () => {
        // Populate current values
        document.getElementById('setting-theme').value = Config.theme.default;
        document.getElementById('setting-notifications').checked = Config.notifications.enabled;
        document.getElementById('setting-sounds').checked = Config.notifications.soundEnabled;
        document.getElementById('setting-api-url').value = Config.api.baseUrl;

        settingsModal?.classList.remove('hidden');
    });

    // Close settings
    settingsClose?.addEventListener('click', () => {
        settingsModal?.classList.add('hidden');
    });

    backdrop?.addEventListener('click', () => {
        settingsModal?.classList.add('hidden');
    });

    // Save settings
    settingsSave?.addEventListener('click', () => {
        const theme = document.getElementById('setting-theme').value;
        const notificationsEnabled = document.getElementById('setting-notifications').checked;
        const soundsEnabled = document.getElementById('setting-sounds').checked;
        const apiUrl = document.getElementById('setting-api-url').value.trim();

        // Apply settings
        Config.setTheme(theme);
        applyTheme(theme);

        Config.setNotifications(notificationsEnabled);
        Config.setSounds(soundsEnabled);

        if (apiUrl && apiUrl !== Config.api.baseUrl) {
            Config.setApiUrl(apiUrl);
            // Reinitialize API
            window.api = new ProgenitorAPI(Config);
            // Refresh status
            status.refreshStatus();
        }

        settingsModal?.classList.add('hidden');
        notifications.show('success', 'Settings Saved', 'Your preferences have been updated.');
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
