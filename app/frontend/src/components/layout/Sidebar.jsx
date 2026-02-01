import { useState, useEffect } from 'react';

function Sidebar({ status }) {
  const [agentStatus, setAgentStatus] = useState({
    running: 'Unknown',
    cycles: '--',
    pid: '--',
    memory: '--',
    uptime: '--',
    version: '--'
  });
  const [loops, setLoops] = useState([]);
  const [agents, setAgents] = useState([]);
  const [backlog, setBacklog] = useState([]);
  const [schedule, setSchedule] = useState([]);

  useEffect(() => {
    if (status) {
      setAgentStatus({
        running: status.progenitor_running ? 'Running' : 'Stopped',
        cycles: status.cycle_count ?? '--',
        pid: status.pid ?? '--',
        memory: status.memory_mb ? `${status.memory_mb.toFixed(1)} MB` : '--',
        uptime: formatUptime(status.uptime_seconds),
        version: status.version ?? '--'
      });
      setLoops(status.loops || []);
      setAgents(status.agents || []);
      setBacklog(status.backlog || []);
      setSchedule(status.schedule || []);
    }
  }, [status]);

  const formatUptime = (seconds) => {
    if (!seconds) return '--';
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  };

  return (
    <aside className="sidebar" id="sidebar">
      <div className="sidebar-section">
        <h3 className="sidebar-title">Progenitor Status</h3>
        <div className="agent-status-panel">
          <div className="status-item">
            <span className="status-label">Status</span>
            <span className="status-value">{agentStatus.running}</span>
          </div>
          <div className="status-item">
            <span className="status-label">Cycles</span>
            <span className="status-value">{agentStatus.cycles}</span>
          </div>
          <div className="status-item">
            <span className="status-label">PID</span>
            <span className="status-value">{agentStatus.pid}</span>
          </div>
          <div className="status-item">
            <span className="status-label">Memory</span>
            <span className="status-value">{agentStatus.memory}</span>
          </div>
          <div className="status-item">
            <span className="status-label">Server Uptime</span>
            <span className="status-value">{agentStatus.uptime}</span>
          </div>
          <div className="status-item">
            <span className="status-label">Version</span>
            <span className="status-value">{agentStatus.version}</span>
          </div>
        </div>
      </div>

      <div className="sidebar-section">
        <h3 className="sidebar-title">Loops</h3>
        <div className="sub-agents-list">
          {loops.length > 0 ? (
            loops.map((loop, i) => (
              <div key={i} className="loop-item">
                <span className="loop-name">{loop.name || `Loop ${i + 1}`}</span>
                <span className={`loop-status ${loop.status}`}>{loop.status}</span>
              </div>
            ))
          ) : (
            <div className="empty-state">No active loops</div>
          )}
        </div>
      </div>

      <div className="sidebar-section build-status-section">
        <h3 className="sidebar-title">
          <span>Build Status</span>
          <span className="build-indicator idle"></span>
        </h3>
        <div className="build-status-panel">
          <div className="build-idle-state">
            <span className="build-icon">✓</span>
            <span>No build in progress</span>
          </div>
        </div>
      </div>

      <div className="sidebar-section">
        <h3 className="sidebar-title">Registered Agents</h3>
        <div className="sub-agents-list">
          {agents.length > 0 ? (
            agents.map((agent, i) => (
              <div key={i} className="agent-item">
                <span className="agent-icon">{agent.icon || '🤖'}</span>
                <span className="agent-name">{agent.name}</span>
                <span className={`agent-status ${agent.status}`}>{agent.status}</span>
              </div>
            ))
          ) : (
            <div className="empty-state">No registered agents</div>
          )}
        </div>
      </div>

      <div className="sidebar-section">
        <h3 className="sidebar-title">Backlog</h3>
        <div className="backlog-list">
          {backlog.length > 0 ? (
            backlog.map((item, i) => (
              <div key={i} className="backlog-item">
                <span className="backlog-priority">{item.priority}</span>
                <span className="backlog-title">{item.title}</span>
              </div>
            ))
          ) : (
            <div className="empty-state">No backlog items</div>
          )}
        </div>
      </div>

      <div className="sidebar-section">
        <h3 className="sidebar-title">Schedule</h3>
        <div className="schedule-list">
          {schedule.length > 0 ? (
            schedule.map((task, i) => (
              <div key={i} className="schedule-item">
                <span className="schedule-time">{task.time}</span>
                <span className="schedule-name">{task.name}</span>
              </div>
            ))
          ) : (
            <div className="empty-state">No scheduled tasks</div>
          )}
        </div>
      </div>
    </aside>
  );
}

export default Sidebar;
