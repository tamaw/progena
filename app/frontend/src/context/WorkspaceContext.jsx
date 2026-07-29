/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { store, workspaceSwitched } from '../store/store';
import { parseWorkspacePath, buildWorkspacePath, currentPageHost, ensureWorkspaceRegistered, setDefaultWorkspaceHost } from '../lib/workspace';
import { clearWorkspaceScopedCaches } from '../lib/workspaceCache';

const WorkspaceContext = createContext(null);

// A plain path (no /w/<host> prefix) means the default workspace: the host
// that served this page (see lib/workspace.js's buildWorkspacePath/main.jsx).
function hostFromLocation() {
  return parseWorkspacePath(window.location.pathname)?.host ?? currentPageHost();
}

function resetForNewWorkspace() {
  clearWorkspaceScopedCaches();
  window.dispatchEvent(new CustomEvent('progenitor:workspace-switched'));
  store.dispatch(workspaceSwitched());
}

/**
 * Owns the active workspace host and switches it in place -- no full page
 * reload. `App.jsx` keys the entire workspace-scoped subtree (ConfigProvider,
 * RealtimeBridge, the router, every page) on `workspaceHost`, so a switch
 * here both resets Redux (every fetched slice, the realtime connection
 * state) and remounts every page, which re-runs the exact mount-time effects
 * that drive the app's initial data load: SSE connect, chat sessions, inbox,
 * and each page's own fetch-on-mount.
 */
export function WorkspaceProvider({ children }) {
  const [workspaceHost, setWorkspaceHost] = useState(hostFromLocation);

  const switchWorkspace = useCallback((host, rest) => {
    if (!host || host === workspaceHost) {
      return;
    }
    const targetRest = rest ?? parseWorkspacePath(window.location.pathname)?.rest ?? '/';
    ensureWorkspaceRegistered(host);
    setDefaultWorkspaceHost(host);
    window.history.pushState(null, '', buildWorkspacePath(host, targetRest));
    resetForNewWorkspace();
    setWorkspaceHost(host);
  }, [workspaceHost]);

  // Back/forward navigation across a switch changes the URL without going
  // through switchWorkspace -- mirror the same reset so the app doesn't end
  // up rendering the new URL against the old workspace's stale state.
  useEffect(() => {
    const onPopState = () => {
      const host = hostFromLocation();
      if (host && host !== workspaceHost) {
        resetForNewWorkspace();
        setWorkspaceHost(host);
      }
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [workspaceHost]);

  return (
    <WorkspaceContext.Provider value={{ workspaceHost, switchWorkspace }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) {
    throw new Error('useWorkspace must be used within a WorkspaceProvider');
  }
  return context;
}
