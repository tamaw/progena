/**
 * Progenitor Chat UI - Chat Module
 *
 * Handles chat functionality including message rendering,
 * streaming, and user interactions.
 */

class ChatManager {
    constructor() {
        this.messagesContainer = document.getElementById('chat-messages');
        this.inputElement = document.getElementById('chat-input');
        this.sendButton = document.getElementById('btn-send');
        this.stopButton = document.getElementById('btn-stop');
        this.typingIndicator = document.getElementById('typing-indicator');

        this.messages = [];
        this.isGenerating = false;
        this.currentStreamMessage = null;

        // Input history for navigation
        this.inputHistory = [];
        this.historyIndex = 0;
        this.currentDraft = '';
        this.maxHistoryItems = 50;

        this.init();
    }

    /**
     * Initialize chat functionality.
     */
    init() {
        // Bind event listeners
        this.sendButton.addEventListener('click', () => this.sendMessage());
        this.stopButton.addEventListener('click', () => this.stopGeneration());

        // Input handling
        this.inputElement.addEventListener('keydown', (e) => this.handleKeyDown(e));
        this.inputElement.addEventListener('input', () => this.autoResizeInput());

        // Quick actions
        document.querySelectorAll('.quick-action').forEach(btn => {
            btn.addEventListener('click', () => {
                const message = btn.dataset.message;
                if (message) {
                    this.inputElement.value = message;
                    this.sendMessage();
                }
            });
        });

        // Load history from localStorage
        this.loadHistory();

        // Focus input
        this.inputElement.focus();
    }

