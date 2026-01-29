/**
 * Progenitor Chat UI - Message Context Menu Module
 * 
 * Provides right-click context menu for messages with various actions.
 */

export class ContextMenu {
    constructor(chatManager) {
        this.chatManager = chatManager;
        this.activeMenu = null;
        
        this.init();
    }

    init() {
        this.createMenu();
        this.bindEvents();
    }

    /**
     * Create the context menu element.
     */
    createMenu() {
        const menu = document.createElement('div');
        menu.id = 'context-menu';
        menu.className = 'context-menu hidden';
        menu.innerHTML = `
            <div class="context-menu-item" data-action="copy">
                <span class="context-menu-icon">📋</span>
                <span class="context-menu-label">Copy Message</span>
                <span class="context-menu-shortcut">Ctrl+C</span>
            </div>
            <div class="context-menu-item" data-action="copy-code">
                <span class="context-menu-icon">💻</span>
                <span class="context-menu-label">Copy Code Block</span>
            </div>
            <div class="context-menu-divider"></div>
            <div class="context-menu-item" data-action="edit" data-role="user">
                <span class="context-menu-icon">✏️</span>
                <span class="context-menu-label">Edit Message</span>
            </div>
            <div class="context-menu-item" data-action="regenerate" data-role="assistant">
                <span class="context-menu-icon">🔄</span>
                <span class="context-menu-label">Regenerate Response</span>
            </div>
            <div class="context-menu-divider"></div>
            <div class="context-menu-item" data-action="select-all">
                <span class="context-menu-icon">📄</span>
                <span class="context-menu-label">Select All Text</span>
                <span class="context-menu-shortcut">Ctrl+A</span>
            </div>
            <div class="context-menu-item" data-action="quote">
                <span class="context-menu-icon">💬</span>
                <span class="context-menu-label">Quote in Reply</span>
            </div>
            <div class="context-menu-divider"></div>
            <div class="context-menu-item" data-action="delete" data-danger="true">
                <span class="context-menu-icon">🗑️</span>
                <span class="context-menu-label">Delete Message</span>
            </div>
        `;

        document.body.appendChild(menu);
        this.menu = menu;
    }

