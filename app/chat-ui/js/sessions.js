/**
 * Progenitor Chat UI - Sessions Module
 *
 * Handles multiple conversation sessions with persistence.
 */

export class SessionManager {
    constructor(chatManager) {
        this.chatManager = chatManager;
        this.sessions = [];
        this.currentSessionId = null;
        this.storageKey = 'progenitor_sessions';
        this.maxSessions = 50;

        this.init();
    }

    init() {
        this.loadSessions();
        this.createSessionUI();
        this.bindEvents();

        // Create default session if none exists
        if (this.sessions.length === 0) {
            this.createSession('New Chat');
        } else {
            // Load most recent session
            this.switchSession(this.sessions[0].id);
        }
    }

    /**
     * Load sessions from localStorage.
     */
    loadSessions() {
        try {
            const saved = localStorage.getItem(this.storageKey);
            if (saved) {
                this.sessions = JSON.parse(saved);
            }
        } catch (e) {
            console.warn('Failed to load sessions:', e);
            this.sessions = [];
        }
    }

    /**
     * Save sessions to localStorage.
     */
    saveSessions() {
        try {
            // Keep only max sessions
            if (this.sessions.length > this.maxSessions) {
                this.sessions = this.sessions.slice(0, this.maxSessions);
            }
            localStorage.setItem(this.storageKey, JSON.stringify(this.sessions));
        } catch (e) {
            console.warn('Failed to save sessions:', e);
        }
    }

    /**
     * Create the session management UI.
     */
    createSessionUI() {
        const sidebar = document.querySelector('.sidebar');
        if (!sidebar) return;

        // Create session list container
        const sessionContainer = document.createElement('div');
        sessionContainer.className = 'session-container';
        sessionContainer.innerHTML = `
            <div class="session-header">
                <h3>Conversations</h3>
                <button class="btn-new-session" title="New Chat (Ctrl+N)">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="12" y1="5" x2="12" y2="19"></line>
                        <line x1="5" y1="12" x2="19" y2="12"></line>
                    </svg>
                </button>
            </div>
            <div class="session-list" id="session-list"></div>
        `;

        // Insert at top of sidebar
        const firstChild = sidebar.firstChild;
        if (firstChild) {
            sidebar.insertBefore(sessionContainer, firstChild);
        } else {
            sidebar.appendChild(sessionContainer);
        }

        this.sessionList = document.getElementById('session-list');
        this.renderSessions();
    }

    /**
     * Bind event handlers.
     */
    bindEvents() {
        // New session button
        const newBtn = document.querySelector('.btn-new-session');
        newBtn?.addEventListener('click', () => this.createSession());

        // Session list click delegation
        this.sessionList?.addEventListener('click', (e) => {
            const sessionItem = e.target.closest('.session-item');
            if (!sessionItem) return;

            const sessionId = sessionItem.dataset.sessionId;

            // Delete button
            if (e.target.closest('.session-delete')) {
                e.stopPropagation();
                this.deleteSession(sessionId);
                return;
            }

            // Switch session
            this.switchSession(sessionId);
        });

        // Double-click to rename
        this.sessionList?.addEventListener('dblclick', (e) => {
            const sessionItem = e.target.closest('.session-item');
            if (sessionItem && !e.target.closest('.session-delete')) {
                this.renameSession(sessionItem.dataset.sessionId);
            }
        });
    }

    /**
     * Render the session list.
     */
    renderSessions() {
        if (!this.sessionList) return;

        this.sessionList.innerHTML = this.sessions.map(session => `
            <div class="session-item ${session.id === this.currentSessionId ? 'active' : ''}"
                 data-session-id="${session.id}">
                <div class="session-info">
                    <span class="session-title">${this.escapeHTML(session.title)}</span>
                    <span class="session-meta">${this.formatDate(session.updatedAt)}</span>
                </div>
                <button class="session-delete" title="Delete">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="18" y1="6" x2="6" y2="18"></line>
                        <line x1="6" y1="6" x2="18" y2="18"></line>
                    </svg>
                </button>
            </div>
        `).join('');
    }

    /**
     * Create a new session.
     * @param {string} title - Optional title
     * @returns {object} The new session
     */
    createSession(title = null) {
        // Save current session first
        this.saveCurrentSession();

        const session = {
            id: this.generateId(),
            title: title || this.generateTitle(),
            messages: [],
            createdAt: Date.now(),
            updatedAt: Date.now()
        };

        // Add to beginning
        this.sessions.unshift(session);
        this.saveSessions();

        // Switch to new session
        this.currentSessionId = session.id;
        this.chatManager?.clearHistory();
        this.renderSessions();

        return session;
    }

