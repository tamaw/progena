import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider } from './context/ConfigContext';
import { NotificationProvider } from './context/NotificationContext';
import { WorkspaceProvider, useWorkspace } from './context/WorkspaceContext';
import RealtimeBridge from './store/RealtimeBridge';
import { buildWorkspacePath } from './lib/workspace';
import { AppRoutes } from './routes';
import './styles/main.css';
import './features/chat/styles/chat-panel.css';
import './features/dashboard/dashboard.css';
import './features/monitoring/logs-page.css';
import './features/tasks/kanban.css';
import './styles/notifications.css';
import './styles/status.css';
import './features/plugins/plugins.css';
import './features/instructions/instructions.css';
import './styles/chrome-frame.css';
import './styles/detail-header.css';
import './styles/forms.css';
import './features/insights/insights.css';

function App() {
  return (
    <WorkspaceProvider>
      <WorkspaceScopedApp />
    </WorkspaceProvider>
  );
}

// Everything a backend switch must reset -- Redux data, the SSE connection,
// and every page's own local state -- lives under this subtree. Keying it on
// `workspaceHost` makes React unmount and remount it whole on a switch, which
// re-runs the exact mount-time effects (RealtimeBridge's SSE connect + chat
// sessions + inbox seed, every page's fetch-on-mount) that drive the app's
// initial load -- no manual re-fetch orchestration needed.
function WorkspaceScopedApp() {
  const { workspaceHost } = useWorkspace();
  const basename = buildWorkspacePath(workspaceHost);
  return (
    <ConfigProvider key={workspaceHost} workspaceHost={workspaceHost}>
      <RealtimeBridge />
      <NotificationProvider>
        <BrowserRouter basename={basename}>
          <AppRoutes />
        </BrowserRouter>
      </NotificationProvider>
    </ConfigProvider>
  );
}

export default App;
