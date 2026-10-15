function getPageStatusContext(pathname, runtime, counts) {
  const route = pathname.replace(/^\//, '').split('/')[0] || 'chat';
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
    repositories: ['Repository map', '6 sources', 'Branches and commits'],
    schedules: ['Schedules', '6 mocked jobs', 'Next runs sorted'],
    sessions: ['Session history', '637 indexed', 'Pinned context ready'],
    plugins: ['Plugins library', 'Installed plugin packages', 'Skills activate in new sessions'],
    tasks: ['Task board', `${runningCount} active`, 'Backlog visible'],
    tools: ['MCP tools', '9 servers', 'Capabilities enabled'],
    workspace: ['Workspace canvas', 'Nodes editable', 'Dock ready'],
  };

  return contextByRoute[route] || ['Progenitor', 'Ready', 'Runtime workspace'];
}

export default function AppStatusBar({ runtime, pathname }) {
  const { health, widgets } = runtime;
  const agentWidgets = widgets.filter((widget) => widget.type === 'agent');
  const runningSessions = widgets.filter((widget) => ['running', 'busy', 'executing'].includes(widget.status || widget.data?.agentStatus)).length;
  const onlineAgents = agentWidgets.filter((widget) => !['offline', 'stopped', 'error'].includes(widget.status || widget.data?.agentStatus)).length;
  const pageContext = getPageStatusContext(pathname, runtime, { onlineAgents, runningSessions });

  return (
    <footer className="app-shell__statusbar">
      <div className="statusbar-context" aria-hidden="true" data-page-context={pageContext.join(' · ')} />
      <div className="statusbar-runtime" aria-label="Runtime status">
        <span className="statusbar-pill">{runningSessions} running sessions</span>
        <span className="statusbar-pill">{onlineAgents} online agents</span>
        <span className="statusbar-pill"><span className={health.isHealthy ? 'status-dot ok' : 'status-dot'} />{health.isHealthy ? 'Online' : 'Offline'}</span>
      </div>
    </footer>
  );
}
