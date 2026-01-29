/**
 * Progenitor Chat UI - Logs Viewer Module
 *
 * Provides real-time agent logs viewing and filtering.
 */

export class LogsViewer {
    constructor() {
        this.logs = [];
        this.maxLogs = 500;
        this.filter = {
            level: 'all',
            search: ''
        };
        this.isOpen = false;
        this.autoScroll = true;

        this.init();
    }

    init() {
        this.createUI();
        this.bindEvents();
        this.loadLogs();
    }

    /**
     * Add a log entry.
     * @param {string} level - Log level (debug, info, warn, error)
     * @param {string} message - Log message
     * @param {Object} data - Optional data object
     */
    log(level, message, data = null) {
        const entry = {
            id: Date.now() + Math.random().toString(36).substr(2, 9),
            timestamp: new Date().toISOString(),
            level: level.toLowerCase(),
            message,
            data
        };

        this.logs.push(entry);

        // Trim logs if exceeding max
        if (this.logs.length > this.maxLogs) {
            this.logs.shift();
        }

        this.renderLog(entry);

        // Console output
        const consoleMethod = level === 'error' ? console.error
                           : level === 'warn' ? console.warn
                           : level === 'debug' ? console.debug
                           : console.log;
        consoleMethod(`[${level.toUpperCase()}] ${message}`, data || '');
    }

    /**
     * Log shortcuts.
     */
    debug(message, data) { this.log('debug', message, data); }
    info(message, data) { this.log('info', message, data); }
    warn(message, data) { this.log('warn', message, data); }
    error(message, data) { this.log('error', message, data); }

    /**
     * Load logs from API.
     */
    async loadLogs() {
        try {
            const response = await fetch('/ui/logs');
            if (response.ok) {
                const data = await response.json();
                // Parse JSONL logs
                if (data.logs) {
                    const entries = data.logs.split('\n')
                        .filter(line => line.trim())
                        .map(line => {
                            try {
                                return JSON.parse(line);
                            } catch {
                                return null;
                            }
                        })
                        .filter(Boolean);

                    this.logs = entries.slice(-this.maxLogs);
                    this.renderAllLogs();
                }
            }
        } catch (e) {
            this.warn('Failed to load logs from server', { error: e.message });
        }
    }

    /**
     * Create the logs viewer UI.
     */
    createUI() {
        // Add logs button to status header
        const statusHeader = document.querySelector('.status-header');
        if (statusHeader) {
            const logsBtn = document.createElement('button');
            logsBtn.id = 'logs-btn';
            logsBtn.className = 'btn-logs';
            logsBtn.title = 'Agent Logs';
            logsBtn.innerHTML = '📜';
            statusHeader.appendChild(logsBtn);
        }

        // Create logs panel
        const panel = document.createElement('div');
        panel.id = 'logs-panel';
        panel.className = 'logs-panel hidden';
        panel.innerHTML = `
            <div class="logs-header">
                <h3>📜 Agent Logs</h3>
                <div class="logs-controls">
                    <select id="logs-level-filter" class="logs-filter">
                        <option value="all">All Levels</option>
                        <option value="error">Errors</option>
                        <option value="warn">Warnings</option>
                        <option value="info">Info</option>
                        <option value="debug">Debug</option>
                    </select>
                    <input type="text"
                           id="logs-search"
                           class="logs-search"
                           placeholder="Search logs...">
                    <button class="btn-auto-scroll active" title="Auto-scroll">
                        ⬇️
                    </button>
                    <button class="btn-clear-logs" title="Clear Logs">🗑️</button>
                    <button class="btn-refresh-logs" title="Refresh">🔄</button>
                    <button class="btn-close-logs">&times;</button>
                </div>
            </div>
            <div class="logs-body">
                <div class="logs-container" id="logs-container"></div>
            </div>
            <div class="logs-footer">
                <span class="logs-count">0 entries</span>
                <span class="logs-status">Ready</span>
            </div>
        `;

        document.body.appendChild(panel);
        this.panel = panel;
        this.container = panel.querySelector('#logs-container');
    }

