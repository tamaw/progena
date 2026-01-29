/**
 * Progenitor Chat UI - Status Module
 * 
 * Handles status monitoring, health checks, and agent status display.
 * Now with Server-Sent Events support for real-time updates.
 */

class StatusManager {
    constructor() {
        this.connectionStatus = document.getElementById('connection-status');
        this.agentRunning = document.getElementById('agent-running');
        this.agentUptime = document.getElementById('agent-uptime');
        this.agentVersion = document.getElementById('agent-version');
        this.agentMemory = document.getElementById('agent-memory');
        this.subAgentsList = document.getElementById('sub-agents-list');
        this.activeTasks = document.getElementById('active-tasks');
        
        this.isConnected = false;
        this.healthCheckInterval = null;
        this.lastHealth = null;
        this.eventSource = null;
        
        this.init();
    }

    /**
     * Initialize status monitoring.
     */
    init() {
        // Refresh button
        const refreshBtn = document.getElementById('btn-refresh-agents');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => this.refreshStatus());
        }

        // Start monitoring
        this.startHealthCheck();
        
        // Connect to SSE for real-time updates
        this.connectEventSource();
    }

    /**
     * Connect to the Server-Sent Events endpoint.
     */
    connectEventSource() {
        if (this.eventSource) {
            this.eventSource.close();
        }

        const url = `${Config.api.baseUrl}/api/ui/events`;
        
        try {
            this.eventSource = new EventSource(url);
            
            this.eventSource.onopen = () => {
                console.log('SSE connection established');
            };

            this.eventSource.addEventListener('connected', (event) => {
                console.log('SSE connected event received');
            });

            this.eventSource.addEventListener('alert', (event) => {
                const data = JSON.parse(event.data);
                this.handleAlertEvent(data);
            });

            this.eventSource.addEventListener('status', (event) => {
                const data = JSON.parse(event.data);
                this.handleStatusEvent(data);
            });

            this.eventSource.onerror = (error) => {
                console.warn('SSE connection error:', error);
                // Will automatically reconnect
            };
        } catch (e) {
            console.warn('Failed to connect to SSE:', e);
        }
    }

    /**
     * Handle alert event from SSE.
     * @param {Object} data - Alert data
     */
    handleAlertEvent(data) {
        if (window.notifications) {
            notifications.showAlert(data.title, data.message, {
                urgent: data.urgent
            });
        }
    }

    /**
     * Handle status event from SSE.
     * @param {Object} data - Status data
     */
    handleStatusEvent(data) {
        // Trigger a status refresh on status change
        this.refreshStatus();
        
        // Show notification if significant change
        if (data.status === 'error' && window.notifications) {
            notifications.show('error', 'Status Change', data.details?.message || 'Agent status changed');
        }
    }

    /**
     * Disconnect from SSE.
     */
    disconnectEventSource() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
    }

    /**
     * Start periodic health checks.
     */
    startHealthCheck() {
        // Initial check
        this.checkHealth();

        // Periodic checks
        this.healthCheckInterval = setInterval(
            () => this.checkHealth(),
            Config.status.healthCheckInterval
        );
    }

    /**
     * Stop health checks and SSE connection.
     */
    stopHealthCheck() {
        if (this.healthCheckInterval) {
            clearInterval(this.healthCheckInterval);
            this.healthCheckInterval = null;
        }
        this.disconnectEventSource();
    }

    /**
     * Check server health.
     */
    async checkHealth() {
        try {
            const health = await api.getHealthDetailed();
            this.lastHealth = health;
            this.updateConnectionStatus(true);
            this.updateAgentStatus(health);
            this.updateSubAgents(health);
            this.updateTasks(health);
        } catch (error) {
            console.warn('Health check failed:', error);
            this.updateConnectionStatus(false, error);
        }
    }

    /**
     * Update connection status display.
     * @param {boolean} connected - Whether connected
     * @param {Error} error - Optional error
     */
    updateConnectionStatus(connected, error = null) {
        this.isConnected = connected;
        
        const status = connected ? 'connected' : 'disconnected';
        const text = connected ? 'Connected' : 'Disconnected';
        
        this.connectionStatus.dataset.status = status;
        this.connectionStatus.querySelector('.status-text').textContent = text;

        // Notify if connection state changed
        if (!connected && this.isConnected !== false) {
            if (window.notifications) {
                notifications.show('error', 'Connection Lost', 
                    error?.message || 'Unable to reach the Progenitor server');
            }
        }
    }

    /**
     * Update agent status panel.
     * @param {Object} health - Health data
     */
    updateAgentStatus(health) {
        // Status
        const statusValue = health.progenitor?.running ? 'Running' : 'Stopped';
        const statusClass = health.progenitor?.running ? 'status-healthy' : 'status-error';
        this.agentRunning.textContent = statusValue;
        this.agentRunning.className = `status-value ${statusClass}`;

        // Uptime
        if (health.uptime_seconds !== undefined) {
            this.agentUptime.textContent = this.formatUptime(health.uptime_seconds);
        }

        // Version
        if (health.version) {
            this.agentVersion.textContent = health.version;
        }

        // Memory
        if (health.memory?.rss_mb !== undefined) {
            this.agentMemory.textContent = `${health.memory.rss_mb} MB`;
        } else if (health.memory?.available === false) {
            this.agentMemory.textContent = 'N/A';
        }
    }

    /**
     * Update sub-agents list.
     * @param {Object} health - Health data
     */
    updateSubAgents(health) {
        // Get sub-agents from health data or simulate some
        const subAgents = health.sub_agents || this.getSimulatedSubAgents(health);

        if (!subAgents || subAgents.length === 0) {
            this.subAgentsList.innerHTML = '<div class="empty-state">No active sub-agents</div>';
            return;
        }

        this.subAgentsList.innerHTML = subAgents.map(agent => this.renderSubAgentCard(agent)).join('');
    }

    /**
     * Get simulated sub-agents based on health data.
     * @param {Object} health - Health data
     * @returns {Array} Simulated sub-agents
     */
    getSimulatedSubAgents(health) {
        // If the server is running, show some default sub-agents
        if (health.progenitor?.running) {
            return [
                {
                    name: 'Planner',
                    status: 'active',
                    task: 'Monitoring task queue',
                    progress: null
                },
                {
                    name: 'Executor',
                    status: 'idle',
                    task: 'Awaiting tasks',
                    progress: null
                }
            ];
        }
        return [];
    }

    /**
     * Render a sub-agent card.
     * @param {Object} agent - Agent data
     * @returns {string} HTML string
     */
    renderSubAgentCard(agent) {
        const statusClass = agent.status || 'idle';
        const progressHtml = agent.progress !== null 
            ? `<div class="sub-agent-progress"><div class="sub-agent-progress-bar" style="width: ${agent.progress}%"></div></div>`
            : '';

        return `
            <div class="sub-agent-card ${statusClass}">
                <div class="sub-agent-header">
                    <span class="sub-agent-name">${this.escapeHtml(agent.name)}</span>
                    <span class="sub-agent-status">
                        <span class="sub-agent-status-dot"></span>
                        ${this.escapeHtml(agent.status || 'idle')}
                    </span>
                </div>
                <div class="sub-agent-task">${this.escapeHtml(agent.task || 'No current task')}</div>
                ${progressHtml}
            </div>
        `;
    }

    /**
     * Update active tasks list.
     * @param {Object} health - Health data
     */
    updateTasks(health) {
        const tasks = health.active_tasks || this.getSimulatedTasks(health);

        if (!tasks || tasks.length === 0) {
            this.activeTasks.innerHTML = '<div class="empty-state">No active tasks</div>';
            return;
        }

        this.activeTasks.innerHTML = tasks.map(task => this.renderTaskItem(task)).join('');
    }

    /**
     * Get simulated tasks based on health data.
     * @param {Object} health - Health data
     * @returns {Array} Simulated tasks
     */
    getSimulatedTasks(health) {
        if (health.progenitor?.running && health.shutdown?.active_requests > 0) {
            return [
                {
                    title: 'Processing request',
                    status: 'running',
                    description: 'Handling active chat request'
                }
            ];
        }
        return [];
    }

    /**
     * Render a task item.
     * @param {Object} task - Task data
     * @returns {string} HTML string
     */
    renderTaskItem(task) {
        const statusIcon = {
            pending: '⏳',
            running: '🔄',
            completed: '✅',
            failed: '❌'
        }[task.status] || '❓';

        return `
            <div class="task-item">
                <span class="task-status-icon ${task.status}">${statusIcon}</span>
                <div class="task-content">
                    <div class="task-title">${this.escapeHtml(task.title)}</div>
                    ${task.description ? `<div class="task-description">${this.escapeHtml(task.description)}</div>` : ''}
                </div>
            </div>
        `;
    }

    /**
     * Manually refresh status.
     */
    async refreshStatus() {
        const btn = document.getElementById('btn-refresh-agents');
        if (btn) {
            btn.disabled = true;
            btn.textContent = '⏳';
        }

        try {
            await this.checkHealth();
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = '🔄';
            }
        }
    }

    /**
     * Format uptime seconds to readable string.
     * @param {number} seconds - Uptime in seconds
     * @returns {string} Formatted uptime
     */
    formatUptime(seconds) {
        if (seconds < 60) {
            return `${Math.round(seconds)}s`;
        } else if (seconds < 3600) {
            const mins = Math.floor(seconds / 60);
            const secs = Math.round(seconds % 60);
            return `${mins}m ${secs}s`;
        } else if (seconds < 86400) {
            const hours = Math.floor(seconds / 3600);
            const mins = Math.floor((seconds % 3600) / 60);
            return `${hours}h ${mins}m`;
        } else {
            const days = Math.floor(seconds / 86400);
            const hours = Math.floor((seconds % 86400) / 3600);
            return `${days}d ${hours}h`;
        }
    }

    /**
     * Escape HTML special characters.
     * @param {string} text - Raw text
     * @returns {string} Escaped text
     */
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Get the last health status.
     * @returns {Object|null}
     */
    getLastHealth() {
        return this.lastHealth;
    }

    /**
     * Check if connected.
     * @returns {boolean}
     */
    isServerConnected() {
        return this.isConnected;
    }
}

// Export for use in main.js
window.StatusManager = StatusManager;
