/**
 * Progenitor Chat UI - Message Reactions Module
 * 
 * Provides emoji reactions for messages.
 */

export class MessageReactions {
    constructor(chatManager) {
        this.chatManager = chatManager;
        this.storageKey = 'progenitor_reactions';
        this.reactions = {};
        this.availableReactions = ['👍', '👎', '❤️', '😄', '🤔', '🎉', '🔥', '👀'];
        
        this.init();
    }

    init() {
        this.loadReactions();
        this.bindEvents();
    }

    /**
     * Load reactions from storage.
     */
    loadReactions() {
        try {
            const saved = localStorage.getItem(this.storageKey);
            if (saved) {
                this.reactions = JSON.parse(saved);
            }
        } catch (e) {
            console.error('Failed to load reactions:', e);
        }
    }

    /**
     * Save reactions to storage.
     */
    saveReactions() {
        try {
            localStorage.setItem(this.storageKey, JSON.stringify(this.reactions));
        } catch (e) {
            console.error('Failed to save reactions:', e);
        }
    }

    /**
     * Bind event handlers.
     */
    bindEvents() {
        // Add reaction button to messages
        document.addEventListener('click', (e) => {
            const addReactionBtn = e.target.closest('.add-reaction-btn');
            if (addReactionBtn) {
                const messageEl = addReactionBtn.closest('.message');
                if (messageEl) {
                    this.showReactionPicker(messageEl, addReactionBtn);
                }
            }

            // Toggle existing reaction
            const reactionBtn = e.target.closest('.reaction-btn');
            if (reactionBtn && !reactionBtn.classList.contains('add-reaction-btn')) {
                const messageEl = reactionBtn.closest('.message');
                const emoji = reactionBtn.dataset.emoji;
                if (messageEl && emoji) {
                    this.toggleReaction(messageEl.dataset.messageId, emoji);
                }
            }
        });

        // Close picker on outside click
        document.addEventListener('click', (e) => {
            const picker = document.querySelector('.reaction-picker');
            if (picker && !picker.contains(e.target) && !e.target.closest('.add-reaction-btn')) {
                picker.remove();
            }
        });
    }

    /**
     * Show reaction picker.
     * @param {HTMLElement} messageEl - The message element
     * @param {HTMLElement} button - The add reaction button
     */
    showReactionPicker(messageEl, button) {
        // Remove existing picker
        document.querySelector('.reaction-picker')?.remove();

        const picker = document.createElement('div');
        picker.className = 'reaction-picker';
        picker.innerHTML = `
            <div class="reaction-picker-content">
                ${this.availableReactions.map(emoji => `
                    <button class="reaction-option" data-emoji="${emoji}">${emoji}</button>
                `).join('')}
            </div>
        `;

        // Position near button
        const rect = button.getBoundingClientRect();
        picker.style.position = 'fixed';
        picker.style.top = `${rect.top - 50}px`;
        picker.style.left = `${rect.left}px`;

        document.body.appendChild(picker);

        // Bind picker clicks
        picker.querySelectorAll('.reaction-option').forEach(option => {
            option.addEventListener('click', () => {
                const emoji = option.dataset.emoji;
                this.addReaction(messageEl.dataset.messageId, emoji);
                picker.remove();
            });
        });
    }

    /**
     * Add a reaction to a message.
     * @param {string} messageId - Message ID
     * @param {string} emoji - Reaction emoji
     */
    addReaction(messageId, emoji) {
        if (!messageId) return;

        if (!this.reactions[messageId]) {
            this.reactions[messageId] = {};
        }

        if (!this.reactions[messageId][emoji]) {
            this.reactions[messageId][emoji] = { count: 0, userReacted: false };
        }

        if (!this.reactions[messageId][emoji].userReacted) {
            this.reactions[messageId][emoji].count++;
            this.reactions[messageId][emoji].userReacted = true;
            this.saveReactions();
            this.renderReactions(messageId);
        }
    }

    /**
     * Toggle a reaction on a message.
     * @param {string} messageId - Message ID
     * @param {string} emoji - Reaction emoji
     */
    toggleReaction(messageId, emoji) {
        if (!messageId || !this.reactions[messageId]?.[emoji]) return;

        const reaction = this.reactions[messageId][emoji];
        
        if (reaction.userReacted) {
            reaction.count--;
            reaction.userReacted = false;
            if (reaction.count <= 0) {
                delete this.reactions[messageId][emoji];
            }
        } else {
            reaction.count++;
            reaction.userReacted = true;
        }

        this.saveReactions();
        this.renderReactions(messageId);
    }

