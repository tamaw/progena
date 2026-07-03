import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { selectChatIndicators } from '../../store/uiDataSlice';
import { useAlertCenter } from '../../context/AlertCenterContext';
import { useChromeRuntime } from '../../context/ChromeRuntimeContext';
import { useConfig } from '../../context/ConfigContext';
import { useTheme } from '../../hooks/useTheme';
import { useChatApi } from '../../hooks/api/useChatApi';
import { renderMarkdownToHtml } from '../../lib/markdown';
import ChatInput from '../chat/ChatInput';
import NotificationContainer from '../common/NotificationContainer';
import {
  createSolutionFromName,
  createProjectFromName,
  loadStoredSolutions,
  saveStoredSolutions,
} from '../../data/solutions';
import {
  AgentsNavIcon,
  AlertsIcon,
  ChannelsNavIcon,
  ChatNavIcon,
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CloseIcon,
  DashboardNavIcon,
  InsightsIcon,
  KnowledgeNavIcon,
  ModelsNavIcon,
  MoonIcon,
  OpenChatIcon,
  PinIcon,
  QuickChatIcon,
  RailSettingsIcon,
  RefreshIcon,
  RepositoriesNavIcon,
  SchedulesNavIcon,
  SearchIcon,
  SequencesNavIcon,
  SettingsAboutIcon,
  SettingsAgentsIcon,
  SettingsAppearanceIcon,
  SettingsGeneralIcon,
  SettingsIntegrationsIcon,
  SettingsKeyboardIcon,
  SettingsNotificationsIcon,
  SettingsTeamIcon,
  SkillsNavIcon,
  SunIcon,
  TasksNavIcon,
  ToolsNavIcon,
  UserAvatarIcon,
  WorkspaceNavIcon,
} from './navIcons';
import {
  buildQuickChatOutboundMessage,
  defaultQuickChatConfig,
  normalizeQuickChatConfig,
} from './quickChatConfig';

// Grouped by owner/level (see the entity-relationship model):
//   Operate  — the surfaces you work in (Progenitor chat, overview, projects)
//   Automate — runtime: triggers, schedules, I/O, work in flight
//   Delegate — agents and the reusable building blocks they're composed of
// Sessions and Logs are intentionally dropped from the rail (still routable).
const navGroups = [
  {
    id: 'orchestrate',
    label: 'Orchestrate',
    items: [
      { to: '/', end: true, icon: <ChatNavIcon />, label: 'Chat', dot: true },
    ],
  },
  {
    id: 'spectate',
    label: 'Spectate',
    items: [
      { to: '/dashboard', icon: <DashboardNavIcon />, label: 'Dashboard', countKey: 'widgets' },
    ],
  },
  {
    id: 'operate',
    label: 'Operate',
    items: [
      // { to: '/projects', icon: <ProjectsNavIcon />, label: 'Projects' }, // hidden for now
      { to: '/tasks', icon: <TasksNavIcon />, label: 'Tasks', countKey: 'tasks' },
    ],
  },
  {
    id: 'automate',
    label: 'Automate',
    items: [
      { to: '/workflows', icon: <WorkspaceNavIcon />, label: 'Workflows' },
      { to: '/sequences', icon: <SequencesNavIcon />, label: 'Sequences' },
    ],
  },
  {
    id: 'delegate',
    label: 'Delegate',
    items: [
      { to: '/agents', icon: <AgentsNavIcon />, label: 'Agents', countKey: 'agents' },
    ],
  },
  {
    id: 'facilitate',
    label: 'Facilitate',
    items: [
      { to: '/skills', icon: <SkillsNavIcon />, label: 'Skills', countKey: 'skills' },
      { to: '/tools', icon: <ToolsNavIcon />, label: 'Tools', countKey: 'tools' },
      { to: '/knowledge', icon: <KnowledgeNavIcon />, label: 'Knowledge', countKey: 'knowledge' },
      { to: '/schedules', icon: <SchedulesNavIcon />, label: 'Schedules', countKey: 'schedules' },
    ],
  },
  {
    id: 'integrate',
    label: 'Integrate',
    items: [
      { to: '/channels', icon: <ChannelsNavIcon />, label: 'Channels' },
      { to: '/models', icon: <ModelsNavIcon />, label: 'Models' },
      { to: '/repositories', icon: <RepositoriesNavIcon />, label: 'Repositories', countKey: 'repositories' },
    ],
  },
];

// Flattened list of rail items, plus the routes that no longer appear in the
// rail but still need a breadcrumb label.
const navItems = navGroups.flatMap((group) => group.items);
const breadcrumbRoutes = [
  ...navItems,
  { to: '/sessions', label: 'Sessions' },
  { to: '/logs', label: 'Logs' },
];

const settingsSections = [
  { id: 'general', label: 'General', icon: <SettingsGeneralIcon /> },
  { id: 'appearance', label: 'Appearance', icon: <SettingsAppearanceIcon /> },
  { id: 'agents', label: 'Agents & Models', icon: <SettingsAgentsIcon /> },
  { id: 'notifications', label: 'Notifications', icon: <SettingsNotificationsIcon /> },
  { id: 'keyboard', label: 'Keyboard', icon: <SettingsKeyboardIcon /> },
  { id: 'integrations', label: 'Integrations', icon: <SettingsIntegrationsIcon /> },
  { id: 'team', label: 'Team', icon: <SettingsTeamIcon /> },
  { id: 'about', label: 'About', icon: <SettingsAboutIcon /> },
];

const workspaceOptions = ['progenitor-main', 'local-development', 'agent-lab'];

function getPageLabel(pathname) {
  if (pathname === '/') {
    return navItems.find((item) => item.to === '/')?.label ?? 'Chat';
  }
  if (pathname.startsWith('/solutions') || pathname.startsWith('/projects')) {
    return 'Projects';
  }
  const match = breadcrumbRoutes.find((item) => item.to !== '/' && pathname.startsWith(item.to));
  return match?.label ?? 'Chat';
}

const insightRanges = ['24h', '7d', '30d'];

