/**
 * Progenitor Chat UI - Status Module
 * SSE-first architecture: Status updates are pushed via Server-Sent Events.
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
        this.eventSource = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 2000;
        this.lastStatus = null;

        this.init();
    }

    init() {
        const refreshBtn = document.getElementById('btn-refresh-agents');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => this.reconnect());
        }
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
                console.log('SSE: Connection established');
                this.reconnectAttempts = 0;
            };

            this.eventSource.addEventListener('connected', (event) => {
                console.log('SSE: Received initial status', event.data);
                try {
                    const data = JSON.parse(event.data);
                    console.log('SSE: Parsed initial data:', data);
                    this.lastStatus = data;
                    this.updateConnectionStatus(true);
                    this.updateAllStatus(data);
                } catch (e) {
                    console.warn('SSE: Failed to parse connected event:', e);
                    this.updateConnectionStatus(true);
                }
            });

            this.eventSource.addEventListener('status', (event) => {
                console.log('SSE: Status update received');
                try {
                    const data = JSON.parse(event.data);
                    this.lastStatus = data;
                    this.updateAllStatus(data);
                } catch (e) {
                    console.warn('SSE: Failed to parse status event:', e);
                }
            });

            this.eventSource.addEventListener('heartbeat', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.lastStatus = data;
                    this.updateAllStatus(data);
                } catch (e) {
                    console.warn('SSE: Failed to parse heartbeat:', e);
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
            const delay = this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts - 1);
            console.log('SSE: Reconnecting in ' + Math.round(delay) + 'ms');
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
        this.updateSubAgents(data);
        this.updateTasks(data);
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
        console.log('Updating agent status with:', data);
        const running = data.progenitor_running;
        if (this.agentRunning) {
            this.agentRunning.textContent = running ? 'Running' : 'Stopped';
            this.agentRunning.className = 'status-value ' + (running ? 'status-healthy' : 'status-error');
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
    }

    updateSubAgents(data) {
        if (!this.subAgentsList) return;
        const running = data.progenitor_running;
        if (running) {
            this.subAgentsList.innerHTML = '<div class="sub-agent-card active"><div class="sub-agent-header"><span class="sub-agent-name">Planner</span><span class="sub-agent-status"><span class="sub-agent-status-dot"></span>active</span></div></div><div class="sub-agent-card idle"><div class="sub-agent-header"><span class="sub-agent-name">Executor</span><span class="sub-agent-status"><span class="sub-agent-status-dot"></span>idle</span></div></div>';
        } else {
            this.subAgentsList.innerHTML = '<div class="empty-state">No active sub-agents</div>';
        }
    }

    updateTasks(data) {
        if (!this.activeTasks) return;
        this.activeTasks.innerHTML = '<div class="empty-state">No active tasks</div>';
    }

    formatUptime(seconds) {
        if (seconds < 60) return Math.round(seconds) + 's';
        if (seconds < 3600) return Math.floor(seconds / 60) + 'm ' + Math.round(seconds % 60) + 's';
        if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ' + Math.floor((seconds % 3600) / 60) + 'm';
        return Math.floor(seconds / 86400) + 'd ' + Math.floor((seconds % 86400) / 3600) + 'h';
    }

    getLastStatus() { return this.lastStatus; }
    isServerConnected() { return this.isConnected; }
}

window.StatusManager = StatusManager;
