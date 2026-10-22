import { useState } from 'react';
import { useWorkspace } from '../../context/WorkspaceContext';
import { getWorkspaceRegistry, addWorkspaceHost, removeWorkspaceHost } from '../../lib/workspace';

export function WorkspacesSettingsRow() {
  const { workspaceHost, switchWorkspace } = useWorkspace();
  const [hosts, setHosts] = useState(() => getWorkspaceRegistry());
  const [newHost, setNewHost] = useState('');
  const [error, setError] = useState('');

  const refresh = () => setHosts(getWorkspaceRegistry());

  const addHost = () => {
    if (!newHost.trim()) return;
    if (!addWorkspaceHost(newHost)) {
      setError('Enter a bare hostname, optionally with :port -- no https://, path, or spaces.');
      return;
    }
    setError('');
    setNewHost('');
    refresh();
  };

  return (
    <div className="set-row set-row--stack">
      <div className="set-row__label">
        <div className="set-row__title">Workspaces</div>
        <div className="set-row__sub">Each workspace is a backend host[:port]. Switching navigates to it directly.</div>
      </div>
      <ul className="set-workspace-list">
        {hosts.map((host) => (
          <li key={host} className="set-workspace-list__item">
            <span className="set-workspace-list__host">{host}</span>
            {host === workspaceHost ? (
              <span className="set-workspace-list__current">Current</span>
            ) : (
              <button type="button" className="set-btn" onClick={() => switchWorkspace(host)}>
                Switch
              </button>
            )}
            <button
              type="button"
              className="set-btn set-btn--danger"
              disabled={host === workspaceHost || hosts.length <= 1}
              onClick={() => { removeWorkspaceHost(host); refresh(); }}
            >
              Remove
            </button>
          </li>
        ))}
      </ul>
      <span className="set-inline-control">
        <input
          className="set-text-input"
          value={newHost}
          placeholder="team-b.example.com or localhost:8001"
          onChange={(event) => { setNewHost(event.target.value); if (error) setError(''); }}
          onKeyDown={(event) => { if (event.key === 'Enter') addHost(); }}
        />
        <button type="button" className="set-btn" onClick={addHost}>
          Add
        </button>
      </span>
      {error ? <p className="set-workspace-list__error">{error}</p> : null}
    </div>
  );
}
