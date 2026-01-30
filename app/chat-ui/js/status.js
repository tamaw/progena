/**
 * Progenitor Chat UI - Status Module
 * SSE-first architecture: Status updates are pushed via Server-Sent Events.
 *
 * Displays real data from the server:
 * - Health status from data/state/health.json
 * - Registered agents from data/state/registry.json
 * - Loops (Planner/Executor) status
 * - Backlog items
 * - Schedule
 */

class StatusManager {
    constructor() {
        // Status elements
        this.connectionStatus = document.getElementById('connection-status');
        this.agentRunning = document.getElementById('agent-running');
        this.agentUptime = document.getElementById('agent-uptime');
        this.agentVersion = document.getElementById('agent-version');
        this.agentMemory = document.getElementById('agent-memory');
        this.agentCycles = document.getElementById('agent-cycles');
        this.agentPid = document.getElementById('agent-pid');

        // List elements
        this.loopsList = document.getElementById('loops-list');
        this.agentsList = document.getElementById('agents-list');
        this.backlogList = document.getElementById('backlog-list');
        this.scheduleList = document.getElementById('schedule-list');
        this.activeTasks = document.getElementById('active-tasks');

        this.isConnected = false;
        this.eventSource = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 1000;  // Start with 1 second
        this.lastStatus = null;

        this.init();
    }

    init() {
        // Connect immediately on page load
        this.connectEventSource();
    }

    connectEventSource() {
        if (this.eventSource) {
            this.eventSource.close();
        }

        const baseUrl = (window.Config && window.Config.api.baseUrl) || 'http://localhost:8000';
        const url = baseUrl + '/api/ui/events';

        try {
            console.log('SSE: Connecting to', url);
            this.eventSource = new EventSource(url);

            this.eventSource.onopen = () => {
                console.log('SSE: Connection opened');
                this.reconnectAttempts = 0;
                this.updateConnectionStatus(true);
            };

            this.eventSource.addEventListener('connected', (event) => {
                console.log('SSE: Received connected event, data length:', event.data.length);
                try {
                    const data = JSON.parse(event.data);
                    console.log('SSE: Initial status - running:', data.progenitor_running, 'status:', data.progenitor_status);
                    this.lastStatus = data;
                    this.updateAllStatus(data);
                } catch (e) {
                    console.error('SSE: Failed to parse connected event:', e);
                }
            });

            this.eventSource.addEventListener('status', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('SSE: Status update - running:', data.progenitor_running, 'cycles:', data.cycle_count);
                    this.lastStatus = data;
                    this.updateAllStatus(data);
                } catch (e) {
                    console.error('SSE: Failed to parse status event:', e);
                }
            });