    /**
     * Handle keyboard events in the input.
     * @param {KeyboardEvent} e - Keyboard event
     */
    handleKeyDown(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            this.sendMessage();
        } else if (e.key === 'Escape') {
            if (this.isGenerating) {
                this.stopGeneration();
            }
        }
    }

    /**
     * Auto-resize the input textarea.
     */
    autoResizeInput() {
        const input = this.inputElement;
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 200) + 'px';
    }

    /**
     * Send a message to the agent.
     */
    async sendMessage() {
        const message = this.inputElement.value.trim();

        if (!message || this.isGenerating) {
            return;
        }

        // Add to input history
        if (message !== this.inputHistory[this.inputHistory.length - 1]) {
            this.inputHistory.push(message);
            if (this.inputHistory.length > this.maxHistoryItems) {
                this.inputHistory.shift();
            }
        }
        this.historyIndex = this.inputHistory.length;
        this.currentDraft = '';

        // Clear input
        this.inputElement.value = '';
        this.autoResizeInput();

        // Hide welcome message if visible
        const welcome = this.messagesContainer.querySelector('.welcome-message');
        if (welcome) {
            welcome.remove();
        }

        // Add user message
        this.addMessage('user', message);

        // Show typing indicator
        this.setGenerating(true);

        // Get conversation history for context
        const history = this.messages.slice(-10).map(m => ({
            role: m.role,
            content: m.content
        }));

        // Create assistant message placeholder
        const assistantMsg = this.addMessage('assistant', '', true);

        try {
            if (Config.chat.streamingEnabled) {
                await this.streamResponse(message, history, assistantMsg);
            } else {
                await this.regularResponse(message, history, assistantMsg);
            }
        } catch (error) {
            this.handleError(error, assistantMsg);
        } finally {
            this.setGenerating(false);
        }
    }

    /**
     * Stream response from the API.
     * @param {string} message - User message
     * @param {Array} history - Conversation history
     * @param {HTMLElement} messageEl - Message element to update
     */
    async streamResponse(message, history, messageEl) {
        const contentEl = messageEl.querySelector('.message-content');
        let fullContent = '';
        let tokenCount = 0;

        // Switch to generating state once we start receiving tokens
        let hasStartedGenerating = false;

        await api.sendMessageStream(
            message,
            history,
            // On chunk
            (chunk) => {
                if (!hasStartedGenerating) {
                    hasStartedGenerating = true;
                    this.setTypingState('generating');
                }

                fullContent += chunk;
                tokenCount++;

                // Update token counter periodically (every 5 tokens for performance)
                if (tokenCount % 5 === 0) {
                    this.updateTokenCounter(tokenCount);
                }

                contentEl.innerHTML = this.formatMessage(fullContent);
                this.scrollToBottom();
            },
            // On complete
            (status) => {
                messageEl.classList.remove('streaming');

                // Final token count update
                this.updateTokenCounter(tokenCount);

                if (status === 'cancelled') {
                    fullContent += ' [Cancelled]';
                }

                contentEl.innerHTML = this.formatMessage(fullContent);
                this.messages.push({
                    role: 'assistant',
                    content: fullContent,
                    timestamp: Date.now()
                });
                this.saveHistory();
                this.scrollToBottom();
            },
            // On error
            (error) => {
                this.handleError(error, messageEl);
            }
        );
    }

    /**
     * Regular (non-streaming) response.
     * @param {string} message - User message
     * @param {Array} history - Conversation history
     * @param {HTMLElement} messageEl - Message element to update
     */
    async regularResponse(message, history, messageEl) {
        const response = await api.sendMessage(message, history);
        const content = response.choices?.[0]?.message?.content || response.response || '';

        const contentEl = messageEl.querySelector('.message-content');
        contentEl.innerHTML = this.formatMessage(content);
        messageEl.classList.remove('streaming');

        this.messages.push({
            role: 'assistant',
            content: content,
            timestamp: Date.now()
        });
        this.saveHistory();
        this.scrollToBottom();
    }

    /**
     * Handle API errors.
     * @param {Error} error - Error object
     * @param {HTMLElement} messageEl - Message element
     */
    handleError(error, messageEl) {
        console.error('Chat error:', error);

        const contentEl = messageEl.querySelector('.message-content');
        messageEl.classList.remove('streaming');
        messageEl.classList.add('error');

        contentEl.innerHTML = `
            <div class="message-error">
                <span class="message-error-icon">⚠️</span>
                <div class="message-error-content">
                    <strong>Error:</strong> ${this.escapeHtml(error.message)}
                </div>
            </div>
        `;

        // Show notification
        if (window.notifications) {
            notifications.show('error', 'Chat Error', error.message);
        }
    }

    /**
     * Add a message to the chat.
     * @param {string} role - Message role (user/assistant)
     * @param {string} content - Message content
     * @param {boolean} isStreaming - Whether this is a streaming message
     * @returns {HTMLElement} - Message element
     */
    addMessage(role, content, isStreaming = false) {
        const messageId = `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

        const wrapper = document.createElement('div');
        wrapper.className = `message-wrapper ${role}`;
        wrapper.dataset.messageId = messageId;

        const message = document.createElement('div');
        message.className = `message ${role}${isStreaming ? ' streaming' : ''}`;

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        contentDiv.innerHTML = content ? this.formatMessage(content) : '';

        const timestamp = document.createElement('div');
        timestamp.className = 'message-timestamp';
        timestamp.textContent = this.formatTime(new Date());

        message.appendChild(contentDiv);
        message.appendChild(timestamp);

        // Add message actions (copy, etc.)
        const actions = this.createMessageActions(role, messageId);

        wrapper.appendChild(message);
        wrapper.appendChild(actions);

        this.messagesContainer.appendChild(wrapper);
        this.scrollToBottom();

        // Store user messages
        if (role === 'user') {
            this.messages.push({
                role: role,
                content: content,
                timestamp: Date.now()
            });
            this.saveHistory();
        }

        return message;
    }

    /**
     * Format message content with enhanced markdown-like styling.
     * Supports: code blocks, inline code, bold, italic, strikethrough,
     * links, headers, lists, blockquotes, tables, and horizontal rules.
     * @param {string} content - Raw message content
     * @returns {string} - Formatted HTML
     */
    formatMessage(content) {
        if (!content) return '';

        // Escape HTML first
        let formatted = this.escapeHtml(content);

        // Code blocks with syntax highlighting placeholder
        formatted = formatted.replace(/```(\w*)\n([\s\S]*?)```/g, (match, lang, code) => {
            const langClass = lang ? ` language-${lang}` : '';
            const langLabel = lang ? `<span class="code-lang">${lang}</span>` : '';
            return `<div class="code-block">${langLabel}<pre><code class="${langClass}">${code.trim()}</code></pre></div>`;
        });

        // Inline code
        formatted = formatted.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

        // Headers (# ## ### etc)
        formatted = formatted.replace(/^(#{1,6})\s+(.+)$/gm, (match, hashes, text) => {
            const level = hashes.length;
            return `<h${level} class="md-heading">${text}</h${level}>`;
        });

        // Bold with ** or __
        formatted = formatted.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        formatted = formatted.replace(/__([^_]+)__/g, '<strong>$1</strong>');

        // Italic with * or _
        formatted = formatted.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        formatted = formatted.replace(/_([^_]+)_/g, '<em>$1</em>');

        // Strikethrough
        formatted = formatted.replace(/~~([^~]+)~~/g, '<del>$1</del>');

        // Links
        formatted = formatted.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
            '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

        // Horizontal rules
        formatted = formatted.replace(/^---$/gm, '<hr class="md-hr">');
        formatted = formatted.replace(/^\*\*\*$/gm, '<hr class="md-hr">');

        // Blockquotes
        formatted = formatted.replace(/^&gt;\s*(.+)$/gm, '<blockquote>$1</blockquote>');

        // Merge consecutive blockquotes
        formatted = formatted.replace(/<\/blockquote>\s*<blockquote>/g, '<br>');

        // Unordered lists
        formatted = formatted.replace(/^[-*+]\s+(.+)$/gm, '<li class="md-li">$1</li>');
        formatted = formatted.replace(/(<li class="md-li">.*<\/li>)/s, '<ul class="md-ul">$1</ul>');

        // Ordered lists
        formatted = formatted.replace(/^\d+\.\s+(.+)$/gm, '<li class="md-li-ordered">$1</li>');
        formatted = formatted.replace(/(<li class="md-li-ordered">.*<\/li>)/s, '<ol class="md-ol">$1</ol>');

        // Tables (basic support)
        formatted = this.formatTables(formatted);

        // Task lists
        formatted = formatted.replace(/\[ \]\s+(.+)/g, '<span class="task-item">☐ $1</span>');
        formatted = formatted.replace(/\[x\]\s+(.+)/gi, '<span class="task-item completed">☑ $1</span>');

        // Line breaks (convert double newlines to paragraphs)
        formatted = formatted.replace(/\n\n/g, '</p><p>');
        formatted = formatted.replace(/\n/g, '<br>');

        // Wrap in paragraph if not already structured
        if (!formatted.startsWith('<') || formatted.startsWith('<br>')) {
            formatted = `<p>${formatted}</p>`;
        }

        return formatted;
    }

    /**
     * Format markdown tables.
     * @param {string} text - Text to process
     * @returns {string} - Text with tables formatted
     */
    formatTables(text) {
        // Simple table detection: rows starting with |
        const tablePattern = /(\|.+\|[\r\n]+)+/g;

        return text.replace(tablePattern, (match) => {
            const rows = match.trim().split(/[\r\n]+/);
            if (rows.length < 2) return match;

            let html = '<table class="md-table">';
            let isHeader = true;

            for (const row of rows) {
                // Skip separator row
                if (/^\|[\s\-:]+\|$/.test(row)) {
                    isHeader = false;
                    continue;
                }

                const cells = row.split('|').filter(c => c.trim());
                const tag = isHeader ? 'th' : 'td';
                const rowClass = isHeader ? 'thead' : 'tbody';

                html += `<tr class="${rowClass}">`;
                for (const cell of cells) {
                    html += `<${tag}>${cell.trim()}</${tag}>`;
                }
                html += '</tr>';

                if (isHeader) isHeader = false;
            }

            html += '</table>';
            return html;
        });
    }

    /**
     * Escape HTML special characters.
     * @param {string} text - Raw text
     * @returns {string} - Escaped text
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Format a timestamp for display.
     * @param {Date} date - Date object
     * @returns {string} - Formatted time string
     */
    formatTime(date) {
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    /**
     * Scroll the messages container to the bottom.
     */
    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }

    /**
     * Set the generating state.
     * @param {boolean} generating - Whether generating
     * @param {string} state - State type: 'thinking', 'generating', 'tool-use'
     */
    setGenerating(generating, state = 'thinking') {
        this.isGenerating = generating;

        this.sendButton.disabled = generating;
        this.stopButton.classList.toggle('hidden', !generating);
        this.typingIndicator.classList.toggle('hidden', !generating);

        if (generating) {
            this.sendButton.classList.add('hidden');
            this.setTypingState(state);
        } else {
            this.sendButton.classList.remove('hidden');
            this.resetTokenCounter();
        }
    }

    /**
     * Set the typing indicator state.
     * @param {string} state - 'thinking', 'generating', 'tool-use'
     */
    setTypingState(state) {
        this.typingIndicator.dataset.state = state;
        const actionEl = this.typingIndicator.querySelector('.typing-action');

        if (actionEl) {
            switch (state) {
                case 'thinking':
                    actionEl.textContent = 'is thinking...';
                    break;
                case 'generating':
                    actionEl.textContent = 'is responding...';
                    break;
                case 'tool-use':
                    actionEl.textContent = 'is using tools...';
                    break;
                default:
                    actionEl.textContent = 'is working...';
            }
        }
    }

    /**
     * Update the token counter display.
     * @param {number} tokens - Number of tokens generated
     */
    updateTokenCounter(tokens) {
        const counter = document.getElementById('token-counter');
        if (counter) {
            counter.textContent = `${tokens} tokens`;
        }
    }

    /**
     * Reset the token counter.
     */
    resetTokenCounter() {
        const counter = document.getElementById('token-counter');
        if (counter) {
            counter.textContent = '';
        }
    }

    /**
     * Stop the current generation.
     */
    stopGeneration() {
        if (api.isStreaming()) {
            api.cancelStream();
        }
    }

    /**
     * Save chat history to localStorage.
     */
    saveHistory() {
        try {
            const historyToSave = this.messages.slice(-Config.chat.maxHistoryLength);
            localStorage.setItem(Config.storage.chatHistory, JSON.stringify(historyToSave));
        } catch (e) {
            console.warn('Failed to save chat history:', e);
        }
    }

    /**
     * Load chat history from localStorage.
     */
    loadHistory() {
        try {
            const saved = localStorage.getItem(Config.storage.chatHistory);
            if (saved) {
                this.messages = JSON.parse(saved);

                // Render saved messages (optional - uncomment to show history on load)
                // this.renderSavedMessages();
            }
        } catch (e) {
            console.warn('Failed to load chat history:', e);
            this.messages = [];
        }
    }

    /**
     * Render saved messages from history.
     */
    renderSavedMessages() {
        // Hide welcome message
        const welcome = this.messagesContainer.querySelector('.welcome-message');
        if (welcome && this.messages.length > 0) {
            welcome.remove();
        }

        this.messages.forEach(msg => {
            const wrapper = document.createElement('div');
            wrapper.className = `message-wrapper ${msg.role}`;

            const message = document.createElement('div');
            message.className = `message ${msg.role}`;

            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            contentDiv.innerHTML = this.formatMessage(msg.content);

            const meta = document.createElement('div');
            meta.className = 'message-meta';
            meta.innerHTML = `<span class="message-time">${this.formatTime(new Date(msg.timestamp))}</span>`;

            message.appendChild(contentDiv);
            wrapper.appendChild(message);
            wrapper.appendChild(meta);

            this.messagesContainer.appendChild(wrapper);
        });

        this.scrollToBottom();
    }

    /**
     * Clear chat history.
     */
    clearHistory() {
        this.messages = [];
        localStorage.removeItem(Config.storage.chatHistory);

        // Clear UI
        this.messagesContainer.innerHTML = `
            <div class="welcome-message">
                <h2>Welcome to Progenitor Chat</h2>
                <p>Start a conversation with the Progenitor AI agent.</p>
                <div class="quick-actions">
                    <button class="quick-action" data-message="What is your current status?">
                        📊 Check Status
                    </button>
                    <button class="quick-action" data-message="What tasks are in the backlog?">
                        📋 View Backlog
                    </button>
                    <button class="quick-action" data-message="Show me your capabilities">
                        🎯 Capabilities
                    </button>
                </div>
            </div>
        `;

        // Re-bind quick actions
        document.querySelectorAll('.quick-action').forEach(btn => {
            btn.addEventListener('click', () => {
                const message = btn.dataset.message;
                if (message) {
                    this.inputElement.value = message;
                    this.sendMessage();
                }
            });
        });
    }

    /**
     * Create message action buttons.
     * @param {string} role - Message role
     * @param {string} messageId - Message ID
     * @returns {HTMLElement} Actions container
     */
    createMessageActions(role, messageId) {
        const actions = document.createElement('div');
        actions.className = 'message-actions';

        // Copy button
        const copyBtn = document.createElement('button');
        copyBtn.className = 'msg-action-btn copy-btn';
        copyBtn.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>
            Copy
        `;
        copyBtn.addEventListener('click', () => this.copyMessage(messageId, copyBtn));
        actions.appendChild(copyBtn);

        // Regenerate button (only for assistant messages)
        if (role === 'assistant') {
            const regenBtn = document.createElement('button');
            regenBtn.className = 'msg-action-btn regen-btn';
            regenBtn.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="1 4 1 10 7 10"></polyline>
                    <polyline points="23 20 23 14 17 14"></polyline>
                    <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"></path>
                </svg>
                Regenerate
            `;
            regenBtn.addEventListener('click', () => this.regenerateResponse(messageId));
            actions.appendChild(regenBtn);
        }

        return actions;
    }

    /**
     * Copy message content to clipboard.
     * @param {string} messageId - Message ID
     * @param {HTMLElement} button - Copy button element
     */
    async copyMessage(messageId, button) {
        const wrapper = document.querySelector(`[data-message-id="${messageId}"]`);
        const content = wrapper?.querySelector('.message-content')?.textContent;

        if (!content) return;

        try {
            await navigator.clipboard.writeText(content);

            // Visual feedback
            button.classList.add('copied');
            const originalHTML = button.innerHTML;
            button.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
                Copied!
            `;

            setTimeout(() => {
                button.classList.remove('copied');
                button.innerHTML = originalHTML;
            }, 2000);
        } catch (e) {
            console.error('Failed to copy:', e);
            window.notifications?.show('error', 'Copy Failed', 'Could not copy to clipboard');
        }
    }

    /**
     * Regenerate the assistant's response.
     * @param {string} messageId - Message ID of the response to regenerate
     */
    async regenerateResponse(messageId) {
        // Find the previous user message
        const wrapper = document.querySelector(`[data-message-id="${messageId}"]`);
        if (!wrapper) return;

        // Get index in DOM
        const allWrappers = [...this.messagesContainer.querySelectorAll('.message-wrapper')];
        const index = allWrappers.indexOf(wrapper);

        // Find the user message before this assistant message
        let userMessage = null;
        for (let i = index - 1; i >= 0; i--) {
            if (allWrappers[i].classList.contains('user')) {
                userMessage = allWrappers[i].querySelector('.message-content')?.textContent;
                break;
            }
        }

        if (!userMessage) {
            window.notifications?.show('warning', 'Cannot Regenerate', 'No user message found');
            return;
        }

        // Remove the old response
        wrapper.remove();
        this.messages = this.messages.filter((m, i) => {
            // Remove the last assistant message
            return !(m.role === 'assistant' && i === this.messages.length - 1);
        });

        // Re-send the message
        this.setGenerating(true);
        const history = this.messages.slice(-10).map(m => ({
            role: m.role,
            content: m.content
        }));
        const assistantMsg = this.addMessage('assistant', '', true);

        try {
            if (Config.chat.streamingEnabled) {
                await this.streamResponse(userMessage, history, assistantMsg);
            } else {
                await this.regularResponse(userMessage, history, assistantMsg);
            }
        } catch (error) {
            this.handleError(error, assistantMsg);
        } finally {
            this.setGenerating(false);
        }
    }

    /**
     * Render all messages from history.
     * Used when switching sessions.
     */
    renderAllMessages() {
        // Clear existing messages
        this.messagesContainer.innerHTML = '';

        if (this.messages.length === 0) {
            // Show welcome message
            this.messagesContainer.innerHTML = `
                <div class="welcome-message">
                    <h2>Welcome to Progenitor Chat</h2>
                    <p>Start a conversation with the Progenitor AI agent.</p>
                    <div class="quick-actions">
                        <button class="quick-action" data-message="What is your current status?">
                            📊 Check Status
                        </button>
                        <button class="quick-action" data-message="What tasks are in the backlog?">
                            📋 View Backlog
                        </button>
                        <button class="quick-action" data-message="Show me your capabilities">
                            🎯 Capabilities
                        </button>
                    </div>
                </div>
            `;
            // Re-bind quick actions
            document.querySelectorAll('.quick-action').forEach(btn => {
                btn.addEventListener('click', () => {
                    const message = btn.dataset.message;
                    if (message) {
                        this.inputElement.value = message;
                        this.sendMessage();
                    }
                });
            });
            return;
        }

        // Render each message
        this.messages.forEach(msg => {
            const messageId = `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

            const wrapper = document.createElement('div');
            wrapper.className = `message-wrapper ${msg.role}`;
            wrapper.dataset.messageId = messageId;

            const message = document.createElement('div');
            message.className = `message ${msg.role}`;

            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            contentDiv.innerHTML = this.formatMessage(msg.content);

            const timestamp = document.createElement('div');
            timestamp.className = 'message-timestamp';
            timestamp.textContent = this.formatTime(new Date(msg.timestamp));

            message.appendChild(contentDiv);
            message.appendChild(timestamp);

            const actions = this.createMessageActions(msg.role, messageId);

            wrapper.appendChild(message);
            wrapper.appendChild(actions);

            this.messagesContainer.appendChild(wrapper);
        });

        this.scrollToBottom();
    }
}

// Export for use in main.js
window.ChatManager = ChatManager;