    /**
     * Bind event handlers.
     */
    bindEvents() {
        // Toggle panel
        document.getElementById('logs-btn')?.addEventListener('click', () => {
            this.toggle();
        });

        // Close button
        this.panel?.querySelector('.btn-close-logs')?.addEventListener('click', () => {
            this.close();
        });

        // Level filter
        this.panel?.querySelector('#logs-level-filter')?.addEventListener('change', (e) => {
            this.filter.level = e.target.value;
            this.renderAllLogs();
        });

        // Search filter
        this.panel?.querySelector('#logs-search')?.addEventListener('input', (e) => {
            this.filter.search = e.target.value.toLowerCase();
            this.renderAllLogs();
        });

        // Auto-scroll toggle
        this.panel?.querySelector('.btn-auto-scroll')?.addEventListener('click', (e) => {
            this.autoScroll = !this.autoScroll;
            e.target.classList.toggle('active', this.autoScroll);
        });

        // Clear logs
        this.panel?.querySelector('.btn-clear-logs')?.addEventListener('click', () => {
            if (confirm('Clear all logs?')) {
                this.logs = [];
                this.renderAllLogs();
            }
        });

        // Refresh logs
        this.panel?.querySelector('.btn-refresh-logs')?.addEventListener('click', () => {
            this.loadLogs();
        });

        // Listen for log events
        window.addEventListener('agent-log', (e) => {
            const { level, message, data } = e.detail;
            this.log(level, message, data);
        });
    }

    /**
     * Toggle panel visibility.
     */
    toggle() {
        this.isOpen = !this.isOpen;
        this.panel?.classList.toggle('hidden', !this.isOpen);
        if (this.isOpen) {
            this.renderAllLogs();
            this.scrollToBottom();
        }
    }

    /**
     * Close panel.
     */
    close() {
        this.isOpen = false;
        this.panel?.classList.add('hidden');
    }

    /**
     * Render all logs with filtering.
     */
    renderAllLogs() {
        if (!this.container) return;

        const filteredLogs = this.logs.filter(log => {
            // Level filter
            if (this.filter.level !== 'all' && log.level !== this.filter.level) {
                return false;
            }
            // Search filter
            if (this.filter.search) {
                const searchText = `${log.message} ${JSON.stringify(log.data || '')}`.toLowerCase();
                if (!searchText.includes(this.filter.search)) {
                    return false;
                }
            }
            return true;
        });

        this.container.innerHTML = filteredLogs.map(log => this.createLogHtml(log)).join('');
        this.updateFooter(filteredLogs.length);

        if (this.autoScroll) {
            this.scrollToBottom();
        }
    }

    /**
     * Render a single log entry.
     */
    renderLog(entry) {
        if (!this.container || !this.isOpen) return;

        // Check if log passes filters
        if (this.filter.level !== 'all' && entry.level !== this.filter.level) {
            return;
        }
        if (this.filter.search) {
            const searchText = `${entry.message} ${JSON.stringify(entry.data || '')}`.toLowerCase();
            if (!searchText.includes(this.filter.search)) {
                return;
            }
        }

        this.container.insertAdjacentHTML('beforeend', this.createLogHtml(entry));
        this.updateFooter(this.container.children.length);

        if (this.autoScroll) {
            this.scrollToBottom();
        }
    }

    /**
     * Create HTML for a log entry.
     */
    createLogHtml(log) {
        const time = new Date(log.timestamp).toLocaleTimeString();
        const dataStr = log.data ? JSON.stringify(log.data, null, 2) : '';

        return `
            <div class="log-entry log-${log.level}" data-id="${log.id}">
                <span class="log-time">${time}</span>
                <span class="log-level">${log.level.toUpperCase()}</span>
                <span class="log-message">${this.escapeHtml(log.message)}</span>
                ${dataStr ? `<pre class="log-data">${this.escapeHtml(dataStr)}</pre>` : ''}
            </div>
        `;
    }

    /**
     * Update footer counts.
     */
    updateFooter(count) {
        const countEl = this.panel?.querySelector('.logs-count');
        if (countEl) {
            countEl.textContent = `${count} entries`;
        }
    }

    /**
     * Scroll to bottom of logs.
     */
    scrollToBottom() {
        if (this.container) {
            this.container.scrollTop = this.container.scrollHeight;
        }
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

export default LogsViewer;