            this.eventSource.addEventListener('heartbeat', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('SSE: Heartbeat - running:', data.progenitor_running);
                    this.lastStatus = data;
                    this.updateAllStatus(data);
                } catch (e) {
                    console.error('SSE: Failed to parse heartbeat:', e);
                }
            });

            this.eventSource.addEventListener('alert', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (window.notifications) {
                        notifications.showAlert(data.title, data.message, { urgent: data.urgent });
                    }
                } catch (e) {
                    console.warn('SSE: Failed to parse alert:', e);
                }
            });

            this.eventSource.onerror = (error) => {
                console.warn('SSE: Connection error', error);
                this.handleSSEError();
            };

        } catch (e) {
            console.error('SSE: Failed to create EventSource:', e);
            this.handleSSEError();
        }
    }

    handleSSEError() {
        this.updateConnectionStatus(false);
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            const delay = Math.min(this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts - 1), 10000);
            console.log('SSE: Reconnecting in ' + Math.round(delay) + 'ms (attempt ' + this.reconnectAttempts + ')');
            setTimeout(() => {
                if (!this.isConnected) this.connectEventSource();
            }, delay);
        }
    }

    reconnect() {
        this.reconnectAttempts = 0;
        this.connectEventSource();
    }

    updateAllStatus(data) {
        this.updateAgentStatus(data);
        this.updateLoops(data);
        this.updateAgents(data);
        this.updateBacklog(data);
        this.updateSchedule(data);
    }

    updateConnectionStatus(connected) {
        this.isConnected = connected;
        if (this.connectionStatus) {
            this.connectionStatus.dataset.status = connected ? 'connected' : 'disconnected';
            const statusText = this.connectionStatus.querySelector('.status-text');
            if (statusText) statusText.textContent = connected ? 'Connected' : 'Disconnected';
        }
    }

    updateAgentStatus(data) {
        // Use progenitor_running (boolean) for the main status indicator
        // Also show progenitor_status (raw status from health.json) for debugging
        const running = data.progenitor_running;
        const rawStatus = data.progenitor_status || 'unknown';

        if (this.agentRunning) {
            // Show both the computed running state and raw status
            const displayText = running ? 'Running' : (rawStatus === 'running' ? 'Stale' : 'Stopped');
            this.agentRunning.textContent = displayText;
            this.agentRunning.className = 'status-value ' + (running ? 'status-healthy' : (rawStatus === 'running' ? 'status-warning' : 'status-error'));
            // Add title for more detail
            this.agentRunning.title = `Status: ${rawStatus}, Active: ${running}`;
        }
        if (this.agentUptime && data.uptime_seconds !== undefined) {
            this.agentUptime.textContent = this.formatUptime(data.uptime_seconds);
        }
        if (this.agentVersion && data.version) {
            this.agentVersion.textContent = data.version;
        }
        if (this.agentMemory && data.memory_mb) {
            this.agentMemory.textContent = data.memory_mb + ' MB';
        }
        if (this.agentCycles && data.cycle_count !== undefined) {
            this.agentCycles.textContent = data.cycle_count;
        }
        if (this.agentPid && data.pid) {
            this.agentPid.textContent = data.pid;
        }
    }

    updateLoops(data) {
        if (!this.loopsList) return;

        const loops = data.loops || [];

        if (loops.length === 0) {
            this.loopsList.innerHTML = '<div class="empty-state">No active loops</div>';
            return;
        }

        this.loopsList.innerHTML = loops.map(loop => {
            const statusClass = loop.status === 'active' ? 'active' : 'idle';
            return `
                <div class="sub-agent-card ${statusClass}">
                    <div class="sub-agent-header">
                        <span class="sub-agent-name">${this.escapeHtml(loop.name)}</span>
                        <span class="sub-agent-status">
                            <span class="sub-agent-status-dot"></span>
                            ${this.escapeHtml(loop.status)}
                        </span>
                    </div>
                    ${loop.current_task ? `<div class="sub-agent-task">${this.escapeHtml(loop.current_task)}</div>` : ''}
                </div>
            `;
        }).join('');
    }

    updateAgents(data) {
        if (!this.agentsList) return;

        const agents = data.agents || [];

        if (agents.length === 0) {
            this.agentsList.innerHTML = '<div class="empty-state">No registered agents</div>';
            return;
        }

        this.agentsList.innerHTML = agents.map(agent => {
            const statusClass = agent.status === 'ready' ? 'idle' : 'offline';
            const icon = this.getAgentIcon(agent.kind);
            return `
                <div class="sub-agent-card ${statusClass}" title="${this.escapeHtml(agent.description)}">
                    <div class="sub-agent-header">
                        <span class="sub-agent-name">${icon} ${this.escapeHtml(agent.name)}</span>
                        <span class="sub-agent-status">
                            <span class="sub-agent-status-dot"></span>
                            ${this.escapeHtml(agent.status)}
                        </span>
                    </div>
                    <div class="sub-agent-meta">
                        <span class="agent-version">v${this.escapeHtml(agent.version)}</span>
                        <span class="agent-transport">${this.escapeHtml(agent.transport)}</span>
                    </div>
                    ${agent.description ? `<div class="sub-agent-task">${this.truncate(agent.description, 60)}</div>` : ''}
                </div>
            `;
        }).join('');
    }

    updateBacklog(data) {
        if (!this.backlogList) return;

        const backlog = data.backlog || [];

        if (backlog.length === 0) {
            this.backlogList.innerHTML = '<div class="empty-state">No backlog items</div>';
            return;
        }

        this.backlogList.innerHTML = backlog.map(item => {
            const statusClass = this.getBacklogStatusClass(item.status);
            const icon = this.getBacklogIcon(item.type);
            return `
                <div class="backlog-item ${statusClass}">
                    <div class="backlog-header">
                        <span class="backlog-icon">${icon}</span>
                        <span class="backlog-type">${this.escapeHtml(item.type)}</span>
                        <span class="backlog-status">${this.escapeHtml(item.status)}</span>
                    </div>
                    <div class="backlog-description">${this.truncate(item.description, 80)}</div>
                </div>
            `;
        }).join('');
    }

    updateSchedule(data) {
        if (!this.scheduleList) return;

        const schedule = data.schedule || [];

        if (schedule.length === 0) {
            this.scheduleList.innerHTML = '<div class="empty-state">No scheduled tasks</div>';
            return;
        }

        this.scheduleList.innerHTML = schedule.map(item => {
            const enabledClass = item.enabled ? 'enabled' : 'disabled';
            return `
                <div class="schedule-item ${enabledClass}">
                    <div class="schedule-header">
                        <span class="schedule-name">${this.escapeHtml(item.name)}</span>
                        <span class="schedule-badge">${item.enabled ? '✓' : '✗'}</span>
                    </div>
                    <div class="schedule-cron">${this.escapeHtml(item.schedule)}</div>
                </div>
            `;
        }).join('');
    }

    getAgentIcon(kind) {
        const icons = {
            'a2a_agent': '🤖',
            'tool': '🔧',
            'service': '⚙️',
        };
        return icons[kind] || '📦';
    }

    getBacklogIcon(type) {
        const icons = {
            'build_agent': '🏗️',
            'task': '📋',
            'bug': '🐛',
            'feature': '✨',
        };
        return icons[type] || '📝';
    }

    getBacklogStatusClass(status) {
        const classes = {
            'pending': 'pending',
            'in_progress': 'active',
            'completed': 'completed',
            'failed': 'error',
        };
        return classes[status] || 'pending';
    }

    formatUptime(seconds) {
        if (seconds < 60) return Math.round(seconds) + 's';
        if (seconds < 3600) return Math.floor(seconds / 60) + 'm ' + Math.round(seconds % 60) + 's';
        if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ' + Math.floor((seconds % 3600) / 60) + 'm';
        return Math.floor(seconds / 86400) + 'd ' + Math.floor((seconds % 86400) / 3600) + 'h';
    }

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    truncate(text, maxLen) {
        if (!text) return '';
        const escaped = this.escapeHtml(text);
        if (escaped.length <= maxLen) return escaped;
        return escaped.substring(0, maxLen) + '...';
    }

    getLastStatus() { return this.lastStatus; }
    isServerConnected() { return this.isConnected; }
}

window.StatusManager = StatusManager;
