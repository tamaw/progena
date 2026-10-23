function formatUptime(seconds) {
  if (!seconds) return '--';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function SidebarContent({ status, mode = 'sidebar' }) {
  const agentStatus = {
    running: status?.progena_running ? 'Running' : 'Stopped',
    cycles: status?.cycle_count ?? '--',
    pid: status?.pid ?? '--',
    memory: status?.memory_mb ? `${status.memory_mb.toFixed(1)} MB` : '--',
    uptime: formatUptime(status?.uptime_seconds),
    version: status?.version ?? '--'
  };
  const loops = status?.loops || [];
  const agents = status?.agents || [];
  const backlog = status?.backlog || [];
  const schedule = status?.schedule || [];
  const sectionClassName = mode === 'dashboard' ? 'dashboard-panel' : 'sidebar-section';
  const titleClassName = mode === 'dashboard' ? 'panel-header' : 'sidebar-title';

  return (
    <>
      <div className={`${sectionClassName} ${mode === 'dashboard' ? 'detail-panel' : ''}`.trim()}>
        <div className={titleClassName}>
          <h3>Progena AI Status</h3>
        </div>
        <div className={mode === 'dashboard' ? 'panel-body status-detail-grid' : 'agent-status-panel'}>
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

      <div className={sectionClassName}>
        <div className={titleClassName}>
          <h3>Loops</h3>
          {mode === 'dashboard' && <span className="badge">{loops.length}</span>}
        </div>
        <div className={mode === 'dashboard' ? 'panel-body panel-list' : 'sub-agents-list'}>
          {loops.length > 0 ? (
            loops.map((loop, i) => (
              <div key={i} className="loop-item">
                <span className="loop-name">{loop.name || `Loop ${i + 1}`}</span>
                <span className={`loop-status ${loop.status}`}>{loop.status}</span>
              </div>
            ))
          ) : (
            <div className={`empty-state ${mode === 'dashboard' ? 'small' : ''}`.trim()}>No active loops</div>
          )}
        </div>
      </div>

      <div className={`${sectionClassName} build-panel`.trim()}>
        <div className={titleClassName}>
          <h3>Build Status</h3>
          {mode !== 'dashboard' && <span className="build-indicator idle"></span>}
        </div>
        <div className={mode === 'dashboard' ? 'panel-body build-status-panel' : 'build-status-panel'}>
          <div className="build-idle-state">
            <span className="build-icon">✓</span>
            <span>No build in progress</span>
          </div>
        </div>
      </div>

      <div className={sectionClassName}>
        <div className={titleClassName}>
          <h3>Registered Agents</h3>
          {mode === 'dashboard' && <span className="badge">{agents.length}</span>}
        </div>
        <div className={mode === 'dashboard' ? 'panel-body panel-list' : 'sub-agents-list'}>
          {agents.length > 0 ? (
            agents.map((agent, i) => (
              <div key={i} className="agent-item">
                <span className="agent-icon">{agent.icon || '🤖'}</span>
                <span className="agent-name">{agent.name}</span>
                <span className={`agent-status ${agent.status}`}>{agent.status}</span>
              </div>
            ))
          ) : (
            <div className={`empty-state ${mode === 'dashboard' ? 'small' : ''}`.trim()}>No registered agents</div>
          )}
        </div>
      </div>

      <div className={sectionClassName}>
        <div className={titleClassName}>
          <h3>Backlog</h3>
          {mode === 'dashboard' && <span className="badge">{backlog.length}</span>}
        </div>
        <div className={mode === 'dashboard' ? 'panel-body panel-list' : 'backlog-list'}>
          {backlog.length > 0 ? (
            backlog.map((item, i) => (
              <div key={i} className="backlog-item">
                <span className="backlog-priority">{item.priority}</span>
                <span className="backlog-title">{item.title}</span>
              </div>
            ))
          ) : (
            <div className={`empty-state ${mode === 'dashboard' ? 'small' : ''}`.trim()}>No backlog items</div>
          )}
        </div>
      </div>

      <div className={sectionClassName}>
        <div className={titleClassName}>
          <h3>Schedule</h3>
          {mode === 'dashboard' && <span className="badge">{schedule.length}</span>}
        </div>
        <div className={mode === 'dashboard' ? 'panel-body panel-list' : 'schedule-list'}>
          {schedule.length > 0 ? (
            schedule.map((task, i) => (
              <div key={i} className="schedule-item">
                <span className="schedule-time">{task.time}</span>
                <span className="schedule-name">{task.name}</span>
              </div>
            ))
          ) : (
            <div className={`empty-state ${mode === 'dashboard' ? 'small' : ''}`.trim()}>No scheduled tasks</div>
          )}
        </div>
      </div>
    </>
  );
}

function Sidebar({ status }) {
  return (
    <aside className="sidebar" id="sidebar">
      <SidebarContent status={status} />
    </aside>
  );
}

export function DashboardSidebarSections({ status }) {
  return <SidebarContent status={status} mode="dashboard" />;
}

export default Sidebar;
