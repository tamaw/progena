import { NavLink } from 'react-router-dom';
import { useAlertCenter } from '../../context/AlertCenterContext';
import { useTheme } from '../../hooks/useTheme';
import StatusIndicator from '../common/StatusIndicator';

function Header({ connectionState }) {
  const { currentTheme, toggleTheme } = useTheme();
  const { unreadCount, openPanel } = useAlertCenter();

  return (
    <header className="header">
      <div className="header-brand">
        <span className="brand-icon">🧬</span>
        <h1>Progenitor</h1>
      </div>
      <nav className="header-nav">
        <NavLink to="/" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          💬 Chat
        </NavLink>
        <NavLink to="/dashboard" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          📊 Dashboard
        </NavLink>
        <NavLink to="/workspace" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          🧱 Workspace
        </NavLink>
        <NavLink to="/kanban" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          🗂️ Kanban
        </NavLink>
        <NavLink to="/prompts" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          ✍️ Prompts
        </NavLink>
        <NavLink to="/logs" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          📜 Logs
        </NavLink>
      </nav>
      <div className="header-status">
        <StatusIndicator status={connectionState} />
      </div>
      <div className="header-actions">
        <button type="button" className="btn-icon" title="Settings">⚙️</button>
        <button type="button" className="btn-icon" title="Toggle Theme" onClick={toggleTheme}>
          {currentTheme === 'dark' ? '🌙' : '☀️'}
        </button>
        <button type="button" className="btn-icon btn-notifications" title="Alerts" onClick={openPanel}>
          🔔
          {unreadCount > 0 ? (
            <span className="alert-badge">{unreadCount > 99 ? '99+' : unreadCount}</span>
          ) : null}
        </button>
      </div>
    </header>
  );
}

export default Header;
