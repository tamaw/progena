/**
 * Progenitor Chat UI - Metrics Dashboard Module
 *
 * Provides real-time metrics visualization and monitoring.
 */

export class MetricsDashboard {
    constructor() {
        this.metrics = {
            messages: { sent: 0, received: 0 },
            sessions: { total: 0, active: 0 },
            response: { avgTime: 0, times: [] },
            tokens: { input: 0, output: 0 },
            errors: { count: 0, last: null },
            uptime: { start: Date.now() }
        };

        this.storageKey = 'progenitor_metrics';
        this.isOpen = false;

        this.init();
    }

    init() {
        this.loadMetrics();
        this.createUI();
        this.bindEvents();
        this.startUpdates();
    }

    /**
     * Load metrics from storage.
     */
    loadMetrics() {
        try {
            const saved = localStorage.getItem(this.storageKey);
            if (saved) {
                const data = JSON.parse(saved);
                // Merge with defaults (in case new fields were added)
                this.metrics = { ...this.metrics, ...data };
            }
        } catch (e) {
            console.error('Failed to load metrics:', e);
        }
    }

    /**
     * Save metrics to storage.
     */
    saveMetrics() {
        try {
            localStorage.setItem(this.storageKey, JSON.stringify(this.metrics));
        } catch (e) {
            console.error('Failed to save metrics:', e);
        }
    }

    /**
     * Record a message sent.
     */
    recordMessageSent() {
        this.metrics.messages.sent++;
        this.saveMetrics();
        this.updateDisplay();
    }

    /**
     * Record a response received.
     * @param {number} responseTime - Response time in ms
     * @param {number} tokens - Token count (if available)
     */
    recordResponseReceived(responseTime, tokens = 0) {
        this.metrics.messages.received++;

        // Update response times (keep last 50)
        this.metrics.response.times.push(responseTime);
        if (this.metrics.response.times.length > 50) {
            this.metrics.response.times.shift();
        }

        // Calculate average
        const sum = this.metrics.response.times.reduce((a, b) => a + b, 0);
        this.metrics.response.avgTime = Math.round(sum / this.metrics.response.times.length);

        // Update tokens
        if (tokens > 0) {
            this.metrics.tokens.output += tokens;
        }

        this.saveMetrics();
        this.updateDisplay();
    }

    /**
     * Record an error.
     * @param {string} error - Error message
     */
    recordError(error) {
        this.metrics.errors.count++;
        this.metrics.errors.last = {
            message: error,
            timestamp: Date.now()
        };
        this.saveMetrics();
        this.updateDisplay();
    }

    /**
     * Record session activity.
     * @param {number} total - Total sessions
     */
    recordSessionActivity(total) {
        this.metrics.sessions.total = total;
        this.saveMetrics();
        this.updateDisplay();
    }

    /**
     * Create the metrics UI.
     */
    createUI() {
        // Add metrics button to status header
        const statusHeader = document.querySelector('.status-header');
        if (statusHeader) {
            const metricsBtn = document.createElement('button');
            metricsBtn.id = 'metrics-btn';
            metricsBtn.className = 'btn-metrics';
            metricsBtn.title = 'Metrics Dashboard';
            metricsBtn.innerHTML = '📊';
            statusHeader.appendChild(metricsBtn);
        }

        // Create metrics panel
        const panel = document.createElement('div');
        panel.id = 'metrics-panel';
        panel.className = 'metrics-panel hidden';
        panel.innerHTML = `
            <div class="metrics-header">
                <h3>📊 Metrics Dashboard</h3>
                <div class="metrics-actions">
                    <button class="btn-reset-metrics" title="Reset Metrics">🔄</button>
                    <button class="btn-close-metrics">&times;</button>
                </div>
            </div>
            <div class="metrics-body">
                <div class="metrics-grid">
                    <div class="metric-card">
                        <div class="metric-icon">💬</div>
                        <div class="metric-data">
                            <div class="metric-value" id="metric-messages-sent">0</div>
                            <div class="metric-label">Messages Sent</div>
                        </div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-icon">🤖</div>
                        <div class="metric-data">
                            <div class="metric-value" id="metric-messages-received">0</div>
                            <div class="metric-label">Responses</div>
                        </div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-icon">⏱️</div>
                        <div class="metric-data">
                            <div class="metric-value" id="metric-avg-response">0ms</div>
                            <div class="metric-label">Avg Response</div>
                        </div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-icon">📁</div>
                        <div class="metric-data">
                            <div class="metric-value" id="metric-sessions">0</div>
                            <div class="metric-label">Sessions</div>
                        </div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-icon">⚠️</div>
                        <div class="metric-data">
                            <div class="metric-value" id="metric-errors">0</div>
                            <div class="metric-label">Errors</div>
                        </div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-icon">⏰</div>
                        <div class="metric-data">
                            <div class="metric-value" id="metric-uptime">0m</div>
                            <div class="metric-label">Uptime</div>
                        </div>
                    </div>
                </div>
                <div class="metrics-chart">
                    <h4>Response Time History</h4>
                    <div class="chart-container" id="response-chart">
                        <div class="chart-bars"></div>
                        <div class="chart-axis"></div>
                    </div>
                </div>
                <div class="metrics-last-error" id="last-error">
                    <h4>Last Error</h4>
                    <div class="error-content">No errors recorded</div>
                </div>
            </div>
        `;

        document.body.appendChild(panel);
        this.panel = panel;
    }

