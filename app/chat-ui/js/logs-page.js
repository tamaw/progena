/**
 * Progenitor - Logs Page JavaScript
 * 
 * Provides a dedicated full-page view for logs with advanced filtering,
 * search, real-time updates, and detailed log inspection.
 */

class LogsPageManager {
    constructor() {
        this.logs = [];
        this.filteredLogs = [];
        this.maxLogs = 1000;
        this.filter = {
            level: 'all',
            source: 'all',
            search: ''
        };
        this.isPaused = false;
        this.autoScroll = true;
        this.selectedLogId = null;
        this.isConnected = false;
        this.eventSource = null;
        
        // Stats
        this.stats = {
            total: 0,
            debug: 0,
            info: 0,
            warn: 0,
            error: 0
        };
        
        this.init();
    }
    
    init() {
        console.log('Initializing Logs Page...');
        
        this.initTheme();
        this.bindEvents();
        this.connectSSE();
        this.fetchLogs();
        
        // Refresh logs every 5 seconds
        setInterval(() => {
            if (this.isConnected && !this.isPaused) {
                this.fetchLogs();
            }
        }, 5000);
    }
    
    // =========================================================================
    // Theme
    // =========================================================================
    
    initTheme() {
        const savedTheme = localStorage.getItem('progenitor_theme') || 'dark';
        document.documentElement.dataset.theme = savedTheme;
        
        const themeBtn = document.getElementById('btn-theme');
        if (themeBtn) {
            themeBtn.textContent = savedTheme === 'dark' ? '🌙' : '☀️';
        }
    }
    
    toggleTheme() {
        const root = document.documentElement;
        const current = root.dataset.theme;
        const newTheme = current === 'dark' ? 'light' : 'dark';
        root.dataset.theme = newTheme;
        localStorage.setItem('progenitor_theme', newTheme);
        
        const themeBtn = document.getElementById('btn-theme');
        if (themeBtn) {
            themeBtn.textContent = newTheme === 'dark' ? '🌙' : '☀️';
        }
    }
    
    // =========================================================================
    // SSE Connection
    // =========================================================================
    
    connectSSE() {
        if (this.eventSource) {
            this.eventSource.close();
        }
        
        const baseUrl = window.Config?.api?.baseUrl || 'http://localhost:8000';
        const url = `${baseUrl}/api/ui/events`;
        
        try {
            this.eventSource = new EventSource(url);
            
            this.eventSource.onopen = () => {
                this.isConnected = true;
                this.updateConnectionStatus('connected');
            };
            
            this.eventSource.addEventListener('log', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (!this.isPaused) {
                        this.addLog(data);
                    }
                } catch (e) {
                    console.error('Failed to parse log event:', e);
                }
            });
            
