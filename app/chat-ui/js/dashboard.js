/**
 * Progenitor Dashboard - Comprehensive Stats & Monitoring
 * 
 * Features:
 * - Real-time metrics via SSE
 * - Token consumption tracking with charts
 * - Response time visualization
 * - Agent management and detail views
 * - Live log streaming with filtering
 * - Build status monitoring
 */

class DashboardManager {
    constructor() {
        // State
        this.isConnected = false;
        this.eventSource = null;
        this.lastStatus = null;
        this.isPaused = false;
        
        // Metrics history for charts
        this.metricsHistory = {
            tokens: { input: [], output: [], total: [], timestamps: [] },
            responseTimes: [],
            memory: [],
            cycles: [],
            timestamps: []
        };
        
        // Activity log
        this.activities = [];
        this.maxActivities = 50;
        
        // Logs
        this.logs = [];
        this.maxLogs = 500;
        this.logFilter = { level: 'all', search: '' };
        
        // Charts
        this.charts = {};
        
        // Token tracking
        this.tokenStats = {
            totalInput: 0,
            totalOutput: 0,
            sessionStart: Date.now()
        };
        
        // Load persisted data
        this.loadPersistedData();
        
        // Initialize
        this.init();
    }
    
    init() {
        console.log('Initializing Progenitor Dashboard...');
        
        // Initialize theme
        this.initTheme();
        
        // Initialize charts
        this.initCharts();
        
        // Bind UI events
        this.bindEvents();
        
        // Connect to SSE
        this.connectSSE();
        
        // Start periodic updates
        this.startPeriodicUpdates();
        
        // Fetch initial metrics
        this.fetchMetrics();
        
        console.log('Dashboard ready!');
    }
    
    // =========================================================================
    // API Fetching
    // =========================================================================
    