    /**
     * Bind event handlers.
     */
    bindEvents() {
        // Right-click on messages
        document.addEventListener('contextmenu', (e) => {
            const messageEl = e.target.closest('.message');
            if (messageEl) {
                e.preventDefault();
                this.show(e.clientX, e.clientY, messageEl);
            }
        });

        // Menu item clicks
        this.menu.addEventListener('click', (e) => {
            const item = e.target.closest('.context-menu-item');
            if (!item) return;

            const action = item.dataset.action;
            this.handleAction(action);
            this.hide();
        });

        // Hide on outside click
        document.addEventListener('click', () => {
            this.hide();
        });

        // Hide on escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.hide();
            }
        });

        // Hide on scroll
        document.addEventListener('scroll', () => {
            this.hide();
        }, true);
    }

    /**
     * Show context menu at position.
     * @param {number} x - X coordinate
     * @param {number} y - Y coordinate
     * @param {HTMLElement} messageEl - The message element
     */
    show(x, y, messageEl) {
        this.targetMessage = messageEl;
        this.targetMessageId = messageEl.dataset.messageId;
        this.targetRole = messageEl.classList.contains('user') ? 'user' : 'assistant';

        // Filter menu items based on message role
        this.menu.querySelectorAll('[data-role]').forEach(item => {
            const role = item.dataset.role;
            if (role && role !== this.targetRole) {
                item.classList.add('hidden');
            } else {
                item.classList.remove('hidden');
            }
        });

        // Check if there's a code block
        const hasCode = messageEl.querySelector('pre code') !== null;
        const copyCodeItem = this.menu.querySelector('[data-action="copy-code"]');
        if (copyCodeItem) {
            copyCodeItem.classList.toggle('hidden', !hasCode);
        }

        // Position menu
        const menuRect = this.menu.getBoundingClientRect();
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;

        // Temporarily show to get dimensions
        this.menu.classList.remove('hidden');
        const menuWidth = this.menu.offsetWidth;
        const menuHeight = this.menu.offsetHeight;

        // Adjust position to stay in viewport
        if (x + menuWidth > viewportWidth) {
            x = viewportWidth - menuWidth - 10;
        }
        if (y + menuHeight > viewportHeight) {
            y = viewportHeight - menuHeight - 10;
        }

        this.menu.style.left = `${x}px`;
        this.menu.style.top = `${y}px`;
    }

    /**
     * Hide context menu.
     */
    hide() {
        this.menu.classList.add('hidden');
        this.targetMessage = null;
        this.targetMessageId = null;
        this.targetRole = null;
    }

    /**
     * Handle menu actions.
     * @param {string} action - Action to perform
     */
    handleAction(action) {
        if (!this.targetMessage) return;

        switch (action) {
            case 'copy':
                this.copyMessage();
                break;
            case 'copy-code':
                this.copyCodeBlock();
                break;
            case 'edit':
                this.editMessage();
                break;
            case 'regenerate':
                this.regenerateResponse();
                break;
            case 'select-all':
                this.selectAllText();
                break;
            case 'quote':
                this.quoteInReply();
                break;
            case 'delete':
                this.deleteMessage();
                break;
        }
    }

    /**
     * Copy message content.
     */
    async copyMessage() {
        const content = this.targetMessage.querySelector('.message-content');
        if (!content) return;

        try {
            await navigator.clipboard.writeText(content.textContent);
            window.notifications?.show('success', 'Copied', 'Message copied to clipboard.');
        } catch (e) {
            // Fallback
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(content);
            selection.removeAllRanges();
            selection.addRange(range);
            document.execCommand('copy');
            selection.removeAllRanges();
            window.notifications?.show('success', 'Copied', 'Message copied to clipboard.');
        }
    }

    /**
     * Copy code block from message.
     */
    async copyCodeBlock() {
        const codeBlock = this.targetMessage.querySelector('pre code');
        if (!codeBlock) return;

        try {
            await navigator.clipboard.writeText(codeBlock.textContent);
            window.notifications?.show('success', 'Copied', 'Code copied to clipboard.');
        } catch (e) {
            console.error('Failed to copy code:', e);
        }
    }

    /**
     * Edit user message.
     */
    editMessage() {
        if (this.targetRole !== 'user') return;

        const content = this.targetMessage.querySelector('.message-content');
        if (!content) return;

        // Show edit mode
        const originalText = content.textContent;
        
        const editContainer = document.createElement('div');
        editContainer.className = 'message-edit-container';
        editContainer.innerHTML = `
            <textarea class="message-edit-input">${this.escapeHtml(originalText)}</textarea>
            <div class="message-edit-actions">
                <button class="btn-edit-cancel">Cancel</button>
                <button class="btn-edit-save">Save</button>
            </div>
        `;

        content.style.display = 'none';
        content.parentElement.insertBefore(editContainer, content.nextSibling);

        const textarea = editContainer.querySelector('.message-edit-input');
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);

        // Auto-resize
        textarea.style.height = 'auto';
        textarea.style.height = `${textarea.scrollHeight}px`;

        // Cancel edit
        editContainer.querySelector('.btn-edit-cancel').addEventListener('click', () => {
            editContainer.remove();
            content.style.display = '';
        });

        // Save edit
        editContainer.querySelector('.btn-edit-save').addEventListener('click', () => {
            const newText = textarea.value.trim();
            if (newText && newText !== originalText) {
                // Update message in chat manager
                if (this.chatManager && this.targetMessageId) {
                    const msgIndex = this.chatManager.messages.findIndex(m => m.id === this.targetMessageId);
                    if (msgIndex !== -1) {
                        this.chatManager.messages[msgIndex].content = newText;
                        this.chatManager.saveHistory();
                    }
                }
                content.innerHTML = this.chatManager?.formatMessage?.(newText) || newText;
            }
            editContainer.remove();
            content.style.display = '';
            window.notifications?.show('success', 'Saved', 'Message updated.');
        });

        // Handle Escape and Enter
        textarea.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                editContainer.remove();
                content.style.display = '';
            } else if (e.key === 'Enter' && e.ctrlKey) {
                editContainer.querySelector('.btn-edit-save').click();
            }
        });
    }

    /**
     * Regenerate assistant response.
     */
    regenerateResponse() {
        if (this.targetRole !== 'assistant' || !this.chatManager) return;
        
        this.chatManager.regenerateResponse?.(this.targetMessageId);
    }

    /**
     * Select all text in message.
     */
    selectAllText() {
        const content = this.targetMessage.querySelector('.message-content');
        if (!content) return;

        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(content);
        selection.removeAllRanges();
        selection.addRange(range);
    }

    /**
     * Quote message in reply.
     */
    quoteInReply() {
        const content = this.targetMessage.querySelector('.message-content');
        if (!content || !this.chatManager) return;

        const input = this.chatManager.inputElement;
        if (!input) return;

        const text = content.textContent.trim();
        const quoted = text.split('\n').map(line => `> ${line}`).join('\n');
        
        input.value = input.value + (input.value ? '\n\n' : '') + quoted + '\n\n';
        input.focus();
        input.dispatchEvent(new Event('input'));

        window.notifications?.show('info', 'Quoted', 'Message quoted in input.');
    }

    /**
     * Delete message.
     */
    deleteMessage() {
        if (!this.chatManager || !this.targetMessageId) return;

        // Confirm deletion
        if (!confirm('Delete this message?')) return;

        // Remove from messages array
        const msgIndex = this.chatManager.messages.findIndex(m => m.id === this.targetMessageId);
        if (msgIndex !== -1) {
            this.chatManager.messages.splice(msgIndex, 1);
            this.chatManager.saveHistory();
        }

        // Remove from DOM
        this.targetMessage.remove();

        window.notifications?.show('success', 'Deleted', 'Message removed.');
    }

    /**
     * Escape HTML.
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

export default ContextMenu;
