function formatTime(value) {
  if (!value) return '--';
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function getPageStatusContext(pathname, runtime, counts) {
  const route = pathname === '/' ? 'chat' : pathname.replace(/^\//, '').split('/')[0];
  const runningCount = counts.runningSessions;
  if (route === 'solutions' || route === 'projects' || route === 'kanban') {
    return ['Project kanban', 'main solution', `${runningCount} active cards`];
  }

  const contextByRoute = {
    agents: ['Agents workspace', `${counts.onlineAgents} online`, 'Tools, memories, tasks linked'],
    chat: ['Chat session', 'Progenitor active', 'Messages stream here'],
    dashboard: ['Dashboard board', `${runtime.widgets.length} widgets`, `${runningCount} running`],
    knowledge: ['Knowledge graph', '8 bases', '214 links mapped'],
    logs: ['Runtime logs', 'Live tail ready', 'Errors grouped'],
    prompts: ['Prompt editor', 'Internal route', 'Workspace deep links'],
    repositories: ['Repository map', '6 sources', 'Branches and commits'],
    schedules: ['Schedules', '6 mocked jobs', 'Next runs sorted'],
    sessions: ['Session history', '637 indexed', 'Pinned context ready'],
    skills: ['Skills library', '14 reusable skills', 'Agents can share them'],
    tasks: ['Task board', `${runningCount} active`, 'Backlog visible'],
    tools: ['MCP tools', '9 servers', 'Capabilities enabled'],
    workspace: ['Workspace canvas', 'Nodes editable', 'Dock ready'],
  };

  return contextByRoute[route] || ['Progenitor', 'Ready', 'Runtime workspace'];
}

export default function AppStatusBar({ runtime, pathname }) {
  const { health, isStatusStreamConnected, lastEventAt, widgets } = runtime;
  const agentWidgets = widgets.filter((widget) => widget.type === 'agent');
  const runningSessions = Math.max(
    2,
    widgets.filter((widget) => ['running', 'busy', 'executing'].includes(widget.status || widget.data?.agentStatus)).length,
  );
  const onlineAgents = Math.max(
    7,
    agentWidgets.filter((widget) => !['offline', 'stopped', 'error'].includes(widget.status || widget.data?.agentStatus)).length,
  );
  const pageContext = getPageStatusContext(pathname, runtime, { onlineAgents, runningSessions });

  return (
    <footer className="app-shell__statusbar">
      <div className="statusbar-context" aria-label="Page context">
        {pageContext.map((item, index) => (
          <span key={item} className={index === 0 ? 'statusbar-context__primary' : ''}>{item}</span>
        ))}
      </div>
      <div className="statusbar-runtime" aria-label="Runtime status">
        <span className="statusbar-pill"><span className={health.isHealthy ? 'status-dot ok' : 'status-dot'} />{health.isHealthy ? 'Online' : 'Offline'}</span>
        <span className="statusbar-pill">{runningSessions} running sessions</span>
        <span className="statusbar-pill">{onlineAgents} online agents</span>
        <span className="statusbar-pill statusbar-pill--muted">
          {isStatusStreamConnected ? 'Live stream' : 'Stream idle'}{lastEventAt ? ` ${formatTime(lastEventAt)}` : ''}
        </span>
      </div>
    </footer>
  );
}
