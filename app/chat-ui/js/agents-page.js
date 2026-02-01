/**
 * Progenitor - Agents Page JavaScript
 * 
 * Provides a dedicated view for managing and monitoring all registered agents.
 * Features: Grid/list views, filtering, search, detailed agent stats with charts.
 */

class AgentsPageManager {
    constructor() {
        this.agents = [];
        this.filteredAgents = [];
        this.filter = {
            search: '',
            status: 'all'
        };
        this.viewMode = 'grid';
        this.selectedAgent = null;
        this.isConnected = false;
        this.eventSource = null;
        this.agentCharts = {};
        
        this.init();
    }
    
    init() {
        console.log('Initializing Agents Page...');
        
        this.initTheme();
        this.bindEvents();
        this.connectSSE();
        this.fetchAgents();
        
        // Refresh every 10 seconds
        setInterval(() => {
            if (this.isConnected) {
                this.fetchAgents();
            }
        }, 10000);
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
            
            this.eventSource.addEventListener('connected', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleStatusUpdate(data);
                } catch (e) {
                    console.error('Failed to parse SSE event:', e);
                }
            });
            
            this.eventSource.addEventListener('status', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleStatusUpdate(data);
                } catch (e) {
                    console.error('Failed to parse SSE event:', e);
                }
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
    
    handleStatusUpdate(data) {
        if (data.agents) {
            this.agents = data.agents;
            this.updateSummary();
            this.filterAndRender();
        }
        
        document.getElementById('version').textContent = data.version || '0.1.0';
        document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
    }
    
    // =========================================================================
    // Data Fetching
    // =========================================================================
    
    async fetchAgents() {
        const baseUrl = window.Config?.api?.baseUrl || 'http://localhost:8000';
        
        try {
            const response = await fetch(`${baseUrl}/api/ui/agents`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            
            const data = await response.json();
            this.agents = data.agents || [];
            this.updateSummary();
            this.filterAndRender();
            
            document.getElementById('agent-count').textContent = this.agents.length;
            
        } catch (e) {
            console.error('Failed to fetch agents:', e);
        }
    }
    
    async fetchAgentDetails(agentName) {
        const baseUrl = window.Config?.api?.baseUrl || 'http://localhost:8000';
        
        try {
            const response = await fetch(`${baseUrl}/api/ui/agents/${encodeURIComponent(agentName)}`);
            if (!response.ok) return null;
            
            return await response.json();
        } catch (e) {
            console.error('Failed to fetch agent details:', e);
            return null;
        }
    }
    
    // =========================================================================
    // Summary
    // =========================================================================
    
    updateSummary() {
        const total = this.agents.length;
        const ready = this.agents.filter(a => a.status === 'ready').length;
        const busy = this.agents.filter(a => a.status === 'busy').length;
        
        let totalRequests = 0;
        let totalTokens = 0;
        
        this.agents.forEach(a => {
            totalRequests += a.total_requests || 0;
            totalTokens += (a.total_tokens_input || 0) + (a.total_tokens_output || 0);
        });
        
        document.getElementById('total-agents').textContent = total;
        document.getElementById('ready-agents').textContent = ready;
        document.getElementById('busy-agents').textContent = busy;
        document.getElementById('total-requests').textContent = this.formatNumber(totalRequests);
        document.getElementById('total-tokens').textContent = this.formatNumber(totalTokens);
    }
    
    // =========================================================================
    // Filtering
    // =========================================================================
    
    filterAndRender() {
        this.filteredAgents = this.agents.filter(agent => {
            // Status filter
            if (this.filter.status !== 'all' && agent.status !== this.filter.status) {
                return false;
            }
            
            // Search filter
            if (this.filter.search) {
                const searchLower = this.filter.search.toLowerCase();
                const searchableText = `${agent.name} ${agent.description || ''}`.toLowerCase();
                if (!searchableText.includes(searchLower)) {
                    return false;
                }
            }
            
            return true;
        });
        
        this.renderAgents();
    }
    
    // =========================================================================
    // Rendering
    // =========================================================================
    
    renderAgents() {
        const container = document.getElementById('agents-container');
        if (!container) return;
        
        if (this.filteredAgents.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <span class="empty-icon">🤖</span>
                    <span>${this.agents.length === 0 ? 'No agents registered' : 'No agents match your filters'}</span>
                </div>
            `;
            return;
        }
        
        container.innerHTML = this.filteredAgents.map(agent => this.renderAgentCard(agent)).join('');
        
        // Update grid class based on view mode
        container.classList.toggle('list-view', this.viewMode === 'list');
    }
    
    renderAgentCard(agent) {
        const statusClass = agent.status === 'ready' ? 'ready' : 
                           agent.status === 'busy' ? 'busy' : 'offline';
        const icon = this.getAgentIcon(agent.kind);
        
        return `
            <div class="agent-card-large" onclick="agentsPage.openAgentDrawer('${this.escapeHtml(agent.name)}')">
                <div class="agent-card-header">
                    <div class="agent-card-avatar ${statusClass === 'offline' ? 'offline' : ''}">
                        ${icon}
                    </div>
                    <div class="agent-card-info">
                        <div class="agent-card-name">${this.escapeHtml(agent.name)}</div>
                        <div class="agent-card-description">${this.escapeHtml(agent.description || 'No description')}</div>
                    </div>
                    <div class="agent-card-status ${statusClass}">
                        <span class="status-pulse"></span>
                        ${this.capitalize(agent.status)}
                    </div>
                </div>
                <div class="agent-card-body">
                    <div class="agent-card-stats">
                        <div class="agent-stat-item">
                            <div class="agent-stat-value input">${this.formatNumber(agent.total_tokens_input || 0)}</div>
                            <div class="agent-stat-label">Input</div>
                        </div>
                        <div class="agent-stat-item">
                            <div class="agent-stat-value output">${this.formatNumber(agent.total_tokens_output || 0)}</div>
                            <div class="agent-stat-label">Output</div>
                        </div>
                        <div class="agent-stat-item">
                            <div class="agent-stat-value success">${agent.success_rate?.toFixed(0) || 100}%</div>
                            <div class="agent-stat-label">Success</div>
                        </div>
                    </div>
                    <div class="agent-sparkline" id="sparkline-${this.escapeHtml(agent.name)}">
                        <canvas class="sparkline-canvas"></canvas>
                    </div>
                </div>
                <div class="agent-card-footer">
                    <div class="agent-card-meta">
                        <span class="meta-tag transport">${this.escapeHtml(agent.transport || 'stdio')}</span>
                        <span class="meta-tag version">v${this.escapeHtml(agent.version || '0.1.0')}</span>
                    </div>
                    <button class="agent-card-action" onclick="event.stopPropagation(); agentsPage.openAgentDrawer('${this.escapeHtml(agent.name)}')">
                        View Details
                    </button>
                </div>
            </div>
        `;
    }
    
    // =========================================================================
    // Agent Drawer
    // =========================================================================
    
    async openAgentDrawer(agentName) {
        const agent = this.agents.find(a => a.name === agentName);
        if (!agent) return;
        
        this.selectedAgent = agentName;
        
        const drawer = document.getElementById('agent-drawer');
        const backdrop = document.getElementById('drawer-backdrop');
        const content = document.getElementById('drawer-content');
        
        // Set header
        document.getElementById('drawer-icon').textContent = this.getAgentIcon(agent.kind);
        document.getElementById('drawer-agent-name').textContent = agent.name;
        
        // Show loading state
        content.innerHTML = `
            <div class="loading-state">
                <div class="loading-spinner"></div>
                <span>Loading agent details...</span>
            </div>
        `;
        
        drawer.classList.remove('hidden');
        backdrop.classList.remove('hidden');
        
        // Fetch full details
        const details = await this.fetchAgentDetails(agentName) || agent;
        
        content.innerHTML = this.renderDrawerContent(details);
        
        // Initialize chart if we have data
        this.initAgentChart(agentName, details);
    }
    
    renderDrawerContent(agent) {
        const statusClass = agent.status === 'ready' ? 'ready' : 
                           agent.status === 'busy' ? 'busy' : 'offline';
        
        return `
            <div class="drawer-section">
                <div class="agent-card-status ${statusClass}" style="margin-bottom: var(--spacing-md);">
                    <span class="status-pulse"></span>
                    ${this.capitalize(agent.status)}
                </div>
                <p style="color: var(--color-text-secondary); margin: 0;">
                    ${this.escapeHtml(agent.description || 'No description available')}
                </p>
            </div>
            
            <div class="drawer-section">
                <h4 class="drawer-section-title">Statistics</h4>
                <div class="drawer-stats-grid">
                    <div class="drawer-stat">
                        <div class="drawer-stat-value">${agent.total_requests || 0}</div>
                        <div class="drawer-stat-label">Total Requests</div>
                    </div>
                    <div class="drawer-stat">
                        <div class="drawer-stat-value" style="color: var(--color-success);">${agent.success_rate?.toFixed(1) || 100}%</div>
                        <div class="drawer-stat-label">Success Rate</div>
                    </div>
                    <div class="drawer-stat">
                        <div class="drawer-stat-value" style="color: #58a6ff;">${this.formatNumber(agent.total_tokens_input || 0)}</div>
                        <div class="drawer-stat-label">Input Tokens</div>
                    </div>
                    <div class="drawer-stat">
                        <div class="drawer-stat-value" style="color: #3fb950;">${this.formatNumber(agent.total_tokens_output || 0)}</div>
                        <div class="drawer-stat-label">Output Tokens</div>
                    </div>
                    <div class="drawer-stat">
                        <div class="drawer-stat-value">${agent.avg_response_time?.toFixed(0) || 0}ms</div>
                        <div class="drawer-stat-label">Avg Response</div>
                    </div>
                    <div class="drawer-stat">
                        <div class="drawer-stat-value">${agent.tokens_per_request_avg?.toFixed(0) || 0}</div>
                        <div class="drawer-stat-label">Tokens/Request</div>
                    </div>
                </div>
            </div>
            
            <div class="drawer-section">
                <h4 class="drawer-section-title">Token Usage Over Time</h4>
                <div class="drawer-chart">
                    <canvas id="agent-chart"></canvas>
                </div>
            </div>
            
            ${agent.last_active ? `
                <div class="drawer-section">
                    <h4 class="drawer-section-title">Last Active</h4>
                    <p style="color: var(--color-text-primary); margin: 0;">
                        ${new Date(agent.last_active).toLocaleString()}
                    </p>
                </div>
            ` : ''}
            
            ${agent.recent_requests && agent.recent_requests.length > 0 ? `
                <div class="drawer-section">
                    <h4 class="drawer-section-title">Recent Activity</h4>
                    <div class="drawer-activity-list">
                        ${agent.recent_requests.slice(-10).reverse().map(req => `
                            <div class="drawer-activity-item ${req.is_error ? 'error' : 'success'}">
                                <span class="activity-time">${new Date(req.timestamp * 1000).toLocaleTimeString()}</span>
                                <span class="activity-tokens">↓${req.input_tokens || 0} ↑${req.output_tokens || 0}</span>
                                <span class="activity-latency">${req.response_time_ms?.toFixed(0) || 0}ms</span>
                            </div>
                        `).join('')}
                    </div>
                </div>
            ` : ''}
            
            <div class="drawer-section">
                <h4 class="drawer-section-title">Configuration</h4>
                <div class="config-preview">
                    <pre>${JSON.stringify(agent, null, 2)}</pre>
                </div>
            </div>
        `;
    }
    
    initAgentChart(agentName, details) {
        const ctx = document.getElementById('agent-chart')?.getContext('2d');
        if (!ctx) return;
        
        // Generate sample data (in real implementation, this would come from the API)
        const labels = [];
        const inputData = [];
        const outputData = [];
        
        for (let i = 9; i >= 0; i--) {
            const date = new Date();
            date.setMinutes(date.getMinutes() - i * 5);
            labels.push(date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
            inputData.push(Math.floor(Math.random() * 1000) + 500);
            outputData.push(Math.floor(Math.random() * 500) + 200);
        }
        
        if (this.agentCharts[agentName]) {
            this.agentCharts[agentName].destroy();
        }
        
        this.agentCharts[agentName] = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Input Tokens',
                        data: inputData,
                        borderColor: '#58a6ff',
                        backgroundColor: 'rgba(88, 166, 255, 0.1)',
                        fill: true,
                        tension: 0.4
                    },
                    {
                        label: 'Output Tokens',
                        data: outputData,
                        borderColor: '#3fb950',
                        backgroundColor: 'rgba(63, 185, 80, 0.1)',
                        fill: true,
                        tension: 0.4
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: true,
                        position: 'bottom',
                        labels: {
                            color: '#8b949e',
                            boxWidth: 12,
                            padding: 10
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255,255,255,0.1)' },
                        ticks: { color: '#8b949e', maxTicksLimit: 5 }
                    },
                    y: {
                        grid: { color: 'rgba(255,255,255,0.1)' },
                        ticks: { color: '#8b949e' },
                        beginAtZero: true
                    }
                }
            }
        });
    }
    
    closeAgentDrawer() {
        const drawer = document.getElementById('agent-drawer');
        const backdrop = document.getElementById('drawer-backdrop');
        
        drawer.classList.add('hidden');
        backdrop.classList.add('hidden');
        this.selectedAgent = null;
    }
    
    // =========================================================================
    // Event Bindings
    // =========================================================================
    
    bindEvents() {
        // Theme toggle
        document.getElementById('btn-theme')?.addEventListener('click', () => this.toggleTheme());
        
        // Refresh
        document.getElementById('btn-refresh')?.addEventListener('click', () => this.fetchAgents());
        
        // Search
        document.getElementById('agent-search')?.addEventListener('input', (e) => {
            this.filter.search = e.target.value;
            this.filterAndRender();
        });
        
        // Filter buttons
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                this.filter.status = e.target.dataset.filter;
                this.filterAndRender();
            });
        });
        
        // View toggle
        document.querySelectorAll('.view-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                this.viewMode = e.target.dataset.view;
                this.filterAndRender();
            });
        });
        
        // Drawer close
        document.getElementById('drawer-close')?.addEventListener('click', () => this.closeAgentDrawer());
        document.getElementById('drawer-backdrop')?.addEventListener('click', () => this.closeAgentDrawer());
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.closeAgentDrawer();
            }
        });
    }
    
    // =========================================================================
    // Utilities
    // =========================================================================
    
    formatNumber(num) {
        if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return num.toString();
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
}

// Initialize
let agentsPage;
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        agentsPage = new AgentsPageManager();
        window.agentsPage = agentsPage;
    });
} else {
    agentsPage = new AgentsPageManager();
    window.agentsPage = agentsPage;
}

export { AgentsPageManager };