    /**
     * Switch to a different session.
     * @param {string} sessionId - Session ID to switch to
     */
    switchSession(sessionId) {
        if (sessionId === this.currentSessionId) return;

        // Save current session
        this.saveCurrentSession();

        // Load new session
        const session = this.sessions.find(s => s.id === sessionId);
        if (!session) return;

        this.currentSessionId = sessionId;

        // Load messages into chat
        if (this.chatManager) {
            this.chatManager.messages = [...session.messages];
            this.chatManager.renderAllMessages();
        }

        // Move to top (most recent)
        this.sessions = this.sessions.filter(s => s.id !== sessionId);
        this.sessions.unshift(session);
        this.saveSessions();

        this.renderSessions();
    }

    /**
     * Save the current session.
     */
    saveCurrentSession() {
        if (!this.currentSessionId || !this.chatManager) return;

        const session = this.sessions.find(s => s.id === this.currentSessionId);
        if (!session) return;

        session.messages = [...this.chatManager.messages];
        session.updatedAt = Date.now();

        // Auto-generate title from first user message
        if (session.messages.length > 0 && session.title.startsWith('New Chat')) {
            const firstUserMsg = session.messages.find(m => m.role === 'user');
            if (firstUserMsg) {
                session.title = this.truncate(firstUserMsg.content, 30);
            }
        }

        this.saveSessions();
        this.renderSessions();
    }

    /**
     * Delete a session.
     * @param {string} sessionId - Session ID to delete
     */
    deleteSession(sessionId) {
        if (this.sessions.length <= 1) {
            window.notifications?.show('warning', 'Cannot Delete', 'You must have at least one conversation.');
            return;
        }

        this.sessions = this.sessions.filter(s => s.id !== sessionId);
        this.saveSessions();

        // If deleting current session, switch to another
        if (sessionId === this.currentSessionId) {
            this.currentSessionId = null;
            this.switchSession(this.sessions[0].id);
        }

        this.renderSessions();
    }

    /**
     * Rename a session.
     * @param {string} sessionId - Session ID to rename
     */
    renameSession(sessionId) {
        const session = this.sessions.find(s => s.id === sessionId);
        if (!session) return;

        const sessionItem = this.sessionList.querySelector(`[data-session-id="${sessionId}"]`);
        const titleEl = sessionItem?.querySelector('.session-title');
        if (!titleEl) return;

        // Create inline editor
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'session-title-input';
        input.value = session.title;

        const finishEdit = () => {
            const newTitle = input.value.trim() || session.title;
            session.title = newTitle;
            this.saveSessions();
            this.renderSessions();
        };

        input.addEventListener('blur', finishEdit);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                finishEdit();
            } else if (e.key === 'Escape') {
                this.renderSessions();
            }
        });

        titleEl.replaceWith(input);
        input.focus();
        input.select();
    }

    /**
     * Get current session.
     * @returns {object|null} Current session
     */
    getCurrentSession() {
        return this.sessions.find(s => s.id === this.currentSessionId) || null;
    }

    /**
     * Export a session as JSON.
     * @param {string} sessionId - Session ID to export
     * @returns {string} JSON string
     */
    exportSession(sessionId = null) {
        const session = sessionId
            ? this.sessions.find(s => s.id === sessionId)
            : this.getCurrentSession();

        if (!session) return null;

        return JSON.stringify(session, null, 2);
    }

    /**
     * Import a session from JSON.
     * @param {string} json - JSON string
     */
    importSession(json) {
        try {
            const session = JSON.parse(json);
            session.id = this.generateId();
            session.createdAt = Date.now();
            session.updatedAt = Date.now();

            this.sessions.unshift(session);
            this.saveSessions();
            this.switchSession(session.id);

            window.notifications?.show('success', 'Imported', 'Session imported successfully.');
        } catch (e) {
            window.notifications?.show('error', 'Import Failed', 'Invalid session data.');
        }
    }

    /**
     * Generate a unique ID.
     * @returns {string} Unique ID
     */
    generateId() {
        return `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    }

    /**
     * Generate a default title.
     * @returns {string} Default title
     */
    generateTitle() {
        const count = this.sessions.filter(s => s.title.startsWith('New Chat')).length;
        return count > 0 ? `New Chat ${count + 1}` : 'New Chat';
    }

    /**
     * Format a timestamp for display.
     * @param {number} timestamp - Unix timestamp
     * @returns {string} Formatted date
     */
    formatDate(timestamp) {
        const date = new Date(timestamp);
        const now = new Date();
        const diff = now - date;

        if (diff < 60000) return 'Just now';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
        if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;

        return date.toLocaleDateString();
    }

    /**
     * Truncate text.
     * @param {string} text - Text to truncate
     * @param {number} maxLength - Maximum length
     * @returns {string} Truncated text
     */
    truncate(text, maxLength) {
        if (text.length <= maxLength) return text;
        return text.substring(0, maxLength).trim() + '...';
    }

    /**
     * Escape HTML.
     * @param {string} text - Text to escape
     * @returns {string} Escaped text
     */
    escapeHTML(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

export default SessionManager;
