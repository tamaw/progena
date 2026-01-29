/**
 * Progenitor Chat UI - Drafts Module
 * 
 * Auto-saves draft messages to prevent data loss.
 */

export class DraftsManager {
    constructor(chatManager) {
        this.chatManager = chatManager;
        this.storageKey = 'progenitor_draft';
        this.autoSaveInterval = 1000; // 1 second
        this.lastSavedContent = '';
        this.saveTimer = null;
        
        this.init();
    }

    init() {
        const input = this.chatManager?.inputElement;
        if (!input) return;

        // Load existing draft
        this.loadDraft();

        // Setup auto-save on input
        input.addEventListener('input', () => this.scheduleSave());

        // Clear draft when message is sent
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                this.clearDraft();
            }
        });

        // Save draft when leaving page
        window.addEventListener('beforeunload', () => this.saveDraft());
        
        // Periodic save
        setInterval(() => this.saveDraft(), 5000);
    }

    /**
     * Schedule a draft save (debounced).
     */
    scheduleSave() {
        if (this.saveTimer) {
            clearTimeout(this.saveTimer);
        }
        
        this.saveTimer = setTimeout(() => {
            this.saveDraft();
        }, this.autoSaveInterval);
    }

    /**
     * Save the current draft.
     */
    saveDraft() {
        const input = this.chatManager?.inputElement;
        if (!input) return;

        const content = input.value;
        
        // Don't save if content hasn't changed
        if (content === this.lastSavedContent) return;

        if (content.trim()) {
            const draft = {
                content: content,
                sessionId: window.sessions?.currentSessionId || null,
                timestamp: Date.now()
            };
            localStorage.setItem(this.storageKey, JSON.stringify(draft));
            this.lastSavedContent = content;
        } else {
            this.clearDraft();
        }
    }

    /**
     * Load and restore a saved draft.
     */
    loadDraft() {
        try {
            const saved = localStorage.getItem(this.storageKey);
            if (!saved) return;

            const draft = JSON.parse(saved);
            
            // Check if draft is recent (within 24 hours)
            const age = Date.now() - draft.timestamp;
            if (age > 24 * 60 * 60 * 1000) {
                this.clearDraft();
                return;
            }

            // Restore draft
            const input = this.chatManager?.inputElement;
            if (input && draft.content) {
                input.value = draft.content;
                this.lastSavedContent = draft.content;
                
                // Trigger resize
                input.dispatchEvent(new Event('input'));
                
                // Show notification
                window.notifications?.show('info', 'Draft Restored', 
                    'Your unsent message has been restored.');
            }
        } catch (e) {
            console.warn('Failed to load draft:', e);
        }
    }

    /**
     * Clear the saved draft.
     */
    clearDraft() {
        localStorage.removeItem(this.storageKey);
        this.lastSavedContent = '';
    }

    /**
     * Check if there's a draft for a specific session.
     * @param {string} sessionId - Session ID
     * @returns {boolean}
     */
    hasDraftForSession(sessionId) {
        try {
            const saved = localStorage.getItem(this.storageKey);
            if (!saved) return false;
            
            const draft = JSON.parse(saved);
            return draft.sessionId === sessionId && draft.content.trim().length > 0;
        } catch {
            return false;
        }
    }
}

export default DraftsManager;
