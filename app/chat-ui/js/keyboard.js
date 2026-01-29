/**
 * Keyboard Shortcuts Manager for Progenitor Chat UI
 * Handles global keyboard shortcuts for improved UX
 */

export class KeyboardManager {
    constructor(chatManager, statusManager, notificationManager) {
        this.chatManager = chatManager;
        this.statusManager = statusManager;
        this.notificationManager = notificationManager;
        this.isHelpVisible = false;
        this.shortcuts = new Map();

        this.registerDefaultShortcuts();
        this.init();
    }

    init() {
        document.addEventListener('keydown', (e) => this.handleKeydown(e));
        this.createHelpModal();
    }

    registerDefaultShortcuts() {
        // Chat shortcuts
        this.register('Enter', { ctrl: false, shift: false }, 'Send message', () => {
            // Only handle if input is focused
            const input = document.querySelector('.chat-input');
            if (document.activeElement === input && !input.disabled) {
                const sendBtn = document.getElementById('sendBtn');
                if (sendBtn && !sendBtn.disabled) {
                    sendBtn.click();
                }
            }
        }, false); // Don't prevent default for Enter

        this.register('Enter', { ctrl: true }, 'Send message (alternative)', () => {
            const sendBtn = document.getElementById('sendBtn');
            if (sendBtn && !sendBtn.disabled) {
                sendBtn.click();
            }
        });

        this.register('Escape', {}, 'Stop generation / Close modal', () => {
            // First check for open modals
            const settingsModal = document.getElementById('settingsModal');
            const helpModal = document.getElementById('keyboardHelpModal');

            if (settingsModal?.classList.contains('visible')) {
                settingsModal.classList.remove('visible');
                return;
            }

            if (helpModal?.classList.contains('visible')) {
                helpModal.classList.remove('visible');
                this.isHelpVisible = false;
                return;
            }

            // Otherwise stop generation
            const stopBtn = document.getElementById('stopBtn');
            if (stopBtn && !stopBtn.classList.contains('hidden')) {
                stopBtn.click();
            }
        });

        this.register('/', { ctrl: true }, 'Focus chat input', () => {
            const input = document.querySelector('.chat-input');
            if (input) {
                input.focus();
            }
        });

        this.register('n', { ctrl: true }, 'New conversation', () => {
            if (this.chatManager) {
                this.chatManager.clearHistory();
            }
        });

        // Navigation shortcuts
        this.register('ArrowUp', { alt: true }, 'Previous message in history', () => {
            // Navigate through sent message history
            if (this.chatManager?.messageHistory) {
                this.navigateHistory(-1);
            }
        });

        this.register('ArrowDown', { alt: true }, 'Next message in history', () => {
            if (this.chatManager?.messageHistory) {
                this.navigateHistory(1);
            }
        });

        // UI shortcuts
        this.register(',', { ctrl: true }, 'Open settings', () => {
            const settingsModal = document.getElementById('settingsModal');
            if (settingsModal) {
                settingsModal.classList.toggle('visible');
            }
        });

        this.register('b', { ctrl: true }, 'Toggle sidebar', () => {
            const sidebar = document.querySelector('.sidebar');
            if (sidebar) {
                sidebar.classList.toggle('collapsed');
            }
        });

        this.register('d', { ctrl: true, shift: true }, 'Toggle dark/light theme', () => {
            const html = document.documentElement;
            const currentTheme = html.getAttribute('data-theme') || 'dark';
            html.setAttribute('data-theme', currentTheme === 'dark' ? 'light' : 'dark');
            localStorage.setItem('theme', currentTheme === 'dark' ? 'light' : 'dark');
        });

        // Help
        this.register('?', { shift: true }, 'Show keyboard shortcuts', () => {
            this.toggleHelp();
        });

        this.register('F1', {}, 'Show keyboard shortcuts', () => {
            this.toggleHelp();
        });

        // Status panel shortcuts
        this.register('s', { ctrl: true, shift: true }, 'Toggle status panel', () => {
            const statusPanel = document.querySelector('.status-panel');
            if (statusPanel) {
                statusPanel.classList.toggle('minimized');
            }
        });

        this.register('r', { ctrl: true, shift: true }, 'Refresh status', () => {
            if (this.statusManager) {
                this.statusManager.fetchStatus();
            }
        });

        // Notifications
        this.register('Delete', { ctrl: true }, 'Dismiss all notifications', () => {
            if (this.notificationManager) {
                this.notificationManager.dismissAll();
            }
        });

        // Export
        this.register('e', { ctrl: true, shift: true }, 'Export conversation', () => {
            this.exportConversation();
        });
    }

    register(key, modifiers = {}, description = '', handler, preventDefault = true) {
        const id = this.getShortcutId(key, modifiers);
        this.shortcuts.set(id, {
            key,
            modifiers: {
                ctrl: modifiers.ctrl || false,
                shift: modifiers.shift || false,
                alt: modifiers.alt || false,
                meta: modifiers.meta || false
            },
            description,
            handler,
            preventDefault
        });
    }

    getShortcutId(key, modifiers = {}) {
        const parts = [];
        if (modifiers.ctrl) parts.push('Ctrl');
        if (modifiers.shift) parts.push('Shift');
        if (modifiers.alt) parts.push('Alt');
        if (modifiers.meta) parts.push('Meta');
        parts.push(key);
        return parts.join('+');
    }