function formatTime(value) {
  if (!value) return '--';
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatTimestamp(timestamp) {
  if (!timestamp) return '';
  return new Date(timestamp * 1000).toLocaleString();
}

function formatRelativeTimestamp(timestamp) {
  if (!timestamp) return 'now';
  const diffSeconds = Math.max(0, Math.floor(Date.now() / 1000 - timestamp));
  if (diffSeconds < 60) return 'now';
  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) return `${diffMinutes}m`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d`;
  return new Date(timestamp * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function PanelIconButton({ label, children, onClick, type = 'button', disabled = false }) {
  return (
    <button type={type} className="topnav-pane__icon-btn" aria-label={label} data-tooltip={label} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

function QuickChatMarkdown({ content }) {
  const html = useMemo(() => renderMarkdownToHtml(content), [content]);
  if (!content) return null;
  return <div className="markdown-content" dangerouslySetInnerHTML={{ __html: html }} />;
}

function SegmentedControl({ items, value, onChange, ariaLabel, className = '' }) {
  return (
    <div className={`topnav-seg ${className}`.trim()} aria-label={ariaLabel}>
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          className={`topnav-seg__btn ${value === item.value ? 'is-active' : ''}`}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function TopNavPane({
  title,
  subtitle,
  badge,
  icon,
  actions,
  children,
  onClose,
  onTogglePin,
  isPinned,
  isOpen = true,
  className = '',
  bodyClassName = 'topnav-pane__body',
}) {
  const panelId = title.replace(/\s+/g, '-').toLowerCase();
  return (
    <>
      <aside
        className={`topnav-pane${isOpen ? ' is-open' : ''}${isPinned ? ' is-pinned' : ''} ${className}`.trim()}
        aria-label={title}
        aria-labelledby={`${panelId}-title`}
        role={isPinned ? undefined : 'dialog'}
        aria-modal={isPinned ? undefined : true}
      >
        <header className="topnav-pane__head">
          <div className="topnav-pane__heading">
            <h2 className="topnav-pane__title" id={`${panelId}-title`}>
              {icon ? <span className="topnav-pane__title-icon">{icon}</span> : null}
              {title}
              {badge ? <span className="topnav-pane__badge">{badge}</span> : null}
            </h2>
            {subtitle ? <p className="topnav-pane__sub">{subtitle}</p> : null}
          </div>
          <div className="topnav-pane__actions">
            {actions}
            {onTogglePin ? (
              <PanelIconButton label={isPinned ? 'Unpin panel' : 'Pin panel'} onClick={onTogglePin}>
                <PinIcon isPinned={isPinned} />
              </PanelIconButton>
            ) : null}
            <PanelIconButton label="Close" onClick={onClose}><CloseIcon /></PanelIconButton>
          </div>
        </header>
        <div className={bodyClassName}>{children}</div>
      </aside>
    </>
  );
}

function ChromeIconButton({ label, icon, active, badge, onClick }) {
  return (
    <button
      type="button"
      className={`chrome-icon-btn ${active ? 'is-active' : ''} ${badge ? 'has-badge' : ''}`}
      aria-label={label}
      data-tooltip={label}
      onClick={onClick}
    >
      <span aria-hidden="true">{icon}</span>
      {badge ? <span className="chrome-icon-btn__badge">{badge}</span> : null}
    </button>
  );
}

function SidebarCreateModal({
  mode,
  solutionName,
  onClose,
  onCreateProject,
  onCreateSolution,
}) {
  const [name, setName] = useState('');
  const [summary, setSummary] = useState('');
  const isSolution = mode === 'solution';
  const title = isSolution ? 'New solution' : `New project in ${solutionName}`;
  const placeholder = isSolution ? 'Solution name' : 'Project name';

  const handleSubmit = useCallback((event) => {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      return;
    }
    if (isSolution) {
      onCreateSolution(trimmedName, summary);
    } else {
      onCreateProject(trimmedName);
    }
  }, [isSolution, name, onCreateProject, onCreateSolution, summary]);

  return (
    <div className="sidebar-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <form className="sidebar-modal" aria-label={title} onSubmit={handleSubmit} onMouseDown={(event) => event.stopPropagation()}>
        <header className="sidebar-modal__head">
          <div>
            <span className="sidebar-modal__eyebrow">{isSolution ? 'Solution' : 'Project'}</span>
            <h3>{title}</h3>
          </div>
          <button type="button" className="sidebar-modal__close" aria-label="Close" onClick={onClose}>×</button>
        </header>
        <label className="sidebar-modal__field">
          <span>{placeholder}</span>
          <input value={name} onChange={(event) => setName(event.target.value)} autoFocus />
        </label>
        {isSolution ? (
          <label className="sidebar-modal__field">
            <span>Short summary</span>
            <textarea rows="3" value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="What does this solution group together?" />
          </label>
        ) : null}
        <footer className="sidebar-modal__foot">
          <button type="button" className="task-btn task-btn--secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="task-btn task-btn--primary">{isSolution ? 'Create solution' : 'Create project'}</button>
        </footer>
      </form>
    </div>
  );
}

function WorkspaceSwitcher() {
  const [isOpen, setIsOpen] = useState(false);
  const [selected, setSelected] = useState(workspaceOptions[0]);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return undefined;
    const handlePointer = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };
    const handleKey = (event) => {
      if (event.key === 'Escape') setIsOpen(false);
    };
    document.addEventListener('mousedown', handlePointer);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handlePointer);
      document.removeEventListener('keydown', handleKey);
    };
  }, [isOpen]);

  return (
    <div className="workspace-menu" ref={rootRef}>
      <button
        type="button"
        className="workspace-menu__trigger"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((current) => !current)}
      >
        <span className={`workspace-menu__chevron ${isOpen ? 'is-open' : ''}`} aria-hidden="true">
          <ChevronDownIcon />
        </span>
        <span className="workspace-menu__label">{selected}</span>
      </button>
      {isOpen ? (
        <ul className="workspace-menu__list" role="listbox" aria-label="Select workspace">
          {workspaceOptions.map((name) => (
            <li key={name} role="option" aria-selected={name === selected}>
              <button
                type="button"
                className={`workspace-menu__item ${name === selected ? 'is-active' : ''}`}
                onClick={() => {
                  setSelected(name);
                  setIsOpen(false);
                }}
              >
                <span className="workspace-menu__item-label">{name}</span>
                {name === selected ? <span className="workspace-menu__check" aria-hidden="true">✓</span> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Header({ activePanel, setActivePanel, onSearch, onOpenPanel, onOpenQuickChat, onToggleNav }) {
  const { currentTheme, toggleTheme } = useTheme();
  const { unreadCount, markAllViewed } = useAlertCenter();
  const location = useLocation();
  const pageLabel = getPageLabel(location.pathname);

  const openPanel = useCallback((panel) => {
    setActivePanel((current) => (current === panel ? null : panel));
    onOpenPanel(panel);
    if (panel === 'alerts') {
      void markAllViewed();
    }
  }, [markAllViewed, onOpenPanel, setActivePanel]);

  return (
    <header className="app-shell__header">
      <button type="button" className="app-shell__menu-btn" aria-label="Toggle navigation" onClick={onToggleNav}>
        ☰
      </button>
      <div className="app-shell__brand">
        <WorkspaceSwitcher />
        <span className="app-shell__crumb-sep" aria-hidden="true">
          <ChevronRightIcon />
        </span>
        <span className="app-shell__crumb">{pageLabel}</span>
      </div>
      <form className="app-shell__search" onSubmit={onSearch}>
        <SearchIcon />
        <input name="search" type="search" placeholder="Search workspace, agents, prompts..." autoComplete="off" />
      </form>
      <div className="app-shell__actions">
        <ChromeIconButton
          label="Toggle theme"
          icon={(
            <>
              <MoonIcon className={currentTheme === 'dark' ? '' : 'chrome-icon-btn__theme-icon is-hidden'} />
              <SunIcon className={currentTheme === 'dark' ? 'chrome-icon-btn__theme-icon is-hidden' : ''} />
            </>
          )}
          onClick={toggleTheme}
        />
        <ChromeIconButton
          label="Insights"
          icon={<InsightsIcon />}
          active={activePanel === 'insights'}
          onClick={() => openPanel('insights')}
        />
        <ChromeIconButton
          label="Quick chat"
          icon={<QuickChatIcon />}
          active={activePanel === 'quickChat'}
          onClick={() => {
            if (activePanel !== 'quickChat') {
              onOpenQuickChat();
            }
            openPanel('quickChat');
          }}
        />
        <ChromeIconButton
          label="Alerts"
          icon={<AlertsIcon />}
          active={activePanel === 'alerts'}
          badge={unreadCount > 0 ? (unreadCount > 99 ? '99+' : unreadCount) : ''}
          onClick={() => openPanel('alerts')}
        />
      </div>
    </header>
  );
}

function PrimaryNav({ isCollapsed, isOpen, onClose, onToggleCollapse, onOpenSettings, onOpenAccount, runtime }) {
  const navigate = useNavigate();
  const [solutions, setSolutions] = useState(() => loadStoredSolutions());
  const [createModal, setCreateModal] = useState(null);
  const chatIndicators = useSelector(selectChatIndicators);
  const counts = useMemo(() => {
    const agentCount = runtime.widgets.filter((widget) => widget.type === 'agent').length;
    const scheduleCount = runtime.widgets.filter((widget) => widget.type === 'schedule').length;
    return {
      agents: Math.max(7, agentCount),
      knowledge: Math.max(6, runtime.widgets.length),
      repositories: 6,
      schedules: Math.max(6, scheduleCount),
      sessions: Math.max(3, agentCount),
      skills: 14,
      tasks: runtime.widgets.filter((widget) => ['running', 'busy', 'executing'].includes(widget.status || widget.data?.agentStatus)).length,
      tools: Math.max(9, runtime.widgets.filter((widget) => widget.type === 'mcp').length),
      widgets: runtime.widgets.length,
    };
  }, [runtime.widgets]);

  const handleCreateProject = useCallback((projectName) => {
    const solutionId = createModal?.solutionId;
    const targetSolution = solutions.find((solution) => solution.id === solutionId);
    if (!targetSolution) {
      return;
    }

    const createdProject = createProjectFromName(projectName, targetSolution.projects);
    const nextSolutions = solutions.map((solution) => (
      solution.id === solutionId
        ? { ...solution, projects: [...solution.projects, createdProject] }
        : solution
    ));
    saveStoredSolutions(nextSolutions);
    setSolutions(nextSolutions);
    setCreateModal(null);

    onClose();
    navigate(`/solutions/${solutionId}/projects/${createdProject.id}`);
  }, [createModal?.solutionId, navigate, onClose, solutions]);

  const handleCreateSolution = useCallback((solutionName, summary) => {
    const createdSolution = createSolutionFromName(solutionName, summary, solutions);
    const nextSolutions = [...solutions, createdSolution];
    saveStoredSolutions(nextSolutions);
    setSolutions(nextSolutions);
    setCreateModal(null);
  }, [solutions]);

  return (
    <aside className={`app-shell__sidebar ${isOpen ? 'is-open' : ''} ${isCollapsed ? 'is-collapsed' : ''}`}>
      <div className="app-shell__rail-logo">
        <Link to="/" className="app-shell__brand-link" aria-label="Progenitor home">
          <span className="app-shell__brand-mark" aria-hidden="true">🧬</span>
          <span className="app-shell__brand-name">Progenitor</span>
        </Link>
        <button
          type="button"
          className="app-shell__rail-toggle"
          aria-label={isOpen ? 'Close navigation' : isCollapsed ? 'Expand navigation' : 'Collapse navigation'}
          title={isOpen ? 'Close navigation' : isCollapsed ? 'Expand navigation' : 'Collapse navigation'}
          onClick={isOpen ? onClose : onToggleCollapse}
        >
          {isOpen ? <ChevronLeftIcon /> : isCollapsed ? <ChevronRightIcon /> : <ChevronLeftIcon />}
        </button>
      </div>
      <nav className="app-shell__nav" aria-label="Primary">
        {navGroups.map((group) => (
          <div className="app-shell__nav-group" key={group.id} data-group={group.id}>
            <div className="app-shell__nav-group-label">{group.label}</div>
            <div className="app-shell__nav-group-items">
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) => `app-shell__nav-item ${isActive ? 'is-active' : ''}`}
                  onClick={onClose}
                >
                  <span className="app-shell__nav-icon" aria-hidden="true">{item.icon}</span>
                  <span className="app-shell__nav-label">{item.label}</span>
                  {item.dot && (chatIndicators.isGenerating || chatIndicators.hasUnread) ? (
                    <span
                      className={`app-shell__nav-dot${chatIndicators.isGenerating ? ' app-shell__nav-dot--generating' : ''}`}
                      aria-hidden="true"
                    />
                  ) : null}
                  {item.countKey ? <span className="app-shell__nav-count">{counts[item.countKey]}</span> : null}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>
      <div className="app-shell__rail-foot">
        <button
          type="button"
          className="app-shell__rail-icon-btn"
          aria-label="Settings"
          onClick={() => {
            onClose();
            onOpenSettings();
          }}
        >
          <span className="app-shell__rail-glyph" aria-hidden="true"><RailSettingsIcon /></span>
          <span className="app-shell__rail-label">Settings</span>
          <span className="app-shell__rail-tip" aria-hidden="true">Settings</span>
        </button>
        <button
          type="button"
          className="app-shell__rail-user-btn"
          aria-label="Account"
          onClick={() => {
            onClose();
            onOpenAccount();
          }}
        >
          <span className="app-shell__rail-glyph" aria-hidden="true"><UserAvatarIcon /></span>
          <span className="app-shell__rail-label">Account</span>
          <span className="app-shell__rail-tip" aria-hidden="true">Account</span>
        </button>
      </div>
      {createModal ? (
        <SidebarCreateModal
          mode={createModal.mode}
          solutionName={solutions.find((solution) => solution.id === createModal.solutionId)?.name || 'solution'}
          onClose={() => setCreateModal(null)}
          onCreateProject={handleCreateProject}
          onCreateSolution={handleCreateSolution}
        />
      ) : null}
    </aside>
  );
}

function AlertsPanel({ onClose, isPinned, onTogglePin, isOpen }) {
  const {
    alerts,
    unreadCount,
    selectedAlert,
    isLoading,
    error,
    openAlert,
    closeAlert,
    markAllViewed,
  } = useAlertCenter();
  const [filter, setFilter] = useState('all');

  const visibleAlerts = useMemo(() => {
    if (filter === 'unread') return alerts.filter((alert) => !alert.viewed);
    if (filter === 'critical') return alerts.filter((alert) => ['error', 'critical'].includes(alert.type));
    return alerts;
  }, [alerts, filter]);
  const todayAlerts = visibleAlerts.filter((alert) => !alert.viewed);
  const earlierAlerts = visibleAlerts.filter((alert) => alert.viewed);
  const alertGroups = [
    { title: 'Today', alerts: todayAlerts },
    { title: 'Earlier', alerts: earlierAlerts },
  ].filter((group) => group.alerts.length > 0);

  const alertIcon = (alert) => {
    if (['error', 'critical'].includes(alert.type)) return { className: 'alert__ic--warn', label: '!' };
    if (alert.type === 'warning') return { className: 'alert__ic--warn', label: '!' };
    if (alert.type === 'success') return { className: 'alert__ic--ok', label: '✓' };
    return { className: alert.viewed ? 'alert__ic--muted' : 'alert__ic--info', label: alert.viewed ? '●' : '@' };
  };

  return (
    <TopNavPane
      title="Alerts"
      badge={unreadCount > 0 ? `${unreadCount} new` : 'All caught up'}
      className="topnav-pane--alerts"
      bodyClassName="alerts-pane__body"
      isPinned={isPinned}
      onTogglePin={onTogglePin}
      isOpen={isOpen}
      onClose={() => {
        closeAlert();
        onClose();
      }}
      actions={(
        <button type="button" className="topnav-pane__text-btn" onClick={() => void markAllViewed()}>
          Mark all viewed
        </button>
      )}
    >
      <SegmentedControl
        className="alerts-pane__tabs"
        ariaLabel="Alert filter"
        value={filter}
        onChange={setFilter}
        items={[
          { value: 'all', label: 'All' },
          { value: 'unread', label: 'Unread' },
          { value: 'critical', label: 'Critical' },
        ]}
      />
      {isLoading ? <div className="chrome-empty">Loading alerts...</div> : null}
      {!isLoading && error ? <div className="chrome-empty">Failed to load alerts: {error}</div> : null}
      {!isLoading && !error && visibleAlerts.length === 0 ? <div className="chrome-empty">No matching alerts.</div> : null}
      <div className="alerts-pane__scroll">
        {alertGroups.map((group) => (
          <div key={group.title} className="alerts-group">
            <div className="alerts-group__head">{group.title}</div>
            {group.alerts.map((alert) => {
              const icon = alertIcon(alert);
              return (
                <button
                  key={alert.id}
                  type="button"
                  className={`alert ${alert.viewed ? 'is-viewed' : 'is-new'}`}
                  onClick={() => openAlert(alert.id)}
                >
                  <span className={`alert__ic ${icon.className}`}>{icon.label}</span>
                  <span className="alert__body">
                    <span className="alert__title">{alert.title}</span>
                    <span className="alert__sub">{alert.message}</span>
                  </span>
                  <time className="alert__time" title={formatTimestamp(alert.timestamp)}>{formatRelativeTimestamp(alert.timestamp)}</time>
                </button>
              );
            })}
          </div>
        ))}
      </div>
      {selectedAlert ? (
        <section className="alert-detail-card">
          <button type="button" className="alert-detail-card__close" onClick={closeAlert}><CloseIcon /></button>
          <h3>{selectedAlert.title}</h3>
          <p>{formatTimestamp(selectedAlert.timestamp)}</p>
          <pre>{selectedAlert.message}</pre>
        </section>
      ) : null}
    </TopNavPane>
  );
}

// Persist the quick chat conversation across panel open/close (the panel unmounts
// when closed) and config/mode changes, so reopening resumes the same conversation.
// Cleared only by "New chat" (resetQuickChat). Resets naturally on full page reload.
const quickChatMemory = { history: [], session: null };

function QuickChatPanel({ onClose, panelConfig = defaultQuickChatConfig, isPinned, onTogglePin, isOpen }) {
  const { createSession, sendMessageStream, cancelStream } = useChatApi();
  const { config } = useConfig();
  const navigate = useNavigate();
  const location = useLocation();
  const isOnChatPage = location.pathname === '/';
  const [history, setHistory] = useState(quickChatMemory.history);
  const [scratchSession, setScratchSession] = useState(quickChatMemory.session);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState('');
  const [composerSeed, setComposerSeed] = useState({ key: 0, message: '' });
  const sessionPromiseRef = useRef(null);
  const isQuickChatMountedRef = useRef(false);
  const activeAssistantIdRef = useRef(null);
  const normalizedConfig = useMemo(
    () => normalizeQuickChatConfig(panelConfig),
    [panelConfig],
  );

  const startFreshQuickSession = useCallback(() => {
    if (isQuickChatMountedRef.current) {
      setScratchSession(null);
    }
    const sessionPromise = createSession()
      .then((session) => {
        if (isQuickChatMountedRef.current) {
          setScratchSession(session);
        }
        return session;
      })
      .catch((sessionError) => {
        if (isQuickChatMountedRef.current) {
          setError(sessionError.message);
        }
        sessionPromiseRef.current = null;
        return null;
      });
    sessionPromiseRef.current = sessionPromise;
    return sessionPromise;
  }, [createSession]);

  // Mirror conversation state into module-level memory so it survives the
  // panel unmounting on close. Streaming turns are stored as settled so a
  // mid-stream close doesn't resume as a stuck "Thinking…" bubble.
  useEffect(() => {
    quickChatMemory.history = history.map((turn) => (turn.isStreaming ? { ...turn, isStreaming: false } : turn));
  }, [history]);
  useEffect(() => {
    quickChatMemory.session = scratchSession;
  }, [scratchSession]);

  useEffect(() => {
    isQuickChatMountedRef.current = true;
    let cancelled = false;
    // Resume the persisted conversation; only spin up a fresh session the first
    // time (or after "New chat" cleared it).
    if (quickChatMemory.session) {
      sessionPromiseRef.current = Promise.resolve(quickChatMemory.session);
    } else {
      startFreshQuickSession()
        .then((session) => {
          if (!cancelled && session) {
            setScratchSession(session);
          }
          return session;
        });
    }

    return () => {
      cancelled = true;
      isQuickChatMountedRef.current = false;
      activeAssistantIdRef.current = null;
      cancelStream();
    };
  }, [cancelStream, startFreshQuickSession]);

  const ensureQuickSession = useCallback(async () => {
    if (scratchSession) {
      return scratchSession;
    }

    let session = sessionPromiseRef.current
      ? await sessionPromiseRef.current
      : null;
    if (!session) {
      session = await createSession();
    }
    setScratchSession(session);
    return session;
  }, [createSession, scratchSession]);

  const submit = useCallback(async (message) => {
    const text = message.trim();
    if (!text || isSending) return;
    const outboundMessage = buildQuickChatOutboundMessage(normalizedConfig.mode, text, normalizedConfig);

    const now = Date.now();
    const assistantId = `qchat_assistant_${now + 1}`;
    const streamHistory = history.map((turn) => ({
      role: turn.role,
      content: turn.content,
    }));
    const nextHistory = history.concat(
      { id: `qchat_user_${now}`, role: 'user', content: text, timestamp: now },
      {
        id: assistantId,
        role: 'assistant',
        content: '',
        timestamp: now + 1,
        isStreaming: true,
      },
    );
    activeAssistantIdRef.current = assistantId;
    setHistory(nextHistory);
    setIsSending(true);
    setError('');

    try {
      await ensureQuickSession();
      let assistantContent = '';
      const isStale = () => activeAssistantIdRef.current !== assistantId;
      await sendMessageStream(
        outboundMessage,
        streamHistory,
        (chunk) => {
          if (isStale()) return;
          assistantContent += chunk;
          setHistory((currentHistory) => currentHistory.map((turn) => (
            turn.id === assistantId
              ? { ...turn, content: assistantContent }
              : turn
          )));
        },
        (completeText) => {
          if (isStale()) return;
          setHistory((currentHistory) => currentHistory.map((turn) => {
            if (turn.id !== assistantId) return turn;
            const content = completeText || assistantContent || turn.content || '(No response text)';
            return { ...turn, content, isStreaming: false };
          }));
          activeAssistantIdRef.current = null;
        },
        (streamError) => {
          if (isStale()) return;
          setError(streamError.message);
          setHistory((currentHistory) => currentHistory.map((turn) => (
            turn.id === assistantId
              ? { ...turn, content: turn.content || `Error: ${streamError.message}`, isStreaming: false, error: true }
              : turn
          )));
          activeAssistantIdRef.current = null;
        },
      );

      if (!isStale()) {
        setHistory((currentHistory) => currentHistory.map((turn) => {
          if (turn.id !== assistantId) return turn;
          const content = assistantContent || turn.content || '(No response text)';
          return { ...turn, content, isStreaming: false };
        }));
        activeAssistantIdRef.current = null;
      }
    } catch (sendError) {
      if (sendError.name !== 'AbortError' && activeAssistantIdRef.current === assistantId) {
        setError(sendError.message);
        setHistory((currentHistory) => currentHistory.map((turn) => (
          turn.id === assistantId
            ? { ...turn, content: turn.content || `Error: ${sendError.message}`, isStreaming: false, error: true }
            : turn
        )));
      }
    } finally {
      if (activeAssistantIdRef.current === assistantId) {
        activeAssistantIdRef.current = null;
      }
      setIsSending(false);
    }
  }, [ensureQuickSession, history, isSending, normalizedConfig, sendMessageStream]);

  const openInChat = useCallback(() => {
    if (history.length > 0) {
      const promotedMessages = history.map((turn, index) => ({
        id: `msg_quick_${Date.now()}_${index}`,
        role: turn.role,
        content: turn.content,
        timestamp: Number.isFinite(turn.timestamp) ? turn.timestamp : Date.now() + index,
        tokenCount: turn.role === 'assistant' ? turn.content.split(/\s+/).filter(Boolean).length : 0,
        reasoningTokenCount: 0,
        reasoningText: '',
        streamEvents: [],
      }));
      localStorage.setItem(config.storage.chatHistory, JSON.stringify(promotedMessages));
      localStorage.setItem(
        config.storage.quickChatHandoff,
        JSON.stringify({
          messages: promotedMessages,
          sessionId: scratchSession?.session_id ?? null,
        }),
      );
      window.dispatchEvent(new CustomEvent('progenitor:load-chat-history', {
        detail: {
          messages: promotedMessages,
          sessionId: scratchSession?.session_id ?? null,
        },
      }));
    }
    onClose();
    if (!isOnChatPage) {
      navigate('/');
    }
  }, [config.storage.chatHistory, config.storage.quickChatHandoff, history, isOnChatPage, navigate, onClose, scratchSession?.session_id]);

  const resetQuickChat = useCallback(() => {
    cancelStream();
    activeAssistantIdRef.current = null;
    quickChatMemory.history = [];
    quickChatMemory.session = null;
    setHistory([]);
    setIsSending(false);
    setError('');
    setComposerSeed((current) => ({ key: current.key + 1, message: '' }));
    void startFreshQuickSession();
  }, [cancelStream, startFreshQuickSession]);

  const selectPrompt = useCallback((prompt) => {
    setComposerSeed((current) => ({ key: current.key + 1, message: prompt }));
  }, []);

  const stopQuickChat = useCallback(() => {
    const assistantId = activeAssistantIdRef.current;
    cancelStream();
    activeAssistantIdRef.current = null;
    setIsSending(false);
    if (!assistantId) return;

    setHistory((currentHistory) => currentHistory
      .map((turn) => {
        if (turn.id !== assistantId) return turn;
        if (!turn.content) return null;
        return { ...turn, content: `${turn.content} [Cancelled]`, isStreaming: false };
      })
      .filter(Boolean));
  }, [cancelStream]);

  return (
    <TopNavPane
      title={normalizedConfig.title}
      icon={<QuickChatIcon />}
      subtitle={normalizedConfig.subtitle || undefined}
      className="topnav-pane--quick"
      bodyClassName="qchat-pane__body"
      onClose={onClose}
      isPinned={isPinned}
      onTogglePin={onTogglePin}
      isOpen={isOpen}
      actions={(
        <>
          <PanelIconButton label="New chat" onClick={resetQuickChat} disabled={isSending}>
            <RefreshIcon />
          </PanelIconButton>
          <PanelIconButton label="Open in chat" onClick={openInChat} disabled={isSending}>
            <OpenChatIcon />
          </PanelIconButton>
        </>
      )}
    >
      <div className={`qchat-pane__scroll${history.length === 0 && !isSending && !error ? ' is-empty' : ''}`}>
        {history.map((turn, index) => (
          <article key={turn.id ?? `${turn.role}-${index}`} className="qchat-turn">
            <div className={`${turn.role === 'user' ? 'qchat-user' : 'qchat-assistant'}${turn.isStreaming && !turn.content ? ' is-loading' : ''}${turn.error ? ' is-error' : ''}`}>
              {turn.isStreaming && !turn.content ? (
                <>
                  Thinking
                  <span className="qchat-typing"><span /><span /><span /></span>
                </>
              ) : (
                <QuickChatMarkdown content={turn.content} />
              )}
            </div>
          </article>
        ))}
        {error ? <div className="qchat-assistant is-error">{error}</div> : null}
      </div>
      {history.length === 0 && !isSending && !error ? (
        <div className="qchat-pane__hints" aria-label="Quick chat suggestions">
          {normalizedConfig.prompts.map((prompt) => (
            <button key={prompt} type="button" className="qchat-chip" onClick={() => selectPrompt(prompt)}>
              {prompt}
            </button>
          ))}
        </div>
      ) : null}
      <ChatInput
        key={composerSeed.key}
        className="qchat-input"
        placeholder={normalizedConfig.placeholder}
        onSend={submit}
        onStop={stopQuickChat}
        isGenerating={isSending}
        disabled={false}
        showHints={false}
        enableSlashCommands={false}
        allowStop
        autoFocus
        keepFocusAfterSend
        initialMessage={composerSeed.message}
      />
    </TopNavPane>
  );
}

function SettingsPanel({ onClose, isPinned, onTogglePin, isOpen }) {
  const { config, setApiUrl, setNotifications, setSounds, setTheme } = useConfig();
  const [apiUrl, setApiUrlDraft] = useState(config.api.baseUrl);
  const [section, setSection] = useState('general');

  return (
    <TopNavPane title="Settings" className="topnav-pane--settings" bodyClassName="settings-pane__body" onClose={onClose} isPinned={isPinned} onTogglePin={onTogglePin} isOpen={isOpen}>
          <nav className="settings-nav" aria-label="Settings sections">
            {settingsSections.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`set-nav__item ${section === item.id ? 'is-active' : ''}`}
                onClick={() => setSection(item.id)}
              >
                {item.icon}
                {item.label}
              </button>
            ))}
          </nav>

          <div className="settings-content">
            {section === 'general' ? (
              <section className="set-section is-active">
                <header className="set-section__head">
                  <h3>General</h3>
                  <p>Workspace defaults and session behavior.</p>
                </header>

                <div className="set-row">
                  <div className="set-row__label">
                    <div className="set-row__title">Default workspace</div>
                    <div className="set-row__sub">Opens when you launch Progenitor.</div>
                  </div>
                  <select className="set-input" defaultValue="progenitor-main">
                    <option>progenitor-main</option>
                    <option>local-development</option>
                    <option>agent-lab</option>
                  </select>
                </div>

                <div className="set-row">
                  <div className="set-row__label">
                    <div className="set-row__title">Start view</div>
                    <div className="set-row__sub">The first screen after sign-in.</div>
                  </div>
                  <div className="set-seg" aria-label="Start view">
                    <button type="button" className="set-seg__btn">Overview</button>
                    <button type="button" className="set-seg__btn">Tasks</button>
                    <button type="button" className="set-seg__btn is-active">Chat</button>
                    <button type="button" className="set-seg__btn">Workspace</button>
                  </div>
                </div>

                <div className="set-row">
                  <span className="set-row__label">
                    <span className="set-row__title">API base URL</span>
                    <span className="set-row__sub">Backend endpoint used by chat, sessions, and tools.</span>
                  </span>
                  <span className="set-inline-control">
                    <input className="set-text-input" value={apiUrl} placeholder="same origin" onChange={(event) => setApiUrlDraft(event.target.value)} />
                    <button type="button" className="set-btn" onClick={() => setApiUrl(apiUrl)}>
                      Save
                    </button>
                  </span>
                </div>

                <div className="set-row">
                  <div className="set-row__label">
                    <div className="set-row__title">Auto-save drafts</div>
                    <div className="set-row__sub">Chat & workspace edits saved every 30s.</div>
                  </div>
                  <label className="set-switch is-on">
                    <input type="checkbox" defaultChecked />
                    <span className="set-switch__knob" aria-hidden="true" />
                  </label>
                </div>

                <div className="set-row">
                  <div className="set-row__label">
                    <div className="set-row__title">Session timeout</div>
                    <div className="set-row__sub">Auto sign-out after inactivity (30 min).</div>
                  </div>
                  <input className="set-slider" type="range" min="5" max="120" step="5" defaultValue="30" />
                </div>

                <div className="set-row set-row--danger">
                  <div className="set-row__label">
                    <div className="set-row__title">Reset all settings</div>
                    <div className="set-row__sub">Restore Progenitor to factory defaults.</div>
                  </div>
                  <button type="button" className="set-btn set-btn--danger">Reset</button>
                </div>
              </section>
            ) : null}

            {section === 'appearance' ? (
              <section className="set-section is-active">
                <header className="set-section__head">
                  <h3>Appearance</h3>
                  <p>Theme, density, and font preferences.</p>
                </header>

                <div className="set-row">
                  <div className="set-row__label">
                    <div className="set-row__title">Theme</div>
                    <div className="set-row__sub">Choose the default interface palette.</div>
                  </div>
                  <select className="set-input" value={config.theme.default} onChange={(event) => setTheme(event.target.value)}>
                    {config.theme.options.map((theme) => <option key={theme} value={theme}>{theme}</option>)}
                  </select>
                </div>

                <div className="set-row">
                  <div className="set-row__label">
                    <div className="set-row__title">Density</div>
                    <div className="set-row__sub">Adjust spacing across navigation and panes.</div>
                  </div>
                  <div className="set-seg" aria-label="Interface density">
                    <button type="button" className="set-seg__btn is-active">Comfortable</button>
                    <button type="button" className="set-seg__btn">Compact</button>
                    <button type="button" className="set-seg__btn">Presentation</button>
                  </div>
                </div>
              </section>
            ) : null}

            {section === 'notifications' ? (
              <section className="set-section is-active">
                <header className="set-section__head">
                  <h3>Notifications</h3>
                  <p>Choose which events trigger alerts, sounds, and desktop notices.</p>
                </header>

                <div className="set-row">
                  <div className="set-row__label">
                    <div className="set-row__title">Enable notifications</div>
                    <div className="set-row__sub">Show alerts from the status stream and background tasks.</div>
                  </div>
                  <label className={`set-switch ${config.notifications.enabled ? 'is-on' : ''}`}>
                    <input
                      type="checkbox"
                      checked={config.notifications.enabled}
                      onChange={(event) => setNotifications(event.target.checked)}
                    />
                    <span className="set-switch__knob" aria-hidden="true" />
                  </label>
                </div>

                <div className="set-row">
                  <div className="set-row__label">
                    <div className="set-row__title">Notification sounds</div>
                    <div className="set-row__sub">Play a subtle sound when important work completes.</div>
                  </div>
                  <label className={`set-switch ${config.notifications.soundEnabled ? 'is-on' : ''}`}>
                    <input
                      type="checkbox"
                      checked={config.notifications.soundEnabled}
                      onChange={(event) => setSounds(event.target.checked)}
                    />
                    <span className="set-switch__knob" aria-hidden="true" />
                  </label>
                </div>
              </section>
            ) : null}

            {!['general', 'appearance', 'notifications'].includes(section) ? (
              <section className="set-section is-active">
                <div className="set-stub">
                  <div className="set-stub__glyph">{settingsSections.find((item) => item.id === section)?.icon}</div>
                  <div className="set-stub__title">{settingsSections.find((item) => item.id === section)?.label}</div>
                  <div className="set-stub__sub">
                    This section is visually integrated into the chrome and ready for real settings as backend support appears.
                  </div>
                </div>
              </section>
            ) : null}
          </div>
    </TopNavPane>
  );
}

function SearchPanel({ query, widgets, onClose, isPinned, onTogglePin, isOpen }) {
  const [search, setSearch] = useState(query);
  const normalizedSearch = search.trim().toLowerCase();
  const routeResults = navItems
    .filter((item) => !normalizedSearch || item.label.toLowerCase().includes(normalizedSearch) || item.to.includes(normalizedSearch))
    .map((item) => ({
      id: `route:${item.to}`,
      title: item.label,
      description: item.to === '/' ? 'Open chat workspace' : `Open ${item.to}`,
      meta: 'route',
      to: item.to,
    }));
  const widgetResults = widgets
    .filter((widget) => {
      const label = widget.title || widget.data?.title || widget.id;
      const description = widget.summary || widget.data?.summaryState || widget.data?.description || '';
      return !normalizedSearch
        || label.toLowerCase().includes(normalizedSearch)
        || description.toLowerCase().includes(normalizedSearch);
    })
    .slice(0, 8)
    .map((widget) => ({
      id: `widget:${widget.id}`,
      title: widget.title || widget.data?.title || widget.id,
      description: widget.summary || widget.data?.summaryState || widget.data?.description || 'Runtime widget',
      meta: widget.type || 'widget',
      to: widget.type === 'agent' || widget.type === 'mcp' ? '/agents' : '/dashboard',
    }));
  const results = routeResults.concat(widgetResults);

  return (
    <TopNavPane title="Search" subtitle="Visual command palette" className="topnav-pane--search" onClose={onClose} isPinned={isPinned} onTogglePin={onTogglePin} isOpen={isOpen}>
      <div className="chrome-search-panel">
        <div className="chrome-search-panel__input">
          <span aria-hidden="true">⌕</span>
          <input
            autoFocus
            value={search}
            placeholder="Search routes, widgets, agents..."
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <div className="topnav-list chrome-search-results">
          {results.length === 0 ? <div className="chrome-empty">No visual matches.</div> : null}
          {results.map((result) => (
            <NavLink key={result.id} to={result.to} className="topnav-list-item chrome-search-result" onClick={onClose}>
              <span className="topnav-list-item__dot" />
              <span>
                <strong>{result.title}</strong>
                <small>{result.description}</small>
              </span>
              <time>{result.meta}</time>
            </NavLink>
          ))}
        </div>
      </div>
    </TopNavPane>
  );
}

function InsightsPanel({ runtime, onClose, isPinned, onTogglePin, isOpen }) {
  const { health, isStatusStreamConnected, lastEventAt, refreshWidgets, status, widgets } = runtime;
  const [range, setRange] = useState('7d');
  const agentWidgets = widgets.filter((widget) => widget.type === 'agent' || widget.type === 'mcp');
  const scheduleWidgets = widgets.filter((widget) => widget.type === 'schedule');
  const running = agentWidgets.filter((widget) => ['running', 'busy', 'executing'].includes(widget.status || widget.data?.agentStatus)).length;
  const ready = agentWidgets.filter((widget) => (widget.status || widget.data?.agentStatus) === 'ready').length;
  const cycleCount = status?.cycle_count ?? 0;
  const totalActivity = Math.max(1, agentWidgets.length + scheduleWidgets.length + cycleCount);

  return (
    <TopNavPane
      title="Token consumption"
      subtitle={`${isStatusStreamConnected ? 'Live status stream connected' : 'Waiting for status stream'} · progenitor-main`}
      onClose={onClose}
      isPinned={isPinned}
      onTogglePin={onTogglePin}
      isOpen={isOpen}
      className="topnav-pane--insights"
      bodyClassName="ins-pane__body"
    >
      <div className="ins-pane__toolbar">
        <SegmentedControl
          className="ins-range"
          ariaLabel="Insight range"
          value={range}
          onChange={setRange}
          items={insightRanges.map((value) => ({ value, label: value }))}
        />
        <button type="button" className="topnav-pane__text-btn" onClick={refreshWidgets}>Refresh</button>
      </div>
      <div className="ins-kpis">
        <div className="ins-kpi">
          <div className="ins-kpi__label">Runtime</div>
          <div className="ins-kpi__value">{health.isHealthy ? 'Online' : 'Offline'}</div>
          <div className="ins-kpi__delta ins-kpi__delta--muted">Health {formatTime(health.lastCheck)} · SSE {formatTime(lastEventAt)}</div>
        </div>
        <div className="ins-kpi">
          <div className="ins-kpi__label">Agents</div>
          <div className="ins-kpi__value">{agentWidgets.length}</div>
          <div className="ins-kpi__delta ins-kpi__delta--up">{ready} ready</div>
        </div>
        <div className="ins-kpi">
          <div className="ins-kpi__label">Running</div>
          <div className="ins-kpi__value">{running}</div>
          <div className="ins-kpi__delta ins-kpi__delta--muted">{scheduleWidgets.length} schedules</div>
        </div>
        <div className="ins-kpi">
          <div className="ins-kpi__label">Cycles</div>
          <div className="ins-kpi__value">{cycleCount}</div>
          <div className="ins-kpi__delta ins-kpi__delta--muted">{range} window</div>
        </div>
      </div>

      <section className="ins-chart-card">
        <header className="ins-chart-card__head">
          <h3>Stacked usage by agent</h3>
          <div className="ins-legend">
            <span className="ins-legend__item"><span className="ins-legend__sw sw-blue" /> agents</span>
            <span className="ins-legend__item"><span className="ins-legend__sw sw-green" /> schedules</span>
            <span className="ins-legend__item"><span className="ins-legend__sw sw-purple" /> cycles</span>
          </div>
        </header>
        <div className="ins-stack-chart">
          {agentWidgets.slice(0, 18).map((widget, index) => {
            const label = widget.title || widget.data?.title || widget.id;
            return (
              <span
                key={widget.id}
                title={label}
                style={{ height: `${Math.max(18, 38 + ((index * 17) % 110))}px` }}
              />
            );
          })}
        </div>
      </section>

      <section className="ins-agents">
        <header className="ins-agents__head">
          <h3>Agents</h3>
          <span className="muted">Sorted by usage</span>
        </header>
        <ul className="ins-agent-list">
        {agentWidgets.slice(0, 8).map((widget, index) => {
          const label = widget.title || widget.data?.title || widget.id;
          const tone = widget.status || widget.data?.agentStatus || 'idle';
          const share = Math.max(8, Math.round(((agentWidgets.length - index) / totalActivity) * 100));
          return (
              <li key={widget.id} className="ins-agent">
                <span className="ins-agent__chip">{label.slice(0, 1).toUpperCase()}</span>
                <span>
                  <span className="ins-agent__name">{label}</span>
                  <span className="ins-agent__sub">{tone}</span>
                </span>
                <span className="ins-agent__spark" aria-hidden="true">
                  <span style={{ width: `${Math.max(18, 92 - index * 8)}%` }} />
                </span>
                <span className="ins-agent__value">{Math.max(1, cycleCount + index * 37).toLocaleString()}</span>
                <span className="ins-agent__share">
                  <span className="ins-agent__share-bar"><span className="ins-agent__share-fill" style={{ width: `${share}%` }} /></span>
                  {share}%
                </span>
              </li>
          );
        })}
        </ul>
      </section>
    </TopNavPane>
  );
}

function AccountPanel({ onClose, isPinned, onTogglePin, isOpen }) {
  return (
    <TopNavPane title="Account" className="topnav-pane--account" bodyClassName="account-pane" onClose={onClose} isPinned={isPinned} onTogglePin={onTogglePin} isOpen={isOpen}>
      <div className="account-pane__id">
        <span className="account-pane__avatar" aria-hidden="true"><UserAvatarIcon /></span>
        <div className="account-pane__id-text">
          <div className="account-pane__name">Tama Waddell</div>
          <div className="account-pane__email">me@tama.id.au</div>
        </div>
      </div>
      <dl className="account-pane__facts">
        <div className="account-pane__fact"><dt>Plan</dt><dd>Free</dd></div>
        <div className="account-pane__fact"><dt>Role</dt><dd>Owner</dd></div>
        <div className="account-pane__fact"><dt>Workspace</dt><dd>progenitor-main</dd></div>
        <div className="account-pane__fact"><dt>Member since</dt><dd>June 2026</dd></div>
      </dl>
      <div className="account-pane__foot">
        <button type="button" className="task-btn task-btn--secondary" onClick={onClose}>Close</button>
        <button type="button" className="task-btn task-btn--secondary" disabled>Sign out</button>
      </div>
    </TopNavPane>
  );
}

// Transition durations must match the CSS transition on .topnav-pane.
const PANEL_EXIT_MS = 200;
const PANEL_ENTER_DELAY_MS = 16; // one rAF tick

function PanelHost({ activePanel, quickChatConfig, searchQuery, setActivePanel, runtime, isPinned, onTogglePin }) {
  const closePanel = useCallback(() => setActivePanel(null), [setActivePanel]);

  // `displayed` = what is currently rendered in the DOM (may lag activePanel during transitions).
  // `isOpen`    = whether the `is-open` CSS class is applied (drives the slide animation).
  const [displayed, setDisplayed] = useState(activePanel);
  const [isOpen, setIsOpen] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    const clear = () => { if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; } };
    clear();

    if (activePanel === null) {
      // Closing: slide out, then unmount.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setIsOpen(false);
      timerRef.current = setTimeout(() => setDisplayed(null), PANEL_EXIT_MS);
    } else if (displayed === null) {
      // Fresh open: mount content, then slide in on next frame.
      setDisplayed(activePanel);
      timerRef.current = setTimeout(() => setIsOpen(true), PANEL_ENTER_DELAY_MS);
    } else if (displayed !== activePanel) {
      // Switching panels: slide out → swap content → slide in.
      setIsOpen(false);
      timerRef.current = setTimeout(() => {
        setDisplayed(activePanel);
        timerRef.current = setTimeout(() => setIsOpen(true), PANEL_ENTER_DELAY_MS);
      }, PANEL_EXIT_MS);
    }
    // Same panel re-opened: ensure it's visible.
    else {
      setIsOpen(true);
    }

    return clear;
  }, [activePanel]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!activePanel) return undefined;
    const handleKeyDown = (event) => { if (event.key === 'Escape') closePanel(); };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [activePanel, closePanel]);

  if (!displayed) return null;

  // Settings and Account are modal popups (centered window), not the right-side
  // slide-in used by the other chrome tools — they are never pinned.
  const isModalPanel = displayed === 'settings' || displayed === 'account';
  // Scrim: visible for the full lifetime of the pane (including enter/exit
  // transitions) so clicks never pass through while the panel is animating.
  // Modal panels get a full-width backdrop; the side panels leave the docked
  // column clickable when pinned.
  const scrim = (isModalPanel || !isPinned) ? (
    <button
      type="button"
      className={`topnav-scrim is-open${isModalPanel ? ' topnav-scrim--modal' : ''}`}
      aria-label="Close panel"
      onClick={closePanel}
    />
  ) : null;

  const shared = { isOpen, isPinned, onTogglePin };

  if (displayed === 'alerts') return <>{scrim}<AlertsPanel onClose={closePanel} {...shared} /></>;
  if (displayed === 'quickChat') {
    return (
      <>
        {scrim}
        <QuickChatPanel
          onClose={closePanel}
          panelConfig={quickChatConfig}
          {...shared}
        />
      </>
    );
  }
  if (displayed === 'settings') return <>{scrim}<SettingsPanel onClose={closePanel} isOpen={isOpen} /></>;
  if (displayed === 'account') return <>{scrim}<AccountPanel onClose={closePanel} isOpen={isOpen} /></>;
  if (displayed === 'search') return <>{scrim}<SearchPanel query={searchQuery} widgets={runtime.widgets} onClose={closePanel} {...shared} /></>;
  if (displayed === 'insights') return <>{scrim}<InsightsPanel runtime={runtime} onClose={closePanel} {...shared} /></>;
  return null;
}

function AppRuntimeShell() {
  const [activePanel, setActivePanel] = useState(null);
  const [quickChatConfig, setQuickChatConfig] = useState(defaultQuickChatConfig);
  const [searchQuery, setSearchQuery] = useState('');
  const [isNavCollapsed, setIsNavCollapsed] = useState(false);
  const [isNavOpen, setIsNavOpen] = useState(false);
  const [isPinned, setIsPinned] = useState(false);
  const runtime = useChromeRuntime();

  // Below 980px the sidebar becomes a fixed overlay drawer (CSS handles it).
  // Ensure the nav is not in icon-rail mode at those widths — the drawer is
  // either open (is-open class) or hidden off-screen, never a collapsed rail.
  // Note: isNavCollapsed initialises to false so no synchronous setState is
  // needed in the effect body; only future viewport-change events need handling.
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 980px)');
    const handler = (e) => {
      if (e.matches) {
        setIsNavCollapsed(false); // no icon rail — drawer handles narrow viewports
        setIsNavOpen(false);      // start with drawer closed
      }
    };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  const handleTogglePin = useCallback(() => {
    setIsPinned((current) => !current);
  }, []);

  const shellClass = [
    'app-shell',
    isNavCollapsed ? 'is-nav-collapsed' : '',
    activePanel && isPinned ? 'has-right-panel' : '',
  ].filter(Boolean).join(' ');

  useEffect(() => {
    const handleKeyDown = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setQuickChatConfig(defaultQuickChatConfig);
        setActivePanel('quickChat');
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'f') {
        event.preventDefault();
        setActivePanel('search');
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  useEffect(() => {
    const handleOpenQuickChat = (event) => {
      const nextConfig = normalizeQuickChatConfig(event.detail);
      setQuickChatConfig(nextConfig);
      setActivePanel('quickChat');
    };
    window.addEventListener('progenitor:open-quick-chat', handleOpenQuickChat);
    return () => {
      window.removeEventListener('progenitor:open-quick-chat', handleOpenQuickChat);
    };
  }, []);

  const handleSearch = useCallback((event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const query = String(form.get('search') || '').trim();
    setSearchQuery(query);
    setActivePanel('search');
  }, []);

  const handleOpenPanel = useCallback((panel) => {
    if (panel === 'insights' && runtime.health.isHealthy) {
      void runtime.refreshWidgets();
    }
  }, [runtime]);

  return (
    <div className={shellClass}>
      <PrimaryNav
        isCollapsed={isNavCollapsed}
        isOpen={isNavOpen}
        onClose={() => setIsNavOpen(false)}
        onToggleCollapse={() => setIsNavCollapsed((current) => !current)}
        onOpenSettings={() => setActivePanel('settings')}
        onOpenAccount={() => setActivePanel('account')}
        runtime={runtime}
      />
      <Header
        activePanel={activePanel}
        setActivePanel={setActivePanel}
        onSearch={handleSearch}
        onOpenPanel={handleOpenPanel}
        onOpenQuickChat={() => {
          setQuickChatConfig(defaultQuickChatConfig);
        }}
        onToggleNav={() => setIsNavOpen((current) => !current)}
      />
      <main className="app-shell__content">
        <Outlet />
      </main>
      {isNavOpen ? <button type="button" className="app-shell__nav-scrim" aria-label="Close navigation" onClick={() => setIsNavOpen(false)} /> : null}
      <PanelHost
        activePanel={activePanel}
        quickChatConfig={quickChatConfig}
        searchQuery={searchQuery}
        setActivePanel={setActivePanel}
        runtime={runtime}
        isPinned={isPinned}
        onTogglePin={handleTogglePin}
      />
      <NotificationContainer />
    </div>
  );
}

export { AppRuntimeShell };
export default AppRuntimeShell;
