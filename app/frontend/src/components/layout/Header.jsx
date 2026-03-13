import { NavLink } from 'react-router-dom';
import { useTheme } from '../../hooks/useTheme';
import StatusIndicator from '../common/StatusIndicator';

function Header({ connectionState }) {
  const { currentTheme, toggleTheme } = useTheme();

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
        <NavLink to="/logs" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
          📜 Logs
        </NavLink>
      </nav>
      <div className="header-status">
        <StatusIndicator status={connectionState} />
      </div>
      <div className="header-actions">
        <button className="btn-icon" title="Settings">⚙️</button>
        <button className="btn-icon" title="Toggle Theme" onClick={toggleTheme}>
          {currentTheme === 'dark' ? '🌙' : '☀️'}
        </button>
        <button className="btn-icon" title="Notifications">🔔</button>
      </div>
    </header>
  );
}

export default Header;