    /**
     * Bind event handlers.
     */
    bindEvents() {
        // Toggle panel
        document.getElementById('metrics-btn')?.addEventListener('click', () => {
            this.toggle();
        });

        // Close button
        this.panel?.querySelector('.btn-close-metrics')?.addEventListener('click', () => {
            this.close();
        });

        // Reset metrics
        this.panel?.querySelector('.btn-reset-metrics')?.addEventListener('click', () => {
            if (confirm('Reset all metrics?')) {
                this.reset();
            }
        });

        // Listen for message events
        window.addEventListener('chat-message-sent', () => this.recordMessageSent());
        window.addEventListener('chat-response-received', (e) => {
            this.recordResponseReceived(e.detail?.responseTime || 0, e.detail?.tokens || 0);
        });
        window.addEventListener('chat-error', (e) => {
            this.recordError(e.detail?.message || 'Unknown error');
        });
    }

    /**
     * Start periodic updates.
     */
    startUpdates() {
        // Update uptime every minute
        setInterval(() => {
            this.updateDisplay();
        }, 60000);
    }

    /**
     * Toggle panel visibility.
     */
    toggle() {
        this.isOpen = !this.isOpen;
        this.panel?.classList.toggle('hidden', !this.isOpen);
        if (this.isOpen) {
            this.updateDisplay();
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
     * Reset all metrics.
     */
    reset() {
        this.metrics = {
            messages: { sent: 0, received: 0 },
            sessions: { total: 0, active: 0 },
            response: { avgTime: 0, times: [] },
            tokens: { input: 0, output: 0 },
            errors: { count: 0, last: null },
            uptime: { start: Date.now() }
        };
        this.saveMetrics();
        this.updateDisplay();
        window.notifications?.show('info', 'Metrics Reset', 'All metrics have been cleared.');
    }

    /**
     * Update the display.
     */
    updateDisplay() {
        if (!this.panel) return;

        // Update values
        this.setText('#metric-messages-sent', this.metrics.messages.sent);
        this.setText('#metric-messages-received', this.metrics.messages.received);
        this.setText('#metric-avg-response', `${this.metrics.response.avgTime}ms`);
        this.setText('#metric-sessions', this.metrics.sessions.total);
        this.setText('#metric-errors', this.metrics.errors.count);

        // Calculate uptime
        const uptimeMs = Date.now() - this.metrics.uptime.start;
        const uptimeMinutes = Math.floor(uptimeMs / 60000);
        const uptimeHours = Math.floor(uptimeMinutes / 60);
        const uptimeStr = uptimeHours > 0
            ? `${uptimeHours}h ${uptimeMinutes % 60}m`
            : `${uptimeMinutes}m`;
        this.setText('#metric-uptime', uptimeStr);

        // Update chart
        this.updateChart();

        // Update last error
        const errorEl = this.panel.querySelector('#last-error .error-content');
        if (errorEl) {
            if (this.metrics.errors.last) {
                const time = new Date(this.metrics.errors.last.timestamp).toLocaleTimeString();
                errorEl.innerHTML = `
                    <span class="error-time">${time}</span>
                    <span class="error-message">${this.metrics.errors.last.message}</span>
                `;
            } else {
                errorEl.textContent = 'No errors recorded';
            }
        }
    }

    /**
     * Update the response time chart.
     */
    updateChart() {
        const container = this.panel?.querySelector('#response-chart .chart-bars');
        if (!container) return;

        const times = this.metrics.response.times.slice(-20); // Last 20
        if (times.length === 0) {
            container.innerHTML = '<div class="chart-empty">No data yet</div>';
            return;
        }

        const maxTime = Math.max(...times, 100);

        container.innerHTML = times.map((time, i) => {
            const height = Math.max(5, (time / maxTime) * 100);
            const color = time < 500 ? 'var(--color-success)'
                        : time < 1000 ? 'var(--color-warning)'
                        : 'var(--color-error)';
            return `
                <div class="chart-bar"
                     style="height: ${height}%; background-color: ${color};"
                     title="${time}ms">
                </div>
            `;
        }).join('');
    }

    /**
     * Set text content helper.
     */
    setText(selector, value) {
        const el = this.panel?.querySelector(selector);
        if (el) el.textContent = value;
    }
}

export default MetricsDashboard;