    /**
     * Get reactions for a message.
     * @param {string} messageId - Message ID
     * @returns {Object} Reactions object
     */
    getReactions(messageId) {
        return this.reactions[messageId] || {};
    }

    /**
     * Render reactions for a message.
     * @param {string} messageId - Message ID
     */
    renderReactions(messageId) {
        const messageEl = document.querySelector(`.message[data-message-id="${messageId}"]`);
        if (!messageEl) return;

        let reactionsContainer = messageEl.querySelector('.message-reactions');
        
        // Create container if it doesn't exist
        if (!reactionsContainer) {
            reactionsContainer = document.createElement('div');
            reactionsContainer.className = 'message-reactions';
            messageEl.appendChild(reactionsContainer);
        }

        const reactions = this.getReactions(messageId);
        const reactionEntries = Object.entries(reactions).filter(([_, data]) => data.count > 0);

        if (reactionEntries.length === 0) {
            reactionsContainer.innerHTML = `
                <button class="reaction-btn add-reaction-btn" title="Add reaction">+</button>
            `;
            return;
        }

        reactionsContainer.innerHTML = `
            ${reactionEntries.map(([emoji, data]) => `
                <button class="reaction-btn ${data.userReacted ? 'active' : ''}" data-emoji="${emoji}">
                    <span class="reaction-emoji">${emoji}</span>
                    <span class="reaction-count">${data.count}</span>
                </button>
            `).join('')}
            <button class="reaction-btn add-reaction-btn" title="Add reaction">+</button>
        `;
    }

    /**
     * Initialize reactions for all visible messages.
     */
    initAllReactions() {
        document.querySelectorAll('.message[data-message-id]').forEach(messageEl => {
            const messageId = messageEl.dataset.messageId;
            this.renderReactions(messageId);
        });
    }

    /**
     * Create reaction buttons HTML for a message.
     * @param {string} messageId - Message ID
     * @returns {string} HTML string
     */
    createReactionButtonsHtml(messageId) {
        const reactions = this.getReactions(messageId);
        const reactionEntries = Object.entries(reactions).filter(([_, data]) => data.count > 0);

        return `
            <div class="message-reactions">
                ${reactionEntries.map(([emoji, data]) => `
                    <button class="reaction-btn ${data.userReacted ? 'active' : ''}" data-emoji="${emoji}">
                        <span class="reaction-emoji">${emoji}</span>
                        <span class="reaction-count">${data.count}</span>
                    </button>
                `).join('')}
                <button class="reaction-btn add-reaction-btn" title="Add reaction">+</button>
            </div>
        `;
    }
}

// Add CSS for reactions
const style = document.createElement('style');
style.textContent = `
    /* Reaction Picker */
    .reaction-picker {
        z-index: 1000;
        animation: fadeIn 0.15s ease;
    }

    .reaction-picker-content {
        display: flex;
        gap: 4px;
        padding: var(--spacing-sm);
        background-color: var(--color-bg-secondary);
        border: 1px solid var(--color-border);
        border-radius: var(--border-radius-lg);
        box-shadow: var(--shadow-lg);
    }

    .reaction-option {
        width: 32px;
        height: 32px;
        padding: 0;
        background: none;
        border: none;
        border-radius: var(--border-radius-sm);
        font-size: 18px;
        cursor: pointer;
        transition: all var(--transition-fast);
    }

    .reaction-option:hover {
        background-color: var(--color-bg-hover);
        transform: scale(1.2);
    }

    /* Message Reactions */
    .message-reactions {
        display: flex;
        gap: var(--spacing-xs);
        margin-top: var(--spacing-xs);
        flex-wrap: wrap;
    }

    .reaction-btn {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 2px 8px;
        border: 1px solid var(--color-border);
        border-radius: var(--border-radius-lg);
        background-color: transparent;
        font-size: var(--font-size-sm);
        cursor: pointer;
        transition: all var(--transition-fast);
    }

    .reaction-btn:hover {
        background-color: var(--color-bg-hover);
        border-color: var(--color-accent);
    }

    .reaction-btn.active {
        background-color: rgba(88, 166, 255, 0.2);
        border-color: var(--color-accent);
    }

    .add-reaction-btn {
        font-size: 14px;
        color: var(--color-text-muted);
        opacity: 0;
        transition: opacity var(--transition-fast);
    }

    .message:hover .add-reaction-btn {
        opacity: 1;
    }

    .reaction-emoji {
        font-size: 14px;
    }

    .reaction-count {
        font-size: var(--font-size-xs);
        color: var(--color-text-secondary);
    }
`;
document.head.appendChild(style);

export default MessageReactions;