    async fetchMetrics() {
        const baseUrl = window.Config?.api?.baseUrl || 'http://localhost:8000';
        
        try {
            const response = await fetch(`${baseUrl}/api/ui/metrics`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            
            const metrics = await response.json();
            this.updateMetricsFromAPI(metrics);
        } catch (e) {
            console.error('Failed to fetch metrics:', e);
        }
    }
    
    updateMetricsFromAPI(metrics) {
        // Update token stats
        this.tokenStats.totalInput = metrics.tokens?.input_total || 0;
        this.tokenStats.totalOutput = metrics.tokens?.output_total || 0;
        this.tokenStats.totalCached = metrics.tokens?.cached_total || 0;
        
        // Update token display
        const tokensEl = document.getElementById('stat-tokens');
        const tokensInputEl = document.getElementById('stat-tokens-input');
        const tokensOutputEl = document.getElementById('stat-tokens-output');
        
        if (tokensEl) {
            tokensEl.textContent = this.formatNumber(metrics.tokens?.total || 0);
        }
        if (tokensInputEl) {
            tokensInputEl.textContent = this.formatNumber(metrics.tokens?.input_total || 0);
        }
        if (tokensOutputEl) {
            tokensOutputEl.textContent = this.formatNumber(metrics.tokens?.output_total || 0);
        }
        
        // Update cost display
        const costEl = document.getElementById('stat-cost');
        if (costEl) {
            costEl.textContent = `$${(metrics.estimated_cost || 0).toFixed(4)}`;
        }
        
        // Update cost breakdown
        document.getElementById('cost-input').textContent = `$${(metrics.cost_breakdown?.input_cost || 0).toFixed(4)}`;
        document.getElementById('cost-output').textContent = `$${(metrics.cost_breakdown?.output_cost || 0).toFixed(4)}`;
        document.getElementById('cost-total').textContent = `$${(metrics.estimated_cost || 0).toFixed(4)}`;
        
        // Update token breakdown chart
        if (this.charts.tokenBreakdown) {
            this.charts.tokenBreakdown.data.datasets[0].data = [
                metrics.tokens?.input_total || 0,
                metrics.tokens?.output_total || 0,
                metrics.tokens?.cached_total || 0
            ];
            this.charts.tokenBreakdown.update('none');
            
            document.getElementById('breakdown-input').textContent = this.formatNumber(metrics.tokens?.input_total || 0);
            document.getElementById('breakdown-output').textContent = this.formatNumber(metrics.tokens?.output_total || 0);
            document.getElementById('breakdown-cached').textContent = this.formatNumber(metrics.tokens?.cached_total || 0);
        }
        
        // Update cost chart
        if (this.charts.cost) {
            this.charts.cost.data.datasets[0].data = [
                metrics.cost_breakdown?.input_cost || 0,
                metrics.cost_breakdown?.output_cost || 0,
                metrics.cost_breakdown?.cached_cost || 0
            ];
            this.charts.cost.update('none');
        }
        
        // Update latency histogram
        if (this.charts.latencyHist && metrics.latency_histogram) {
            const histData = [
                metrics.latency_histogram['0-100ms'] || 0,
                metrics.latency_histogram['100-250ms'] || 0,
                metrics.latency_histogram['250-500ms'] || 0,
                metrics.latency_histogram['500-1000ms'] || 0,
                metrics.latency_histogram['1000-2500ms'] || 0,
                metrics.latency_histogram['2500ms+'] || 0
            ];
            this.charts.latencyHist.data.datasets[0].data = histData;
            this.charts.latencyHist.update('none');
        }
        
        // Update response time stats
        document.getElementById('min-response').textContent = `${metrics.response_time_min || 0}ms`;
        document.getElementById('p50-response').textContent = `${metrics.response_time_p50 || 0}ms`;
        document.getElementById('p95-response').textContent = `${metrics.response_time_p95 || 0}ms`;
        document.getElementById('max-response').textContent = `${metrics.response_time_max || 0}ms`;
        document.getElementById('avg-response').textContent = `${metrics.response_time_avg || 0}ms`;
        
        // Update request stats
        document.getElementById('stat-requests').textContent = metrics.requests_per_minute?.toFixed(1) || '0';
        
        // Update success/error stats
        const totalRequests = metrics.requests_total || 0;
        const successRate = totalRequests > 0 
            ? ((metrics.successful_requests || 0) / totalRequests * 100).toFixed(1) 
            : '100.0';
        document.getElementById('success-rate').textContent = `${successRate}%`;
        document.getElementById('error-count').textContent = metrics.error_count || 0;
    }
    
    // =========================================================================
    // Theme Management
    // =========================================================================
    
    initTheme() {
        const savedTheme = localStorage.getItem('progenitor_theme') || 'dark';
        this.applyTheme(savedTheme);
    }
    
    applyTheme(theme) {
        const root = document.documentElement;
        if (theme === 'system') {
            const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            root.dataset.theme = prefersDark ? 'dark' : 'light';
        } else {
            root.dataset.theme = theme;
        }
        
        const themeBtn = document.getElementById('btn-theme');
        if (themeBtn) {
            themeBtn.textContent = root.dataset.theme === 'dark' ? '🌙' : '☀️';
        }
        
        // Update chart colors
        this.updateChartColors();
    }
    
    toggleTheme() {
        const root = document.documentElement;
        const current = root.dataset.theme;
        const newTheme = current === 'dark' ? 'light' : 'dark';
        this.applyTheme(newTheme);
        localStorage.setItem('progenitor_theme', newTheme);
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
        
        console.log('SSE: Connecting to', url);
        this.updateConnectionStatus('connecting');
        
        try {
            this.eventSource = new EventSource(url);
            
            this.eventSource.onopen = () => {
                console.log('SSE: Connected');
                this.isConnected = true;
                this.updateConnectionStatus('connected');
                this.addActivity('info', 'Connected to server');
            };
            
            this.eventSource.addEventListener('connected', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleStatusUpdate(data);
                } catch (e) {
                    console.error('SSE: Failed to parse connected event', e);
                }
            });
            
            this.eventSource.addEventListener('status', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleStatusUpdate(data);
                } catch (e) {
                    console.error('SSE: Failed to parse status event', e);
                }
            });
            
            this.eventSource.addEventListener('heartbeat', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleStatusUpdate(data);
                } catch (e) {
                    console.error('SSE: Failed to parse heartbeat', e);
                }
            });
            
            this.eventSource.addEventListener('alert', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleAlert(data);
                } catch (e) {
                    console.error('SSE: Failed to parse alert', e);
                }
            });
            
            this.eventSource.onerror = () => {
                console.warn('SSE: Connection error');
                this.isConnected = false;
                this.updateConnectionStatus('disconnected');
                this.addActivity('error', 'Disconnected from server');
                
                // Reconnect after delay
                setTimeout(() => {
                    if (!this.isConnected) {
                        this.connectSSE();
                    }
                }, 5000);
            };
            
        } catch (e) {
            console.error('SSE: Failed to create EventSource', e);
            this.updateConnectionStatus('disconnected');
        }
    }
    
    updateConnectionStatus(status) {
        const indicator = document.getElementById('connection-status');
        const pulseIndicator = document.getElementById('pulse-indicator');
        
        if (indicator) {
            indicator.dataset.status = status;
            const statusText = indicator.querySelector('.status-text');
            if (statusText) {
                statusText.textContent = status === 'connected' ? 'Connected' :
                                         status === 'connecting' ? 'Connecting...' :
                                         'Disconnected';
            }
        }
        
        if (pulseIndicator) {
            pulseIndicator.classList.toggle('disconnected', status !== 'connected');
        }
    }
    
    // =========================================================================
    // Status Updates
    // =========================================================================
    
    handleStatusUpdate(data) {
        this.lastStatus = data;
        
        // Update stats cards
        this.updateStatsCards(data);
        
        // Update agents
        this.updateAgentsList(data.agents || []);
        
        // Update build status
        this.updateBuildStatus(data.build);
        
        // Update backlog
        this.updateBacklogList(data.backlog || []);
        
        // Update schedule
        this.updateScheduleList(data.schedule || []);
        
        // Update metrics history
        this.updateMetricsHistory(data);
        
        // Update charts
        this.updateCharts();
        
        // Update sparklines
        this.updateSparklinesFromStatus(data);
        
        // Update footer
        this.updateFooter(data);
        
        // Update last update time
        document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
    }
    
    updateStatsCards(data) {
        // Status
        const statusEl = document.getElementById('stat-status');
        const statusIndicator = document.getElementById('stat-status-indicator');
        if (statusEl) {
            const running = data.progenitor_running;
            statusEl.textContent = running ? 'Running' : 'Stopped';
            if (statusIndicator) {
                statusIndicator.className = `stat-indicator ${running ? '' : 'error'}`;
            }
        }
        
        // Cycles
        const cyclesEl = document.getElementById('stat-cycles');
        if (cyclesEl) {
            cyclesEl.textContent = this.formatNumber(data.cycle_count || 0);
        }
        
        // Messages
        const messagesEl = document.getElementById('stat-messages');
        if (messagesEl) {
            messagesEl.textContent = this.formatNumber(data.messages_count || 0);
        }
        
        // Tokens (from our tracking)
        const tokensEl = document.getElementById('stat-tokens');
        if (tokensEl) {
            const total = this.tokenStats.totalInput + this.tokenStats.totalOutput;
            tokensEl.textContent = this.formatNumber(total);
        }
        
        // Uptime
        const uptimeEl = document.getElementById('stat-uptime');
        if (uptimeEl) {
            uptimeEl.textContent = this.formatUptime(data.uptime_seconds || 0);
        }
        
        // Memory
        const memoryEl = document.getElementById('stat-memory');
        const memoryBar = document.getElementById('memory-bar');
        const memoryPercent = document.getElementById('memory-percent');
        if (memoryEl && data.memory_mb) {
            memoryEl.textContent = `${Math.round(data.memory_mb)} MB`;
            
            // Assuming 1GB max for percentage
            const percent = Math.min(100, (data.memory_mb / 1024) * 100);
            if (memoryBar) {
                memoryBar.style.width = `${percent}%`;
                if (percent > 80) memoryBar.classList.add('warning');
                else memoryBar.classList.remove('warning');
            }
            if (memoryPercent) {
                memoryPercent.textContent = `${Math.round(percent)}%`;
            }
        }
    }
    
    updateAgentsList(agents) {
        const container = document.getElementById('agents-list');
        const countBadge = document.getElementById('agents-count');
        
        if (countBadge) {
            countBadge.textContent = agents.length;
        }
        
        if (!container) return;
        
        if (agents.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <span class="empty-icon">🤖</span>
                    <span>No agents registered</span>
                </div>
            `;
            return;
        }
        
        container.innerHTML = agents.map(agent => `
            <div class="agent-card" data-agent="${this.escapeHtml(agent.name)}" onclick="dashboard.showAgentDetail('${this.escapeHtml(agent.name)}')">
                <div class="agent-avatar">${this.getAgentIcon(agent.kind)}</div>
                <div class="agent-info">
                    <div class="agent-name">${this.escapeHtml(agent.name)}</div>
                    <div class="agent-description">${this.escapeHtml(agent.description || 'No description')}</div>
                </div>
                <div class="agent-status">
                    <span class="agent-status-dot ${agent.status}"></span>
                    <span>${agent.status}</span>
                </div>
            </div>
        `).join('');
    }
    
    updateBuildStatus(build) {
        const container = document.getElementById('build-status');
        if (!container) return;
        
        if (!build || !build.in_progress) {
            container.innerHTML = `
                <div class="build-idle">
                    <span class="build-icon">✅</span>
                    <span>No build in progress</span>
                </div>
            `;
            return;
        }
        
        const phaseIcon = this.getBuildPhaseIcon(build.phase);
        const scorePercent = Math.round((build.score || 0) * 100);
        
        container.innerHTML = `
            <div class="build-active">
                <div class="build-phase">
                    <span class="phase-icon">${phaseIcon}</span>
                    <span class="phase-label">${this.capitalize(build.phase || 'Building')}</span>
                </div>
                <div class="build-task">${this.escapeHtml(build.task_description || 'Working...')}</div>
                <div class="build-progress">
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${scorePercent}%"></div>
                    </div>
                    <span>${scorePercent}%</span>
                </div>
                <div class="build-meta">
                    <span>Iteration ${build.iteration || 0}</span>
                    <span>${build.message_count || 0} messages</span>
                </div>
            </div>
        `;
    }
    
    updateBacklogList(backlog) {
        const container = document.getElementById('backlog-list');
        if (!container) return;
        
        if (backlog.length === 0) {
            container.innerHTML = '<div class="empty-state small">No items</div>';
            return;
        }
        
        container.innerHTML = backlog.slice(0, 5).map(item => `
            <div class="backlog-item">
                <div class="backlog-header">
                    <span class="backlog-icon">${this.getBacklogIcon(item.type)}</span>
                    <span class="backlog-type">${this.escapeHtml(item.type)}</span>
                    <span class="backlog-status ${item.status}">${item.status}</span>
                </div>
                <div class="backlog-description">${this.escapeHtml(item.description)}</div>
            </div>
        `).join('');
    }
    
    updateScheduleList(schedule) {
        const container = document.getElementById('schedule-list');
        if (!container) return;
        
        if (schedule.length === 0) {
            container.innerHTML = '<div class="empty-state small">No scheduled tasks</div>';
            return;
        }
        
        container.innerHTML = schedule.slice(0, 5).map(item => `
            <div class="schedule-item">
                <div class="schedule-header">
                    <span class="schedule-name">${this.escapeHtml(item.name)}</span>
                    <span class="schedule-badge">${item.enabled ? '✓' : '✗'}</span>
                </div>
                <div class="schedule-cron">${this.escapeHtml(item.schedule)}</div>
            </div>
        `).join('');
    }
    
    updateFooter(data) {
        const versionEl = document.getElementById('version');
        const pidEl = document.getElementById('pid');
        const runIdEl = document.getElementById('run-id');
        
        if (versionEl) versionEl.textContent = data.version || '0.1.0';
        if (pidEl) pidEl.textContent = data.pid || '--';
        if (runIdEl) runIdEl.textContent = data.run_id ? data.run_id.substring(0, 8) : '--';
    }
    
    // =========================================================================
    // Charts
    // =========================================================================
    
    initCharts() {
        // Token consumption chart
        const tokenCtx = document.getElementById('token-chart')?.getContext('2d');
        if (tokenCtx) {
            this.charts.tokens = new Chart(tokenCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: 'Input Tokens',
                            data: [],
                            borderColor: '#58a6ff',
                            backgroundColor: 'rgba(88, 166, 255, 0.1)',
                            fill: true,
                            tension: 0.4
                        },
                        {
                            label: 'Output Tokens',
                            data: [],
                            borderColor: '#3fb950',
                            backgroundColor: 'rgba(63, 185, 80, 0.1)',
                            fill: true,
                            tension: 0.4
                        },
                        {
                            label: 'Cached',
                            data: [],
                            borderColor: '#a371f7',
                            backgroundColor: 'rgba(163, 113, 247, 0.1)',
                            fill: true,
                            tension: 0.4
                        },
                        {
                            label: 'Total',
                            data: [],
                            borderColor: '#d29922',
                            backgroundColor: 'rgba(210, 153, 34, 0.1)',
                            fill: false,
                            tension: 0.4,
                            borderDash: [5, 5]
                        }
                    ]
                },
                options: this.getChartOptions('Tokens')
            });
        }
        
        // Latency histogram chart
        const latencyHistCtx = document.getElementById('latency-histogram-chart')?.getContext('2d');
        if (latencyHistCtx) {
            this.charts.latencyHist = new Chart(latencyHistCtx, {
                type: 'bar',
                data: {
                    labels: ['0-100ms', '100-250ms', '250-500ms', '500-1000ms', '1000-2500ms', '2500ms+'],
                    datasets: [{
                        label: 'Requests',
                        data: [0, 0, 0, 0, 0, 0],
                        backgroundColor: [
                            'rgba(63, 185, 80, 0.8)',   // Green - fast
                            'rgba(88, 166, 255, 0.8)',  // Blue
                            'rgba(210, 153, 34, 0.8)',  // Yellow
                            'rgba(218, 130, 47, 0.8)',  // Orange
                            'rgba(248, 81, 73, 0.8)',   // Red - slow
                            'rgba(163, 113, 247, 0.8)', // Purple - very slow
                        ],
                        borderRadius: 4
                    }]
                },
                options: {
                    ...this.getChartOptions('Requests'),
                    plugins: {
                        legend: { display: false }
                    },
                    scales: {
                        x: {
                            grid: { display: false }
                        }
                    }
                }
            });
        }
        
        // Token breakdown doughnut chart
        const tokenBreakdownCtx = document.getElementById('token-breakdown-chart')?.getContext('2d');
        if (tokenBreakdownCtx) {
            this.charts.tokenBreakdown = new Chart(tokenBreakdownCtx, {
                type: 'doughnut',
                data: {
                    labels: ['Input', 'Output', 'Cached'],
                    datasets: [{
                        data: [0, 0, 0],
                        backgroundColor: ['#58a6ff', '#3fb950', '#a371f7'],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '65%',
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        }
        
        // Response time chart
        const responseCtx = document.getElementById('response-chart')?.getContext('2d');
        if (responseCtx) {
            this.charts.response = new Chart(responseCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Response Time (ms)',
                        data: [],
                        borderColor: '#58a6ff',
                        backgroundColor: 'rgba(88, 166, 255, 0.1)',
                        fill: true,
                        tension: 0.4,
                        pointRadius: 2,
                        pointHoverRadius: 5
                    }]
                },
                options: {
                    ...this.getChartOptions('ms'),
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        }
        
        // Cost breakdown chart
        const costCtx = document.getElementById('cost-chart')?.getContext('2d');
        if (costCtx) {
            this.charts.cost = new Chart(costCtx, {
                type: 'doughnut',
                data: {
                    labels: ['Input Cost', 'Output Cost', 'Cached Cost'],
                    datasets: [{
                        data: [0, 0, 0],
                        backgroundColor: ['#58a6ff', '#3fb950', '#a371f7'],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '65%',
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        }
        
        // Resources chart (doughnut)
        const resourcesCtx = document.getElementById('resources-chart')?.getContext('2d');
        if (resourcesCtx) {
            this.charts.resources = new Chart(resourcesCtx, {
                type: 'doughnut',
                data: {
                    labels: ['Used Memory', 'Available'],
                    datasets: [{
                        data: [0, 100],
                        backgroundColor: ['#58a6ff', 'rgba(48, 54, 61, 0.5)'],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '70%',
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        }
    }
    
    getChartOptions(yLabel) {
        const isDark = document.documentElement.dataset.theme === 'dark';
        const gridColor = isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)';
        const textColor = isDark ? '#8b949e' : '#656d76';
        
        return {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            },
            scales: {
                x: {
                    grid: { color: gridColor },
                    ticks: { color: textColor, maxTicksLimit: 8 }
                },
                y: {
                    grid: { color: gridColor },
                    ticks: { color: textColor },
                    beginAtZero: true
                }
            },
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    backgroundColor: isDark ? '#21262d' : '#ffffff',
                    titleColor: isDark ? '#e6edf3' : '#1f2328',
                    bodyColor: isDark ? '#8b949e' : '#656d76',
                    borderColor: isDark ? '#30363d' : '#d0d7de',
                    borderWidth: 1
                }
            }
        };
    }
    
    updateChartColors() {
        // Update chart colors when theme changes
        Object.values(this.charts).forEach(chart => {
            if (chart && chart.options) {
                const opts = this.getChartOptions('');
                chart.options.scales = opts.scales;
                chart.options.plugins.tooltip = opts.plugins.tooltip;
                chart.update();
            }
        });
    }
    
    updateMetricsHistory(data) {
        const now = new Date();
        const timestamp = now.toLocaleTimeString();
        
        // Keep last 60 data points (1 hour at 1 min intervals)
        const maxPoints = 60;
        
        // Get current token values
        const inputTokens = this.tokenStats.totalInput;
        const outputTokens = this.tokenStats.totalOutput;
        const cachedTokens = this.tokenStats.totalCached || 0;
        
        // Track incremental changes for the chart
        const lastInput = this.metricsHistory.tokens.input.slice(-1)[0] || 0;
        const lastOutput = this.metricsHistory.tokens.output.slice(-1)[0] || 0;
        const lastCached = this.metricsHistory.tokens.cached?.slice(-1)[0] || 0;
        
        // Only add new data point if values changed
        if (inputTokens !== lastInput || outputTokens !== lastOutput) {
            this.metricsHistory.tokens.input.push(inputTokens);
            this.metricsHistory.tokens.output.push(outputTokens);
            if (!this.metricsHistory.tokens.cached) {
                this.metricsHistory.tokens.cached = [];
            }
            this.metricsHistory.tokens.cached.push(cachedTokens);
            this.metricsHistory.tokens.total.push(inputTokens + outputTokens);
            this.metricsHistory.tokens.timestamps.push(timestamp);
        }
        
        // Trim to max points
        if (this.metricsHistory.tokens.timestamps.length > maxPoints) {
            this.metricsHistory.tokens.input.shift();
            this.metricsHistory.tokens.output.shift();
            this.metricsHistory.tokens.cached?.shift();
            this.metricsHistory.tokens.total.shift();
            this.metricsHistory.tokens.timestamps.shift();
        }
        
        // Memory history
        if (data.memory_mb) {
            this.metricsHistory.memory.push(data.memory_mb);
            if (this.metricsHistory.memory.length > maxPoints) {
                this.metricsHistory.memory.shift();
            }
        }
        
        // Persist data
        this.persistData();
    }
    
    updateCharts() {
        // Update token chart
        if (this.charts.tokens) {
            this.charts.tokens.data.labels = this.metricsHistory.tokens.timestamps;
            this.charts.tokens.data.datasets[0].data = this.metricsHistory.tokens.input;
            this.charts.tokens.data.datasets[1].data = this.metricsHistory.tokens.output;
            this.charts.tokens.data.datasets[2].data = this.metricsHistory.tokens.cached || [];
            this.charts.tokens.data.datasets[3].data = this.metricsHistory.tokens.total;
            this.charts.tokens.update('none');
        }
        
        // Update response chart
        if (this.charts.response) {
            this.charts.response.data.labels = this.metricsHistory.timestamps.slice(-20);
            this.charts.response.data.datasets[0].data = this.metricsHistory.responseTimes;
            this.charts.response.update('none');
        }
        
        // Update resources chart
        if (this.charts.resources && this.lastStatus?.memory_mb) {
            const memPercent = Math.min(100, (this.lastStatus.memory_mb / 1024) * 100);
            this.charts.resources.data.datasets[0].data = [memPercent, 100 - memPercent];
            this.charts.resources.update('none');
        }
    }
    
    // =========================================================================
    // Activity Timeline
    // =========================================================================
    
    addActivity(type, message) {
        const activity = {
            id: Date.now(),
            type,
            message,
            timestamp: new Date()
        };
        
        this.activities.unshift(activity);
        
        if (this.activities.length > this.maxActivities) {
            this.activities.pop();
        }
        
        this.renderActivities();
    }
    
    renderActivities() {
        const container = document.getElementById('activity-timeline');
        if (!container) return;
        
        if (this.activities.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <span class="empty-icon">📋</span>
                    <span>No activity yet</span>
                </div>
            `;
            return;
        }
        
        container.innerHTML = this.activities.slice(0, 20).map(activity => `
            <div class="timeline-item">
                <div class="timeline-icon ${activity.type}">${this.getActivityIcon(activity.type)}</div>
                <div class="timeline-content">
                    <div class="timeline-title">${this.escapeHtml(activity.message)}</div>
                    <div class="timeline-time">${activity.timestamp.toLocaleTimeString()}</div>
                </div>
            </div>
        `).join('');
    }
    
    clearActivities() {
        this.activities = [];
        this.renderActivities();
    }
    
    // =========================================================================
    // Logs
    // =========================================================================
    
    addLog(level, message, data = null) {
        if (this.isPaused) return;
        
        const entry = {
            id: Date.now() + Math.random().toString(36).substr(2, 9),
            timestamp: new Date(),
            level: level.toLowerCase(),
            message,
            data
        };
        
        this.logs.push(entry);
        
        if (this.logs.length > this.maxLogs) {
            this.logs.shift();
        }
        
        this.renderLogs();
    }
    
    renderLogs() {
        const container = document.getElementById('logs-container');
        const countEl = document.getElementById('logs-count');
        
        if (!container) return;
        
        // Filter logs
        const filtered = this.logs.filter(log => {
            if (this.logFilter.level !== 'all' && log.level !== this.logFilter.level) {
                return false;
            }
            if (this.logFilter.search) {
                const text = `${log.message} ${JSON.stringify(log.data || '')}`.toLowerCase();
                if (!text.includes(this.logFilter.search.toLowerCase())) {
                    return false;
                }
            }
            return true;
        });
        
        if (countEl) {
            countEl.textContent = `${filtered.length} entries`;
        }
        
        if (filtered.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <span class="empty-icon">📜</span>
                    <span>Waiting for logs...</span>
                </div>
            `;
            return;
        }
        
        container.innerHTML = filtered.slice(-100).map(log => `
            <div class="log-entry">
                <span class="log-time">${log.timestamp.toLocaleTimeString()}</span>
                <span class="log-level ${log.level}">${log.level.toUpperCase()}</span>
                <span class="log-message">${this.escapeHtml(log.message)}</span>
            </div>
        `).join('');
        
        // Auto-scroll to bottom
        container.scrollTop = container.scrollHeight;
    }
    
    clearLogs() {
        this.logs = [];
        this.renderLogs();
    }
    
    exportLogs() {
        const content = this.logs.map(log => 
            `[${log.timestamp.toISOString()}] [${log.level.toUpperCase()}] ${log.message}`
        ).join('\n');
        
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
    // Agent Detail Modal
    // =========================================================================
    
    async showAgentDetail(agentName) {
        const agent = this.lastStatus?.agents?.find(a => a.name === agentName);
        if (!agent) return;
        
        const modal = document.getElementById('agent-modal');
        const title = document.getElementById('agent-modal-title');
        const body = document.getElementById('agent-modal-body');
        
        if (!modal || !body) return;
        
        title.textContent = agent.name;
        
        // Show loading state first
        body.innerHTML = `
            <div class="agent-detail-loading">
                <div class="loading-spinner"></div>
                <span>Loading agent details...</span>
            </div>
        `;
        
        modal.classList.remove('hidden');
        modal.classList.add('visible');
        
        // Fetch detailed agent data from API
        try {
            const baseUrl = window.Config?.api?.baseUrl || 'http://localhost:8000';
            const response = await fetch(`${baseUrl}/api/ui/agents/${encodeURIComponent(agent.name)}`);
            
            let agentDetail = agent;
            if (response.ok) {
                agentDetail = await response.json();
            }
            
            // Render full agent detail
            body.innerHTML = `
                <div class="agent-detail-header">
                    <div class="agent-detail-avatar">${this.getAgentIcon(agentDetail.kind)}</div>
                    <div class="agent-detail-info">
                        <h4>${this.escapeHtml(agentDetail.name)}</h4>
                        <p>${this.escapeHtml(agentDetail.description || 'No description available')}</p>
                        <div class="agent-meta-badges">
                            <span class="meta-badge transport">${this.escapeHtml(agentDetail.transport || 'stdio')}</span>
                            <span class="meta-badge version">v${this.escapeHtml(agentDetail.version || '0.1.0')}</span>
                            <span class="meta-badge status ${agentDetail.status}">${this.capitalize(agentDetail.status)}</span>
                        </div>
                    </div>
                </div>
                
                <div class="agent-stats">
                    <div class="agent-stat">
                        <div class="agent-stat-value">${agentDetail.total_requests || 0}</div>
                        <div class="agent-stat-label">Total Requests</div>
                    </div>
                    <div class="agent-stat">
                        <div class="agent-stat-value success">${agentDetail.success_rate?.toFixed(1) || 100}%</div>
                        <div class="agent-stat-label">Success Rate</div>
                    </div>
                    <div class="agent-stat">
                        <div class="agent-stat-value">${agentDetail.avg_response_time?.toFixed(0) || 0}ms</div>
                        <div class="agent-stat-label">Avg Response</div>
                    </div>
                </div>
                
                <div class="agent-stats">
                    <div class="agent-stat">
                        <div class="agent-stat-value token-input">${this.formatNumber(agentDetail.total_tokens_input || 0)}</div>
                        <div class="agent-stat-label">Input Tokens</div>
                    </div>
                    <div class="agent-stat">
                        <div class="agent-stat-value token-output">${this.formatNumber(agentDetail.total_tokens_output || 0)}</div>
                        <div class="agent-stat-label">Output Tokens</div>
                    </div>
                    <div class="agent-stat">
                        <div class="agent-stat-value">${agentDetail.tokens_per_request_avg?.toFixed(0) || 0}</div>
                        <div class="agent-stat-label">Tokens/Request</div>
                    </div>
                </div>

                ${agentDetail.last_active ? `
                    <div class="agent-last-active">
                        <span class="label">Last Active:</span>
                        <span class="value">${new Date(agentDetail.last_active).toLocaleString()}</span>
                    </div>
                ` : ''}

                ${agentDetail.recent_requests && agentDetail.recent_requests.length > 0 ? `
                    <h4>Recent Activity</h4>
                    <div class="agent-activity-list">
                        ${agentDetail.recent_requests.slice(-5).reverse().map(req => `
                            <div class="activity-item ${req.is_error ? 'error' : 'success'}">
                                <span class="activity-time">${new Date(req.timestamp * 1000).toLocaleTimeString()}</span>
                                <span class="activity-tokens">↓${req.input_tokens} ↑${req.output_tokens}</span>
                                <span class="activity-latency">${req.response_time_ms?.toFixed(0) || 0}ms</span>
                            </div>
                        `).join('')}
                    </div>
                ` : ''}
                
                <h4>Configuration</h4>
                <div class="agent-log">
                    <pre>${JSON.stringify(agentDetail, null, 2)}</pre>
                </div>
            `;
        } catch (e) {
            console.error('Failed to fetch agent details:', e);
            // Fall back to basic display
            body.innerHTML = `
                <div class="agent-detail-header">
                    <div class="agent-detail-avatar">${this.getAgentIcon(agent.kind)}</div>
                    <div class="agent-detail-info">
                        <h4>${this.escapeHtml(agent.name)}</h4>
                        <p>${this.escapeHtml(agent.description || 'No description available')}</p>
                    </div>
                </div>
                
                <div class="agent-stats">
                    <div class="agent-stat">
                        <div class="agent-stat-value">${agent.version}</div>
                        <div class="agent-stat-label">Version</div>
                    </div>
                    <div class="agent-stat">
                        <div class="agent-stat-value">${this.capitalize(agent.status)}</div>
                        <div class="agent-stat-label">Status</div>
                    </div>
                    <div class="agent-stat">
                        <div class="agent-stat-value">${this.capitalize(agent.kind)}</div>
                        <div class="agent-stat-label">Type</div>
                    </div>
                </div>
                
                <h4>Configuration</h4>
                <div class="agent-log">
                    <pre>${JSON.stringify(agent, null, 2)}</pre>
                </div>
            `;
        }
    }
    
    closeAgentModal() {
        const modal = document.getElementById('agent-modal');
        if (modal) {
            modal.classList.remove('visible');
            modal.classList.add('hidden');
        }
    }
    
    // =========================================================================
    // Alerts
    // =========================================================================
    
    handleAlert(alert) {
        this.addActivity(alert.type || 'warning', alert.title + ': ' + alert.message);
        this.addLog('warn', `[ALERT] ${alert.title}: ${alert.message}`);
    }
    
    // =========================================================================
    // Event Bindings
    // =========================================================================
    
    bindEvents() {
        // Theme toggle
        document.getElementById('btn-theme')?.addEventListener('click', () => this.toggleTheme());
        
        // Refresh
        document.getElementById('btn-refresh')?.addEventListener('click', () => this.refresh());
        
        // Clear activity
        document.getElementById('btn-clear-activity')?.addEventListener('click', () => this.clearActivities());
        
        // Logs controls
        document.getElementById('log-level-filter')?.addEventListener('change', (e) => {
            this.logFilter.level = e.target.value;
            this.renderLogs();
        });
        
        document.getElementById('log-search')?.addEventListener('input', (e) => {
            this.logFilter.search = e.target.value;
            this.renderLogs();
        });
        
        document.getElementById('btn-pause-logs')?.addEventListener('click', (e) => {
            this.isPaused = !this.isPaused;
            e.target.textContent = this.isPaused ? '▶️' : '⏸️';
            document.getElementById('logs-status').textContent = this.isPaused ? 'Paused' : 'Live';
        });
        
        document.getElementById('btn-clear-logs')?.addEventListener('click', () => this.clearLogs());
        document.getElementById('btn-export-logs')?.addEventListener('click', () => this.exportLogs());
        
        // Agent modal
        document.getElementById('agent-modal-close')?.addEventListener('click', () => this.closeAgentModal());
        document.querySelector('#agent-modal .modal-backdrop')?.addEventListener('click', () => this.closeAgentModal());
        
        // Chart range buttons
        document.querySelectorAll('.chart-btn[data-range]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.chart-btn[data-range]').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                // In a full implementation, this would filter the chart data by time range
            });
        });
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.closeAgentModal();
            }
        });
    }
    
    // =========================================================================
    // Periodic Updates
    // =========================================================================
    
    startPeriodicUpdates() {
        // Fetch metrics every 10 seconds
        setInterval(() => {
            if (this.isConnected) {
                this.fetchMetrics();
            }
        }, 10000);
        
        // Fetch real logs periodically
        setInterval(() => {
            if (!this.isPaused && this.isConnected) {
                this.fetchLogs();
            }
        }, 5000);
    }
    
    async fetchLogs() {
        const baseUrl = window.Config?.api?.baseUrl || 'http://localhost:8000';
        
        try {
            const response = await fetch(`${baseUrl}/api/ui/logs?format=json&limit=50`);
            if (!response.ok) return;
            
            const data = await response.json();
            if (data.entries && data.entries.length > 0) {
                // Add new log entries (avoiding duplicates based on timestamp+message)
                const existingKeys = new Set(this.logs.map(l => `${l.timestamp}|${l.message}`));
                
                for (const entry of data.entries) {
                    const key = `${entry.timestamp}|${entry.message}`;
                    if (!existingKeys.has(key)) {
                        this.logs.push({
                            id: Date.now() + Math.random().toString(36).substr(2, 9),
                            timestamp: entry.timestamp ? new Date(entry.timestamp) : new Date(),
                            level: entry.level || 'info',
                            message: entry.message || '',
                        });
                        existingKeys.add(key);
                    }
                }
                
                // Keep only last maxLogs
                if (this.logs.length > this.maxLogs) {
                    this.logs = this.logs.slice(-this.maxLogs);
                }
                
                this.renderLogs();
            }
        } catch (e) {
            console.debug('Failed to fetch logs:', e);
        }
    }
    
    refresh() {
        this.addActivity('info', 'Manual refresh triggered');
        // Fetch fresh metrics
        this.fetchMetrics();
        // Reconnect SSE to get fresh data
        this.connectSSE();
    }
    
    // =========================================================================
    // Persistence
    // =========================================================================
    
    persistData() {
        try {
            localStorage.setItem('progenitor_dashboard_metrics', JSON.stringify({
                tokenStats: this.tokenStats,
                metricsHistory: this.metricsHistory
            }));
        } catch (e) {
            console.warn('Failed to persist dashboard data:', e);
        }
    }
    
    loadPersistedData() {
        try {
            const saved = localStorage.getItem('progenitor_dashboard_metrics');
            if (saved) {
                const data = JSON.parse(saved);
                if (data.tokenStats) this.tokenStats = data.tokenStats;
                if (data.metricsHistory) this.metricsHistory = data.metricsHistory;
            }
        } catch (e) {
            console.warn('Failed to load persisted dashboard data:', e);
        }
    }
    
    // =========================================================================
    // Utilities
    // =========================================================================
    
    formatNumber(num) {
        if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return num.toString();
    }
    
    formatUptime(seconds) {
        if (seconds < 60) return Math.round(seconds) + 's';
        if (seconds < 3600) return Math.floor(seconds / 60) + 'm ' + Math.round(seconds % 60) + 's';
        if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ' + Math.floor((seconds % 3600) / 60) + 'm';
        return Math.floor(seconds / 86400) + 'd ' + Math.floor((seconds % 86400) / 3600) + 'h';
    }
    
    capitalize(str) {
        if (!str) return '';
        return str.charAt(0).toUpperCase() + str.slice(1);
    }
    
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    getAgentIcon(kind) {
        const icons = {
            'a2a_agent': '🤖',
            'tool': '🔧',
            'service': '⚙️',
            'llm': '🧠'
        };
        return icons[kind] || '📦';
    }
    
    getBuildPhaseIcon(phase) {
        const icons = {
            'observe': '👁️',
            'think': '🧠',
            'plan': '📋',
            'build': '🏗️',
            'execute': '⚡',
            'verify': '✅',
            'learn': '📚'
        };
        return icons[phase] || '🔄';
    }
    
    getBacklogIcon(type) {
        const icons = {
            'build_agent': '🏗️',
            'task': '📋',
            'bug': '🐛',
            'feature': '✨'
        };
        return icons[type] || '📝';
    }
    
    getActivityIcon(type) {
        const icons = {
            'success': '✓',
            'error': '✗',
            'warning': '⚠',
            'info': 'ℹ'
        };
        return icons[type] || '•';
    }
    
    // =========================================================================
    // Sparkline Rendering
    // =========================================================================
    
    initSparklines() {
        // Initialize sparkline data arrays
        this.sparklineData = {
            cycles: [],
            tokens: [],
            requests: []
        };
        
        // Max data points for sparklines
        this.maxSparklinePoints = 20;
    }
    
    updateSparkline(name, value) {
        if (!this.sparklineData) {
            this.initSparklines();
        }
        
        // Add new value
        this.sparklineData[name].push(value);
        
        // Keep only last N points
        if (this.sparklineData[name].length > this.maxSparklinePoints) {
            this.sparklineData[name].shift();
        }
        
        // Render the sparkline
        this.renderSparkline(name);
    }
    
    renderSparkline(name) {
        const container = document.getElementById(`sparkline-${name}`);
        if (!container || !this.sparklineData[name] || this.sparklineData[name].length < 2) {
            return;
        }
        
        const data = this.sparklineData[name];
        const max = Math.max(...data, 1);
        const min = Math.min(...data, 0);
        const range = max - min || 1;
        
        // Create SVG sparkline
        const width = 80;
        const height = 24;
        const points = data.map((val, i) => {
            const x = (i / (data.length - 1)) * width;
            const y = height - ((val - min) / range) * (height - 4) - 2;
            return `${x},${y}`;
        });
        
        // Get theme color
        const isDark = document.documentElement.dataset.theme === 'dark';
        const color = isDark ? '#58a6ff' : '#0969da';
        
        const svg = `
            <svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" class="sparkline-svg">
                <defs>
                    <linearGradient id="sparklineGradient-${name}" x1="0%" y1="0%" x2="0%" y2="100%">
                        <stop offset="0%" style="stop-color:${color};stop-opacity:0.3" />
                        <stop offset="100%" style="stop-color:${color};stop-opacity:0" />
                    </linearGradient>
                </defs>
                <path d="M${points[0]} ${points.join(' L')} L${width},${height} L0,${height} Z" 
                      fill="url(#sparklineGradient-${name})" />
                <polyline points="${points.join(' ')}" 
                          fill="none" 
                          stroke="${color}" 
                          stroke-width="1.5" 
                          stroke-linecap="round"
                          stroke-linejoin="round" />
                <circle cx="${points[points.length-1].split(',')[0]}" 
                        cy="${points[points.length-1].split(',')[1]}" 
                        r="2" 
                        fill="${color}" />
            </svg>
        `;
        
        container.innerHTML = svg;
    }
    
    // Update sparklines from status data
    updateSparklinesFromStatus(data) {
        if (!this.sparklineData) {
            this.initSparklines();
        }
        
        // Update cycles sparkline
        if (data.cycle_count !== undefined) {
            this.updateSparkline('cycles', data.cycle_count);
        }
        
        // Update tokens sparkline
        const totalTokens = this.tokenStats.totalInput + this.tokenStats.totalOutput;
        this.updateSparkline('tokens', totalTokens);
        
        // Update requests sparkline (simulated for now)
        const requestRate = data.requests_per_minute || Math.random() * 10;
        this.updateSparkline('requests', requestRate);
    }
}

// Initialize dashboard
let dashboard;
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        dashboard = new DashboardManager();
        window.dashboard = dashboard;
    });
} else {
    dashboard = new DashboardManager();
    window.dashboard = dashboard;
}

export { DashboardManager };
