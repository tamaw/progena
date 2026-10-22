import { useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useWorkspace } from '../context/WorkspaceContext';
import { getWorkspaceRegistry, removeWorkspaceHost } from '../lib/workspace';
import { useMenuDismiss } from '../hooks/useMenuDismiss';
import { ChevronDownIcon, CloseIcon } from '../components/navIcons';

export function WorkspaceSwitcher() {
  const { workspaceHost, switchWorkspace } = useWorkspace();
  const location = useLocation();
  const [isOpen, setIsOpen] = useState(false);
  const [hosts, setHosts] = useState(() => getWorkspaceRegistry());
  const rootRef = useRef(null);

  useMenuDismiss(isOpen, rootRef, () => setIsOpen(false));

  const switchTo = (host) => {
    setIsOpen(false);
    switchWorkspace(host, `${location.pathname}${location.search}`);
  };

  const removeHost = (event, host) => {
    event.stopPropagation();
    removeWorkspaceHost(host);
    setHosts(getWorkspaceRegistry());
  };

  return (
    <div className="workspace-menu" ref={rootRef}>
      <button
        type="button"
        className="workspace-menu__trigger"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        onClick={() => { if (!isOpen) setHosts(getWorkspaceRegistry()); setIsOpen((current) => !current); }}
      >
        <span className={`workspace-menu__chevron ${isOpen ? 'is-open' : ''}`} aria-hidden="true">
          <ChevronDownIcon />
        </span>
        <span className="workspace-menu__label">{workspaceHost}</span>
      </button>
      {isOpen ? (
        <ul className="workspace-menu__list" role="listbox" aria-label="Select workspace">
          {hosts.map((host) => (
            <li key={host} role="option" aria-selected={host === workspaceHost} className="workspace-menu__row">
              <button
                type="button"
                className={`workspace-menu__item ${host === workspaceHost ? 'is-active' : ''}`}
                onClick={() => switchTo(host)}
              >
                <span className="workspace-menu__item-label">{host}</span>
                {host === workspaceHost ? <span className="workspace-menu__check" aria-hidden="true">✓</span> : null}
              </button>
              <button
                type="button"
                className="workspace-menu__delete"
                aria-label={`Remove workspace ${host}`}
                disabled={host === workspaceHost || hosts.length <= 1}
                onClick={(event) => removeHost(event, host)}
              >
                <CloseIcon />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