    handleKeydown(event) {
        const id = this.getShortcutId(event.key, {
            ctrl: event.ctrlKey,
            shift: event.shiftKey,
            alt: event.altKey,
            meta: event.metaKey
        });

        const shortcut = this.shortcuts.get(id);

        if (shortcut) {
            // Don't trigger shortcuts when typing in input unless explicitly allowed
            const isInputFocused = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName);

            // Allow certain shortcuts even when input is focused
            const allowedInInput = ['Escape', 'Enter', 'Ctrl+Enter', 'Ctrl+/', 'F1', 'Shift+?'];

            if (isInputFocused && !allowedInInput.includes(id)) {
                return;
            }

            if (shortcut.preventDefault) {
                event.preventDefault();
            }

            shortcut.handler(event);
        }
    }

    navigateHistory(direction) {
        const input = document.querySelector('.chat-input');
        if (!input || !this.chatManager) return;

        const history = this.chatManager.inputHistory || [];
        if (history.length === 0) return;

        const currentIndex = this.chatManager.historyIndex ?? history.length;
        const newIndex = Math.max(0, Math.min(history.length, currentIndex + direction));

        if (newIndex !== currentIndex) {
            this.chatManager.historyIndex = newIndex;
            if (newIndex < history.length) {
                input.value = history[newIndex];
            } else {
                input.value = this.chatManager.currentDraft || '';
            }
        }
    }

    exportConversation() {
        const messages = document.querySelectorAll('.message');
        if (messages.length === 0) {
            this.notificationManager?.show({
                title: 'Export',
                message: 'No messages to export',
                type: 'info'
            });
            return;
        }

        let exportText = `# Progenitor Chat Export\n`;
        exportText += `Date: ${new Date().toISOString()}\n\n`;
        exportText += `---\n\n`;

        messages.forEach(msg => {
            const isUser = msg.classList.contains('user');
            const role = isUser ? 'User' : 'Assistant';
            const content = msg.querySelector('.message-content')?.textContent || '';
            const time = msg.querySelector('.message-time')?.textContent || '';

            exportText += `## ${role} (${time})\n\n`;
            exportText += `${content}\n\n`;
            exportText += `---\n\n`;
        });

        // Create download
        const blob = new Blob([exportText], { type: 'text/markdown' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `progenitor-chat-${new Date().toISOString().slice(0, 10)}.md`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        this.notificationManager?.show({
            title: 'Export Complete',
            message: 'Conversation exported as Markdown',
            type: 'success'
        });
    }

    createHelpModal() {
        const modal = document.createElement('div');
        modal.id = 'keyboardHelpModal';
        modal.className = 'modal hidden';
        modal.innerHTML = `
            <div class="modal-backdrop"></div>
            <div class="modal-content keyboard-help">
                <div class="modal-header">
                    <h2>Keyboard Shortcuts</h2>
                    <button class="modal-close" aria-label="Close">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="shortcuts-grid">
                        ${this.renderShortcutsHTML()}
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Close button handler
        modal.querySelector('.modal-close').addEventListener('click', (e) => {
            e.stopPropagation();
            modal.classList.remove('visible');
            modal.classList.add('hidden');
            this.isHelpVisible = false;
        });

        // Click outside to close (on the modal background, not the content)
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.classList.remove('visible');
                modal.classList.add('hidden');
                this.isHelpVisible = false;
            }
        });
    }

    renderShortcutsHTML() {
        const categories = {
            'Chat': [],
            'Navigation': [],
            'UI': [],
            'Other': []
        };

        this.shortcuts.forEach((shortcut, id) => {
            const item = { id, ...shortcut };

            if (id.includes('Enter') || id.includes('Escape') || id.includes('Ctrl+/') || id.includes('Ctrl+n')) {
                categories['Chat'].push(item);
            } else if (id.includes('Arrow') || id.includes('history')) {
                categories['Navigation'].push(item);
            } else if (id.includes(',') || id.includes('b') || id.includes('d') || id.includes('s') || id.includes('r')) {
                categories['UI'].push(item);
            } else {
                categories['Other'].push(item);
            }
        });

        let html = '';

        for (const [category, shortcuts] of Object.entries(categories)) {
            if (shortcuts.length === 0) continue;

            html += `<div class="shortcut-category">
                <h3>${category}</h3>
                <div class="shortcut-list">`;

            shortcuts.forEach(shortcut => {
                html += `
                    <div class="shortcut-item">
                        <kbd>${this.formatShortcutKey(shortcut.id)}</kbd>
                        <span>${shortcut.description}</span>
                    </div>`;
            });

            html += `</div></div>`;
        }

        return html;
    }

    formatShortcutKey(id) {
        return id
            .replace('Control', 'Ctrl')
            .replace('+', ' + ')
            .replace('ArrowUp', '↑')
            .replace('ArrowDown', '↓')
            .replace('ArrowLeft', '←')
            .replace('ArrowRight', '→')
            .replace('Escape', 'Esc')
            .replace('Delete', 'Del');
    }

    toggleHelp() {
        const modal = document.getElementById('keyboardHelpModal');
        if (modal) {
            this.isHelpVisible = !this.isHelpVisible;
            if (this.isHelpVisible) {
                modal.classList.remove('hidden');
                modal.classList.add('visible');
            } else {
                modal.classList.remove('visible');
                modal.classList.add('hidden');
            }
        }
    }
}

export default KeyboardManager;