            this.eventSource.addEventListener('status', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    document.getElementById('version').textContent = data.version || '0.1.0';
                } catch (e) {}
            });
            
            this.eventSource.onerror = () => {
                this.isConnected = false;
                this.updateConnectionStatus('disconnected');
                
                setTimeout(() => {
                    if (!this.isConnected) {
                        this.connectSSE();
                    }
                }, 5000);
            };
            
        } catch (e) {
            console.error('Failed to create EventSource:', e);
        }
    }
    
    updateConnectionStatus(status) {
        const indicator = document.getElementById('connection-status');
        const pulseIndicator = document.getElementById('pulse-indicator');
        
        if (indicator) {
            indicator.dataset.status = status;
            const statusText = indicator.querySelector('.status-text');
            if (statusText) {
                statusText.textContent = status === 'connected' ? 'Connected' : 'Disconnected';
            }
        }
        
        if (pulseIndicator) {
            pulseIndicator.classList.toggle('disconnected', status !== 'connected');
        }
    }
    
    // =========================================================================
    // Data Fetching
    // =========================================================================
    
    async fetchLogs() {
        const baseUrl = window.Config?.api?.baseUrl || 'http://localhost:8000';
        
        try {
            const response = await fetch(`${baseUrl}/api/ui/logs?format=json&limit=200`);
            if (!response.ok) return;
            
            const data = await response.json();
            if (data.entries && data.entries.length > 0) {
                // Merge new entries
                const existingIds = new Set(this.logs.map(l => l.id || `${l.timestamp}|${l.message}`));
                
                for (const entry of data.entries) {
                    const id = entry.id || `${entry.timestamp}|${entry.message}`;
                    if (!existingIds.has(id)) {
                        const logEntry = {
                            id: id,
                            timestamp: entry.timestamp ? new Date(entry.timestamp) : new Date(),
                            level: (entry.level || 'info').toLowerCase(),
                            message: entry.message || '',
                            source: entry.source || entry.logger || 'progenitor',
                            data: entry.data || entry.extra || null
                        };
                        this.logs.push(logEntry);
                        this.updateStats(logEntry.level, 1);
                        existingIds.add(id);
                    }
                }
                
                // Trim to max
                while (this.logs.length > this.maxLogs) {
                    const removed = this.logs.shift();
                    this.updateStats(removed.level, -1);
                }
                
                this.filterAndRender();
            }
            
            document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
            
        } catch (e) {
            console.debug('Failed to fetch logs:', e);
        }
    }
    
    addLog(entry) {
        const logEntry = {
            id: entry.id || `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
            timestamp: entry.timestamp ? new Date(entry.timestamp) : new Date(),
            level: (entry.level || 'info').toLowerCase(),
            message: entry.message || '',
            source: entry.source || entry.logger || 'progenitor',
            data: entry.data || entry.extra || null,
            isNew: true
        };
        
        this.logs.push(logEntry);
        this.updateStats(logEntry.level, 1);
        
        // Trim
        while (this.logs.length > this.maxLogs) {
            const removed = this.logs.shift();
            this.updateStats(removed.level, -1);
        }
        
        this.filterAndRender();
        
        // Remove "new" flag after animation
        setTimeout(() => {
            logEntry.isNew = false;
        }, 300);
    }
    
    updateStats(level, delta) {
        this.stats.total += delta;
        if (this.stats[level] !== undefined) {
            this.stats[level] += delta;
        }
        this.renderStats();
    }
    
    // =========================================================================
    // Filtering
    // =========================================================================
    
    filterAndRender() {
        this.filteredLogs = this.logs.filter(log => {
            // Level filter
            if (this.filter.level !== 'all' && log.level !== this.filter.level) {
                return false;
            }
            
            // Source filter
            if (this.filter.source !== 'all') {
                const source = (log.source || '').toLowerCase();
                if (!source.includes(this.filter.source.toLowerCase())) {
                    return false;
                }
            }
            
            // Search filter
            if (this.filter.search) {
                const searchLower = this.filter.search.toLowerCase();
                const searchableText = `${log.message} ${log.source} ${JSON.stringify(log.data || '')}`.toLowerCase();
                if (!searchableText.includes(searchLower)) {
                    return false;
                }
            }
            
            return true;
        });
        
        this.renderLogs();
        this.renderStats();
        this.renderLevelBreakdown();
    }
    
    // =========================================================================
    // Rendering
    // =========================================================================
    
    renderLogs() {
        const container = document.getElementById('logs-container');
        if (!container) return;
        
        if (this.filteredLogs.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <span class="empty-icon">📜</span>
                    <span>${this.logs.length === 0 ? 'Waiting for logs...' : 'No logs match your filters'}</span>
                    <span class="empty-hint">Logs will appear here in real-time</span>
                </div>
            `;
            return;
        }
        
        // Create scroll area if not exists
        let scrollArea = container.querySelector('.logs-scroll-area');
        if (!scrollArea) {
            container.innerHTML = '<div class="logs-scroll-area"></div>';
            scrollArea = container.querySelector('.logs-scroll-area');
        }
        
        // Render last 500 logs for performance
        const logsToRender = this.filteredLogs.slice(-500);
        
        scrollArea.innerHTML = logsToRender.map(log => this.renderLogEntry(log)).join('');
        
        // Auto-scroll
        if (this.autoScroll) {
            scrollArea.scrollTop = scrollArea.scrollHeight;
        }
        
        document.getElementById('log-count').textContent = this.filteredLogs.length;
    }
    
    renderLogEntry(log) {
        const time = log.timestamp.toLocaleTimeString();
        const hasData = log.data && Object.keys(log.data).length > 0;
        const isSelected = this.selectedLogId === log.id;
        const message = this.highlightSearch(this.escapeHtml(log.message));
        
        return `
            <div class="log-entry-full ${log.isNew ? 'new' : ''} ${isSelected ? 'selected' : ''} ${log.level === 'error' ? 'error-entry' : ''}"
                 data-id="${log.id}"
                 onclick="logsPage.selectLog('${log.id}')">
                <span class="log-timestamp">${time}</span>
                <span class="log-level-badge ${log.level}">${log.level}</span>
                <span class="log-message-full">${message}</span>
                <span class="log-source">${this.escapeHtml(log.source)}</span>
                ${hasData && isSelected ? `
                    <div class="log-data-preview">
                        <pre>${this.syntaxHighlightJson(JSON.stringify(log.data, null, 2))}</pre>
                    </div>
                ` : ''}
            </div>
        `;
    }
    
    renderStats() {
        document.getElementById('log-count').textContent = this.filteredLogs.length;
        document.getElementById('error-count').textContent = this.stats.error;
        document.getElementById('warn-count').textContent = this.stats.warn;
        document.getElementById('log-status').textContent = this.isPaused ? 'Paused' : 'Live';
    }
    
    renderLevelBreakdown() {
        const total = this.stats.total || 1;
        
        document.getElementById('level-debug-bar').style.width = `${(this.stats.debug / total) * 100}%`;
        document.getElementById('level-info-bar').style.width = `${(this.stats.info / total) * 100}%`;
        document.getElementById('level-warn-bar').style.width = `${(this.stats.warn / total) * 100}%`;
        document.getElementById('level-error-bar').style.width = `${(this.stats.error / total) * 100}%`;
    }
    
    highlightSearch(text) {
        if (!this.filter.search) return text;
        
        const regex = new RegExp(`(${this.escapeRegex(this.filter.search)})`, 'gi');
        return text.replace(regex, '<span class="highlight">$1</span>');
    }
    
    syntaxHighlightJson(json) {
        if (!json) return '';
        
        return json
            .replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, (match) => {
                let cls = 'json-number';
                if (/^"/.test(match)) {
                    if (/:$/.test(match)) {
                        cls = 'json-key';
                    } else {
                        cls = 'json-string';
                    }
                } else if (/true|false/.test(match)) {
                    cls = 'json-boolean';
                } else if (/null/.test(match)) {
                    cls = 'json-null';
                }
                return `<span class="${cls}">${match}</span>`;
            });
    }
    
    // =========================================================================
    // Log Selection & Detail
    // =========================================================================
    
    selectLog(logId) {
        if (this.selectedLogId === logId) {
            this.selectedLogId = null;
        } else {
            this.selectedLogId = logId;
        }
        
        this.renderLogs();
        
        const log = this.logs.find(l => l.id === logId);
        if (log && this.selectedLogId) {
            this.showLogDetail(log);
        } else {
            this.hideLogDetail();
        }
    }
    
    showLogDetail(log) {
        const detail = document.getElementById('log-detail');
        const content = document.getElementById('detail-content');
        
        if (!detail || !content) return;
        
        content.innerHTML = `
            <div class="detail-grid">
                <span class="detail-label">Timestamp</span>
                <span class="detail-value">${log.timestamp.toISOString()}</span>
                
                <span class="detail-label">Level</span>
                <span class="detail-value"><span class="log-level-badge ${log.level}">${log.level}</span></span>
                
                <span class="detail-label">Source</span>
                <span class="detail-value">${this.escapeHtml(log.source)}</span>
                
                <span class="detail-label">Message</span>
                <span class="detail-value">${this.escapeHtml(log.message)}</span>
                
                ${log.data ? `
                    <div class="detail-json">
                        <pre>${this.syntaxHighlightJson(JSON.stringify(log.data, null, 2))}</pre>
                    </div>
                ` : ''}
            </div>
        `;
        
        detail.classList.remove('hidden');
    }
    
    hideLogDetail() {
        const detail = document.getElementById('log-detail');
        if (detail) {
            detail.classList.add('hidden');
        }
    }
    
    // =========================================================================
    // Controls
    // =========================================================================
    
    togglePause() {
        this.isPaused = !this.isPaused;
        
        const pauseBtn = document.getElementById('btn-pause');
        if (pauseBtn) {
            pauseBtn.textContent = this.isPaused ? '▶️' : '⏸️';
            pauseBtn.title = this.isPaused ? 'Resume' : 'Pause';
        }
        
        this.renderStats();
    }
    
    toggleAutoScroll() {
        this.autoScroll = !this.autoScroll;
        
        const scrollBtn = document.getElementById('btn-auto-scroll');
        if (scrollBtn) {
            scrollBtn.classList.toggle('active', this.autoScroll);
        }
    }
    
    clearLogs() {
        if (confirm('Clear all logs?')) {
            this.logs = [];
            this.stats = { total: 0, debug: 0, info: 0, warn: 0, error: 0 };
            this.selectedLogId = null;
            this.filterAndRender();
            this.hideLogDetail();
        }
    }
    
    exportLogs() {
        const logsToExport = this.filteredLogs.length > 0 ? this.filteredLogs : this.logs;
        
        const content = logsToExport.map(log => 
            `[${log.timestamp.toISOString()}] [${log.level.toUpperCase()}] [${log.source}] ${log.message}${log.data ? '\n' + JSON.stringify(log.data, null, 2) : ''}`
        ).join('\n\n');
        
        const blob = new Blob([content], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `progenitor-logs-${new Date().toISOString().split('T')[0]}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }
    
    // =========================================================================
    // Event Bindings
    // =========================================================================
    
    bindEvents() {
        // Theme toggle
        document.getElementById('btn-theme')?.addEventListener('click', () => this.toggleTheme());
        
        // Refresh
        document.getElementById('btn-refresh')?.addEventListener('click', () => this.fetchLogs());
        
        // Search
        document.getElementById('log-search')?.addEventListener('input', (e) => {
            this.filter.search = e.target.value;
            this.filterAndRender();
        });
        
        // Level filter
        document.getElementById('log-level-filter')?.addEventListener('change', (e) => {
            this.filter.level = e.target.value;
            this.filterAndRender();
        });
        
        // Source filter
        document.getElementById('log-source-filter')?.addEventListener('change', (e) => {
            this.filter.source = e.target.value;
            this.filterAndRender();
        });
        
        // Controls
        document.getElementById('btn-auto-scroll')?.addEventListener('click', () => this.toggleAutoScroll());
        document.getElementById('btn-pause')?.addEventListener('click', () => this.togglePause());
        document.getElementById('btn-clear')?.addEventListener('click', () => this.clearLogs());
        document.getElementById('btn-export')?.addEventListener('click', () => this.exportLogs());
        
        // Detail close
        document.getElementById('close-detail')?.addEventListener('click', () => {
            this.selectedLogId = null;
            this.hideLogDetail();
            this.renderLogs();
        });
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.selectedLogId = null;
                this.hideLogDetail();
                this.renderLogs();
            }
            if (e.key === ' ' && e.target.tagName !== 'INPUT') {
                e.preventDefault();
                this.togglePause();
            }
        });
    }
    
    // =========================================================================
    // Utilities
    // =========================================================================
    
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    escapeRegex(string) {
        return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }
}

// Initialize
let logsPage;
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        logsPage = new LogsPageManager();
        window.logsPage = logsPage;
    });
} else {
    logsPage = new LogsPageManager();
    window.logsPage = logsPage;
}

export { LogsPageManager };
