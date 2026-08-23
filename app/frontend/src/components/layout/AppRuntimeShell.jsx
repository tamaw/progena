import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useDispatch, useSelector } from 'react-redux';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  ensureDraftChatSession,
  fetchChatMessages,
  fetchPreferences,
  fetchWidgets,
  markAlertsViewed,
  savePreferences,
  selectChatIndicators,
  selectGeneratingChatIds,
  selectInboxUnreadCount,
  selectLastReceivedAlert,
  selectPreferences,
  selectPreferencesSaveRequest,
  selectRequestIdByThreadId,
  selectUnseenNavRoutes,
  selectUiStatus,
  selectUiWidgets,
  selectUiAlerts,
  setActiveNavRoute,
  selectMarketplaces,
  fetchMarketplaces,
  saveMarketplaces,
} from '../../store/uiDataSlice';
import { selectRealtime, selectUiEventQueue } from '../../store/realtimeSlice';
import { consumeUiEventQueue } from '../../store/uiData/eventQueue';
import { useConfig } from '../../context/ConfigContext';
import { useWorkspace } from '../../context/WorkspaceContext';
import { getWorkspaceRegistry, addWorkspaceHost, removeWorkspaceHost } from '../../lib/workspace';
import { useNotifications } from '../../context/NotificationContext';
import { playNotificationSound } from '../../lib/notificationSound';
import { CHAT_STATUS } from '../../lib/chatStatus';
import { useTheme } from '../../hooks/useTheme';
import { useChatApi } from '../../hooks/api/useChatApi';
import { renderMarkdownToHtml } from '../../lib/markdown';
import { createUuid } from '../../lib/uuid';
import ChatInput from '../chat/ChatInput';
import NotificationContainer from '../common/NotificationContainer';
import Select from '../common/Select';
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
  InboxNavIcon,
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CloseIcon,
  DashboardNavIcon,
  InsightsIcon,
  KnowledgeNavIcon,
  MoonIcon,
  OpenChatIcon,
  PinIcon,
  ProgenitorAiIcon,
  QuickChatIcon,
  RailSettingsIcon,
  RefreshIcon,
  RepositoriesNavIcon,
  SchedulesNavIcon,
  RoutinesNavIcon,
  SearchIcon,
  SettingsAboutIcon,
  SettingsAgentsIcon,
  SettingsAppearanceIcon,
  SettingsGeneralIcon,
  SettingsIntegrationsIcon,
  SettingsKeyboardIcon,
  SettingsMarketplacesIcon,
  SettingsTeamIcon,
  PluginsNavIcon,
  SunIcon,
  TasksNavIcon,
  ToolsNavIcon,
  UserAvatarIcon,
  WorkspaceNavIcon,
} from './navIcons';
import {
  defaultQuickChatConfig,
  normalizeAiPrompt,
  normalizeQuickChatConfig,
} from './quickChatConfig';
import { derivePageContext, formatPageContextBlock, stripPageContextBlock } from '../../lib/pageContext';
import PopupModal from '../common/PopupModal';

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
      { to: '/inbox', icon: <InboxNavIcon />, label: 'Inbox', unreadDot: true },
      { to: '/chat', icon: <ChatNavIcon />, label: 'Chat', dot: true },
    ],
  },
  {
    id: 'spectate',
    label: 'Spectate',
    items: [
      { to: '/dashboard', icon: <DashboardNavIcon />, label: 'Dashboard', trackActivity: true },
    ],
  },
  {
    id: 'operate',
    label: 'Operate',
    items: [
      // { to: '/projects', icon: <ProjectsNavIcon />, label: 'Projects' }, // hidden for now
      { to: '/tasks', icon: <TasksNavIcon />, label: 'Tasks', trackActivity: true },
    ],
  },
  {
    id: 'automate',
    label: 'Automate',
    items: [
      { to: '/workflows', icon: <WorkspaceNavIcon />, label: 'Workflows' },
      { to: '/routines', icon: <RoutinesNavIcon />, label: 'Routines' },
    ],
  },
  {
    id: 'delegate',
    label: 'Delegate',
    items: [
      { to: '/agents', icon: <AgentsNavIcon />, label: 'Agents', trackActivity: true },
    ],
  },
  {
    id: 'facilitate',
    label: 'Facilitate',
    items: [
      { to: '/plugins', icon: <PluginsNavIcon />, label: 'Plugins', trackActivity: true },
      { to: '/tools', icon: <ToolsNavIcon />, label: 'Tools', trackActivity: true },
      { to: '/knowledge', icon: <KnowledgeNavIcon />, label: 'Knowledge', trackActivity: true },
      { to: '/schedules', icon: <SchedulesNavIcon />, label: 'Schedules', trackActivity: true },
    ],
  },
  {
    id: 'integrate',
    label: 'Integrate',
    items: [
      { to: '/channels', icon: <ChannelsNavIcon />, label: 'Channels' },
      { to: '/repositories', icon: <RepositoriesNavIcon />, label: 'Repositories' },
    ],
  },
];

// Flattened list of rail items, plus the routes that no longer appear in the
// rail but still need a breadcrumb label.
const navItems = navGroups.flatMap((group) => group.items);
const EMPTY_ALERTS = [];
// Pages whose nav dot reflects "an event arrived while you weren't there"
// (see uiDataSlice's NAV_ACTIVITY_EVENT_ROUTES for the event->route mapping).
const NAV_ACTIVITY_ROUTES = navItems.filter((item) => item.trackActivity).map((item) => item.to);
const breadcrumbRoutes = [
  ...navItems,
  { to: '/sessions', label: 'Sessions' },
  { to: '/logs', label: 'Logs' },
];

// Panels PanelHost actually grants isPinned/onTogglePin to (Settings/Account
// are modal popups, never pinned) - the only values worth persisting as
// preferences.pinnedPanel.
const PINNABLE_PANELS = new Set(['quickChat', 'alerts', 'insights']);
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'progenitor_sidebar_collapsed';
const PINNED_PANEL_STORAGE_KEY = 'progenitor_pinned_panel';

const settingsSections = [
  { id: 'general', label: 'General', icon: <SettingsGeneralIcon /> },
  { id: 'appearance', label: 'Appearance', icon: <SettingsAppearanceIcon /> },
  { id: 'marketplaces', label: 'Marketplaces', icon: <SettingsMarketplacesIcon /> },
  { id: 'agents', label: 'Agents & Models', icon: <SettingsAgentsIcon /> },
  { id: 'keyboard', label: 'Keyboard', icon: <SettingsKeyboardIcon /> },
  { id: 'integrations', label: 'Integrations', icon: <SettingsIntegrationsIcon /> },
  { id: 'team', label: 'Team', icon: <SettingsTeamIcon /> },
  { id: 'about', label: 'About', icon: <SettingsAboutIcon /> },
];

// The full IANA tz database, as the runtime actually knows it - matches what
// the backend's zoneinfo-based validation accepts, so nothing typed here can
// fail to save. Computed once at module load, not per-render.
const TIMEZONE_OPTIONS = (() => {
  try {
    return Intl.supportedValuesOf('timeZone').sort();
  } catch {
    // Very old browsers only: Intl.supportedValuesOf isn't universally
    // available. A minimal fallback still lets the dropdown function.
    return ['UTC', 'America/New_York', 'America/Los_Angeles', 'Europe/London', 'Europe/Paris', 'Asia/Tokyo', 'Australia/Sydney'];
  }
})();
const TIMEZONE_SELECT_OPTIONS = TIMEZONE_OPTIONS.map((tz) => ({ value: tz, label: tz }));

function getPageLabel(pathname) {
  if (pathname.startsWith('/solutions') || pathname.startsWith('/projects')) {
    return 'Projects';
  }
  const match = breadcrumbRoutes.find((item) => pathname.startsWith(item.to));
  return match?.label ?? 'Chat';
}

const insightRanges = ['24h', '7d', '30d'];

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
  const { workspaceHost, switchWorkspace } = useWorkspace();
  const location = useLocation();
  const [isOpen, setIsOpen] = useState(false);
  const [hosts, setHosts] = useState(() => getWorkspaceRegistry());
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

function Header({ activePanel, setActivePanel, onSubmitQuickChat, onOpenPanel, onOpenQuickChat, onToggleNav, quickChatInputRef }) {
  const { currentTheme, toggleTheme } = useTheme();
  const alerts = useSelector(selectUiAlerts);
  const unreadCount = alerts?.filter((a) => !a.viewed)?.length || 0;
  const location = useLocation();
  const pageLabel = getPageLabel(location.pathname);

  const openPanel = useCallback((panel) => {
    setActivePanel((current) => (current === panel ? null : panel));
    onOpenPanel(panel);
  }, [onOpenPanel, setActivePanel]);

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
      <form className="app-shell__quick-chat app-shell__quick-chat--ai" onSubmit={onSubmitQuickChat} aria-label="Ask Progenitor AI">
        <span className="app-shell__quick-chat-ai-icon" aria-hidden="true"><ProgenitorAiIcon /></span>
        <input ref={quickChatInputRef} name="prompt" type="text" placeholder="Ask Progenitor AI" aria-label="Ask Progenitor AI" autoComplete="off" />
        <span className="app-shell__quick-chat-shortcut" aria-label="Keyboard shortcut: Control Q">
          <kbd>Ctrl</kbd><kbd>Q</kbd>
        </span>
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

function PrimaryNav({ isCollapsed, isOpen, onClose, onToggleCollapse, onOpenSettings, onOpenAccount }) {
  const navigate = useNavigate();
  const dispatch = useDispatch();
  const location = useLocation();
  const [solutions, setSolutions] = useState(() => loadStoredSolutions());
  const [createModal, setCreateModal] = useState(null);
  const chatIndicators = useSelector(selectChatIndicators);
  const inboxUnread = useSelector(selectInboxUnreadCount);
  const unseenNavRoutes = useSelector(selectUnseenNavRoutes);

  // Tell the store which tracked page is currently open, so the "unseen
  // activity" dot (see uiDataSlice's NAV_ACTIVITY_EVENT_ROUTES) clears for it
  // and future events for this route are known-seen rather than "new".
  useEffect(() => {
    const matchedRoute = NAV_ACTIVITY_ROUTES.find(
      (route) => location.pathname === route || location.pathname.startsWith(`${route}/`),
    );
    dispatch(setActiveNavRoute(matchedRoute || null));
  }, [dispatch, location.pathname]);

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
        <Link to="/chat" className="app-shell__brand-link" aria-label="Progenitor home">
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
              {group.items.map((item) => {
                // Chat's global dot is the highest-priority status across all
                // conversations; other nav dots retain their own activity rules.
                const chatStatus = item.dot ? chatIndicators.status : null;
                const isGenerating = chatStatus === CHAT_STATUS.GENERATING;
                const showChatDot = item.dot && chatStatus !== CHAT_STATUS.IDLE;
                const showDot = showChatDot
                  || (item.unreadDot && inboxUnread > 0)
                  || (item.trackActivity && unseenNavRoutes[item.to]);
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) => `app-shell__nav-item ${isActive ? 'is-active' : ''}`}
                    onClick={onClose}
                  >
                    <span className="app-shell__nav-icon" aria-hidden="true">{item.icon}</span>
                    <span className="app-shell__nav-label">{item.label}</span>
                    {showDot ? (
                      <span
                        className={`app-shell__nav-dot${isGenerating ? ' app-shell__nav-dot--generating' : ''}${chatStatus === CHAT_STATUS.COMPLETE ? ' app-shell__nav-dot--complete' : ''}${chatStatus === CHAT_STATUS.ERROR ? ' app-shell__nav-dot--error' : ''}`}
                        aria-hidden="true"
                      />
                    ) : null}
                  </NavLink>
                );
              })}
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
  const alerts = useSelector(selectUiAlerts) || EMPTY_ALERTS;
  const [selectedAlertId, setSelectedAlertId] = useState(null);
  const [filter, setFilter] = useState('all');
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const selectedAlert = alerts.find((alert) => alert.id === selectedAlertId) || null;
  const unreadAlertIds = useMemo(
    () => alerts.filter((alert) => !alert.viewed).map((alert) => alert.id),
    [alerts],
  );
  const acknowledgeAlerts = useCallback((alertIds) => {
    if (!alertIds.length) {
      return;
    }
    // Do not alter Redux state here: only the correlated alerts.updated frame
    // confirms that the server has cleared an alert's unread state.
    void dispatch(markAlertsViewed({
      alertsUrl: getApiUrl('/api/ui/alerts'),
      alertIds,
    })).unwrap().catch(() => undefined);
  }, [dispatch, getApiUrl]);
  const selectAlert = useCallback((alert) => {
    setSelectedAlertId(alert.id);
    if (!alert.viewed) {
      acknowledgeAlerts([alert.id]);
    }
  }, [acknowledgeAlerts]);

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

  const unreadCount = alerts.filter((a) => !a.viewed).length;

  return (
    <TopNavPane
      title="Alerts"
      badge={unreadCount > 0 ? `${unreadCount} new` : 'All caught up'}
      actions={unreadAlertIds.length > 0 ? (
        <button
          type="button"
          className="topnav-pane__text-btn"
          onClick={() => acknowledgeAlerts(unreadAlertIds)}
        >
          Mark all read
        </button>
      ) : null}
      className="topnav-pane--alerts"
      bodyClassName="alerts-pane__body"
      isPinned={isPinned}
      onTogglePin={onTogglePin}
      isOpen={isOpen}
      onClose={onClose}
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
      {!alerts.length && visibleAlerts.length === 0 ? <div className="chrome-empty">No matching alerts.</div> : null}
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
                  onClick={() => selectAlert(alert)}
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
          <button type="button" className="alert-detail-card__close" onClick={() => setSelectedAlertId(null)}><CloseIcon /></button>
          <h3>{selectedAlert.title}</h3>
          <p>{formatTimestamp(selectedAlert.timestamp)}</p>
          <pre>{selectedAlert.message}</pre>
        </section>
      ) : null}
    </TopNavPane>
  );
}
// Persist the quick chat conversation across panel open/close (the panel unmounts
// when closed), so reopening the *same* mode resumes the same conversation.
// Cleared by "New chat" (resetQuickChat) and whenever the mode changes between
// opens (e.g. closing a "connect an MCP server" chat on Tools and opening
// "create a knowledge base" from Knowledge must not resume the old one, or its
// suggestion chips never show since the history is no longer empty). Resets
// naturally on full page reload.
const quickChatMemory = { history: [], threadId: null, mode: null };

// A workspace switch remounts every page (see App.jsx's key={workspaceHost}),
// which resets QuickChatPanel's own useState -- but this object lives at
// module scope for the page's whole lifetime and survives that remount, so
// it needs its own explicit reset or a reopened panel would resume the
// previous backend's scratch conversation.
if (typeof window !== 'undefined') {
  window.addEventListener('progenitor:workspace-switched', () => {
    quickChatMemory.history = [];
    quickChatMemory.threadId = null;
    quickChatMemory.mode = null;
  });
}

function QuickChatPanel({
  onClose,
  panelConfig = defaultQuickChatConfig,
  isPinned,
  onTogglePin,
  isOpen,
  pendingSubmission,
  onSubmissionConsumed,
}) {
  const { sendMessageStream, cancelStream, observeResponseStream } = useChatApi();
  const { config, getApiUrl } = useConfig();
  const dispatch = useDispatch();
  const uiEventQueue = useSelector(selectUiEventQueue);
  const preferences = useSelector(selectPreferences);
  const preferencesSaveRequest = useSelector(selectPreferencesSaveRequest);
  const generatingChatIds = useSelector(selectGeneratingChatIds);
  const requestIdByThreadId = useSelector(selectRequestIdByThreadId);
  const navigate = useNavigate();
  const location = useLocation();
  const isOnChatPage = location.pathname.startsWith('/chat');
  const normalizedConfig = useMemo(
    () => normalizeQuickChatConfig(panelConfig),
    [panelConfig],
  );
  if (quickChatMemory.mode !== normalizedConfig.mode) {
    quickChatMemory.history = [];
    quickChatMemory.threadId = null;
    quickChatMemory.mode = normalizedConfig.mode;
  }
  const [history, setHistory] = useState(quickChatMemory.history);
  const [scratchThreadId, setScratchThreadId] = useState(quickChatMemory.threadId);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState('');
  const [composerSeed, setComposerSeed] = useState({ key: 0, message: '' });
  const sessionPromiseRef = useRef(null);
  const pendingDraftRef = useRef(null);
  const pendingRehydrateRef = useRef(null);
  const lastPersistedThreadIdRef = useRef(null);
  const hasInitializedThreadRef = useRef(false);
  const lastSentPageContextRef = useRef(null);
  const eventSequenceRef = useRef(0);
  const isQuickChatMountedRef = useRef(false);
  const activeAssistantIdRef = useRef(null);
  const scrollEndRef = useRef(null);

  // Keep the transcript pinned to the latest message, including as it streams
  // in token by token (mirrors the main Chat page's scroll-to-bottom effect).
  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' });
  }, [history, isSending]);
  // A draft acknowledgement carries no session data. Resolve it only from the
  // causal stream event with the same request ID, using that event's thread ID.
  useEffect(() => {
    eventSequenceRef.current = consumeUiEventQueue(
      uiEventQueue,
      eventSequenceRef.current,
      ({ event }) => {
        const pendingDraft = pendingDraftRef.current;
        const session = event?.payload?.session;
        const threadId = session?.thread_id;
        if (
          pendingDraft
          && event?.payload?.request_id === pendingDraft.requestId
          && ['chat.session.created', 'chat.session.metadata.updated'].includes(event?.type)
          && typeof threadId === 'string'
          && threadId
        ) {
          pendingDraftRef.current = null;
          sessionPromiseRef.current = Promise.resolve(threadId);
          if (isQuickChatMountedRef.current) {
            setScratchThreadId(threadId);
          }
          pendingDraft.resolve(threadId);
          return false;
        }

        // A persisted quick-chat thread's messages, fetched directly (not
        // through the shared `chats` slice, which would clobber the main
        // Chat page's active-session state) to resume it after a reload.
        const pendingRehydrate = pendingRehydrateRef.current;
        if (
          pendingRehydrate
          && event?.type === 'chat.messages.page.loaded'
          && event?.payload?.request_id === pendingRehydrate.requestId
        ) {
          pendingRehydrateRef.current = null;
          pendingRehydrate.resolve(Array.isArray(event.payload.messages) ? event.payload.messages : []);
          return false;
        }

        return undefined;
      },
    );
  }, [uiEventQueue]);

  const startFreshQuickThread = useCallback(async () => {
    if (isQuickChatMountedRef.current) {
      setScratchThreadId(null);
    }
    pendingDraftRef.current?.resolve(null);
    pendingDraftRef.current = null;

    const proposedThreadId = createUuid();
    const requestId = createUuid();
    let resolveSession;
    const sessionPromise = new Promise((resolve) => {
      resolveSession = resolve;
    });
    // Register the causal event waiter before dispatching. The backend may
    // publish chat.session.created/metadata.updated before the 202 acknowledgement,
    // so assigning this after dispatch can miss the event and leave submit
    // waiting forever for a thread ID.
    pendingDraftRef.current = { requestId, resolve: resolveSession };
    sessionPromiseRef.current = sessionPromise;
    const draftRequest = dispatch(ensureDraftChatSession({
      chatsUrl: getApiUrl('chats'),
      threadId: proposedThreadId,
      requestId,
    }));

    try {
      await draftRequest.unwrap();
    } catch (sessionError) {
      if (pendingDraftRef.current?.requestId === requestId) {
        pendingDraftRef.current.resolve(null);
        pendingDraftRef.current = null;
        sessionPromiseRef.current = null;
      }
      if (isQuickChatMountedRef.current) {
        setError(sessionError.message);
      }
      return null;
    }

    return sessionPromise;
  }, [dispatch, getApiUrl]);

  // Resume a thread persisted in preferences (see the mount effect below).
  // Returns true once resumed (even with zero messages, e.g. a thread that
  // was created but never sent to), or false if it couldn't be resumed
  // (deleted/archived elsewhere) so the caller falls back to a fresh thread.
  const rehydrateFromPersistedThread = useCallback(async (persistedThreadId) => {
    const requestId = createUuid();
    let resolveMessages;
    const messagesPromise = new Promise((resolve) => {
      resolveMessages = resolve;
    });
    pendingRehydrateRef.current = { requestId, resolve: resolveMessages };

    const fetchRequest = dispatch(fetchChatMessages({
      chatsUrl: getApiUrl('chats'),
      threadId: persistedThreadId,
      limit: 50,
      requestId,
    }));

    let messages;
    try {
      await fetchRequest.unwrap();
      messages = await messagesPromise;
    } catch {
      if (pendingRehydrateRef.current?.requestId === requestId) {
        pendingRehydrateRef.current = null;
      }
      return false;
    }

    if (!isQuickChatMountedRef.current) {
      return true;
    }
    setHistory(messages.map((message) => ({
      id: message.id,
      role: message.role,
      content: stripPageContextBlock(message.content),
      timestamp: (typeof message.created_at === 'string' && Date.parse(message.created_at)) || Date.now(),
      isStreaming: false,
    })));
    setScratchThreadId(persistedThreadId);
    sessionPromiseRef.current = Promise.resolve(persistedThreadId);
    lastPersistedThreadIdRef.current = persistedThreadId;
    return true;
  }, [dispatch, getApiUrl]);

  // Spectator support: render live deltas for a generation this panel did not
  // itself dispatch -- e.g. the same thread open in the main Chat page (or
  // another tab) was sent a message. `isSending` already covers "this panel
  // is the one generating" (its own send flips it before `chat.response.started`
  // ever reaches Redux), so this only attaches for everyone else's sends.
  const spectatorCleanupRef = useRef(null);
  const spectatedRequestIdRef = useRef(null);

  useEffect(() => {
    const shouldSpectate = Boolean(scratchThreadId)
      && !isSending
      && generatingChatIds.includes(scratchThreadId);
    const requestId = shouldSpectate ? (requestIdByThreadId[scratchThreadId] || null) : null;

    if (!requestId) {
      if (spectatedRequestIdRef.current) {
        spectatorCleanupRef.current?.();
        spectatorCleanupRef.current = null;
        spectatedRequestIdRef.current = null;
        // The spectated generation just ended -- swap the live bubble for
        // the persisted, canonical transcript, same as a refresh rehydrate.
        void rehydrateFromPersistedThread(scratchThreadId);
      }
      return;
    }
    if (spectatedRequestIdRef.current === requestId) {
      return;
    }
    spectatorCleanupRef.current?.();

    const assistantId = `qchat_spectate_${requestId}`;
    let assistantContent = '';
    spectatedRequestIdRef.current = requestId;
    setHistory((currentHistory) => currentHistory.concat({
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      isStreaming: true,
    }));

    spectatorCleanupRef.current = observeResponseStream(requestId, {
      onChunk: (chunk) => {
        if (spectatedRequestIdRef.current !== requestId) return;
        assistantContent += chunk;
        setHistory((currentHistory) => currentHistory.map((turn) => (
          turn.id === assistantId ? { ...turn, content: assistantContent } : turn
        )));
      },
    });
  }, [scratchThreadId, isSending, generatingChatIds, requestIdByThreadId, observeResponseStream, rehydrateFromPersistedThread]);

  useEffect(() => () => {
    spectatorCleanupRef.current?.();
  }, []);

  const ensureQuickThread = useCallback(async () => {
    if (scratchThreadId) {
      return scratchThreadId;
    }
    const threadId = sessionPromiseRef.current
      ? await sessionPromiseRef.current
      : await startFreshQuickThread();
    if (!threadId) {
      throw new Error('Unable to create a quick chat thread.');
    }
    return threadId;
  }, [scratchThreadId, startFreshQuickThread]);


  // Mirror conversation state into module-level memory so it survives the
  // panel unmounting on close. Streaming turns are stored as settled so a
  // mid-stream close doesn't resume as a stuck "Thinking…" bubble.
  useEffect(() => {
    quickChatMemory.history = history.map((turn) => (turn.isStreaming ? { ...turn, isStreaming: false } : turn));
  }, [history]);
  useEffect(() => {
    quickChatMemory.threadId = scratchThreadId;
  }, [scratchThreadId]);

  // Persist the default-mode quick chat's thread id in preferences (the same
  // backend-persisted document `pinnedPanel` already lives in) so a pinned
  // quick chat can rehydrate after a full page reload, not just a
  // popover close/reopen. Other modes (schedule-create, mcp-create, ...) are
  // one-off flows whose own config never survives a reload either (it's
  // plain useState seeded from defaultQuickChatConfig), so persisting their
  // thread would just orphan it.
  useEffect(() => {
    if (
      normalizedConfig.mode !== 'default'
      || !scratchThreadId
      || scratchThreadId === lastPersistedThreadIdRef.current
      || !preferences.loaded
    ) {
      return;
    }
    lastPersistedThreadIdRef.current = scratchThreadId;
    if (scratchThreadId === preferences.quickChatThreadId) {
      return;
    }
    dispatch(savePreferences({
      preferencesUrl: getApiUrl('preferences'),
      ...preferencesSaveRequest,
      quickChatThreadId: scratchThreadId,
    }));
  }, [normalizedConfig.mode, scratchThreadId, preferences.loaded, preferences.quickChatThreadId, dispatch, getApiUrl, preferencesSaveRequest]);

  useEffect(() => {
    isQuickChatMountedRef.current = true;
    return () => {
      activeAssistantIdRef.current = null;
      // If a draft/rehydrate was still in flight, this abort leaves
      // sessionPromiseRef resolved to null with nothing to retry it -- reset
      // the "already decided" guard below so a remount (a real popover
      // reopen, or React StrictMode's dev-only double-invoke of mount
      // effects) starts over instead of treating the aborted attempt as done.
      if (pendingDraftRef.current || pendingRehydrateRef.current) {
        hasInitializedThreadRef.current = false;
      }
      pendingDraftRef.current?.resolve(null);
      pendingDraftRef.current = null;
      pendingRehydrateRef.current?.resolve([]);
      pendingRehydrateRef.current = null;
      isQuickChatMountedRef.current = false;
      void cancelStream();
    };
  }, [cancelStream]);

  // Decide the thread once per mount. A pinned quick chat's QuickChatPanel
  // mounts as soon as the shell adopts the pinned panel from its fast
  // localStorage cache (see PINNED_PANEL_STORAGE_KEY below) -- well before
  // the backend `preferences.snapshot` round-trip resolves -- so this can't
  // just run once on mount like `startFreshQuickThread` used to: it has to
  // wait for `preferences.loaded` before deciding there's nothing to resume.
  useEffect(() => {
    if (hasInitializedThreadRef.current) {
      return;
    }
    if (quickChatMemory.threadId) {
      hasInitializedThreadRef.current = true;
      sessionPromiseRef.current = Promise.resolve(quickChatMemory.threadId);
      return;
    }
    if (normalizedConfig.mode === 'default' && !preferences.loaded) {
      return;
    }
    hasInitializedThreadRef.current = true;
    if (normalizedConfig.mode === 'default' && preferences.quickChatThreadId) {
      void rehydrateFromPersistedThread(preferences.quickChatThreadId).then((resumed) => {
        if (!resumed && isQuickChatMountedRef.current) {
          void startFreshQuickThread();
        }
      });
    } else {
      void startFreshQuickThread();
    }
  }, [normalizedConfig.mode, preferences.loaded, preferences.quickChatThreadId, rehydrateFromPersistedThread, startFreshQuickThread]);


  const submit = useCallback(async (message, attachments = []) => {
    const text = message.trim();
    if ((!text && attachments.length === 0) || isSending) return;
    const attachmentIds = attachments.map((attachment) => attachment.id).filter(Boolean);

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
      const threadId = await ensureQuickThread();

      // Tell the agent what the user is looking at, without cluttering the
      // visible transcript: only the outgoing API payload for this one
      // message gets the block prepended, never the displayed bubble or
      // the prior-turn history resent on the next message. Only sent when
      // it's actually new information for this thread -- a fresh thread
      // (New chat, or a new page) always gets it; an unchanged page on an
      // established thread doesn't, since the agent already has it.
      const pageContext = derivePageContext(location.pathname);
      const previousPageContext = lastSentPageContextRef.current;
      const pageContextChanged = !previousPageContext
        || previousPageContext.threadId !== threadId
        || previousPageContext.page !== pageContext?.page
        || previousPageContext.id !== pageContext?.id;
      let outgoingText = text;
      if (pageContext && pageContextChanged) {
        outgoingText = `${formatPageContextBlock(pageContext)}\n\n${text}`;
        lastSentPageContextRef.current = { threadId, ...pageContext };
      }

      let assistantContent = '';
      const isStale = () => activeAssistantIdRef.current !== assistantId;
      await sendMessageStream(
        outgoingText,
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
        (status) => {
          if (isStale()) return;
          setHistory((currentHistory) => currentHistory.map((turn) => {
            if (turn.id !== assistantId) return turn;
            const content = assistantContent || turn.content || (status === 'cancelled'
              ? '(Response cancelled)'
              : '(No response text)');
            return {
              ...turn,
              content: status === 'cancelled' && content !== '(Response cancelled)'
                ? `${content} [Cancelled]`
                : content,
              isStreaming: false,
            };
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
        undefined,
        { thread_id: threadId, ...(attachmentIds.length > 0 ? { attachments: attachmentIds } : {}) },
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
  }, [ensureQuickThread, history, isSending, location.pathname, sendMessageStream]);
  useEffect(() => {
    if (!pendingSubmission?.message || isSending) {
      return;
    }

    onSubmissionConsumed?.(pendingSubmission.id);
    setComposerSeed((current) => ({ key: current.key + 1, message: '' }));
    void submit(pendingSubmission.message);
  }, [isSending, onSubmissionConsumed, pendingSubmission, submit]);

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
          threadId: scratchThreadId,
        }),
      );
      window.dispatchEvent(new CustomEvent('progenitor:load-chat-history', {
        detail: {
          messages: promotedMessages,
          threadId: scratchThreadId,
        },
      }));
    }
    onClose();
    if (!isOnChatPage) {
      navigate('/chat');
    }
  }, [config.storage.chatHistory, config.storage.quickChatHandoff, history, isOnChatPage, navigate, onClose, scratchThreadId]);

  const resetQuickChat = useCallback(() => {
    void cancelStream();
    activeAssistantIdRef.current = null;
    quickChatMemory.history = [];
    quickChatMemory.threadId = null;
    setHistory([]);
    setScratchThreadId(null);
    pendingDraftRef.current?.resolve(null);
    pendingDraftRef.current = null;
    sessionPromiseRef.current = null;
    setIsSending(false);
    setError('');
    setComposerSeed((current) => ({ key: current.key + 1, message: '' }));
    void startFreshQuickThread();
  }, [cancelStream, startFreshQuickThread]);

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
        <div ref={scrollEndRef} />
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
        enableSlashCommands={false}
        allowStop
        autoFocus
        keepFocusAfterSend
        initialMessage={composerSeed.message}
        enableAttachments
      />
    </TopNavPane>
  );
}

function WorkspacesSettingsRow() {
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

function MarketplacesSettingsRow() {
  const { getApiUrl } = useConfig();
  const dispatch = useDispatch();
  const marketplacesUrl = getApiUrl('marketplaces');
  const marketplaces = useSelector(selectMarketplaces);
  const [newName, setNewName] = useState('');
  const [newSource, setNewSource] = useState('');
  const [error, setError] = useState('');

  const handleRefresh = useCallback(async () => {
    setError('');
    try {
      await dispatch(fetchMarketplaces({ marketplacesUrl })).unwrap();
    } catch (err) {
      setError(err?.message || 'Failed to refresh marketplaces');
    }
  }, [dispatch, marketplacesUrl]);

  const handleSave = useCallback(async (nextMarketplaces) => {
    setError('');
    try {
      await dispatch(saveMarketplaces({ marketplacesUrl, marketplaces: nextMarketplaces })).unwrap();
    } catch (err) {
      setError(err?.message || 'Failed to save marketplaces');
    }
  }, [dispatch, marketplacesUrl]);

  const handleRemove = (name) => {
    const next = { ...marketplaces };
    delete next[name];
    void handleSave(next);
  };

  const handleAdd = () => {
    const name = newName.trim();
    const source = newSource.trim();
    if (!name || !source) {
      setError('Name and source URL are required');
      return;
    }
    if (Object.prototype.hasOwnProperty.call(marketplaces, name)) {
      setError(`Marketplace "${name}" already exists`);
      return;
    }
    const next = { ...marketplaces, [name]: { source, added_at: new Date().toISOString() } };
    void handleSave(next);
    setNewName('');
    setNewSource('');
    setError('');
  };

  return (
    <section className="set-section is-active">
      <header className="set-section__head">
        <h3>Marketplaces</h3>
        <p>Registered plugin marketplace sources.</p>
      </header>

      <div className="set-row">
        <ul className="set-workspace-list">
          {Object.entries(marketplaces).map(([name, entry]) => (
            <li key={name} className="set-workspace-list__item">
              <span className="set-workspace-list__host">{name}</span>
              <span className="set-workspace-list__current">{entry?.source}</span>
              <button type="button" className="set-btn set-btn--danger" onClick={() => handleRemove(name)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
        <span className="set-inline-control">
          <input
            className="set-text-input"
            value={newName}
            placeholder="my-marketplace"
            onChange={(event) => { setNewName(event.target.value); if (error) setError(''); }}
            onKeyDown={(event) => { if (event.key === 'Enter') handleAdd(); }}
          />
          <input
            className="set-text-input"
            value={newSource}
            placeholder="https://github.com/owner/repo"
            onChange={(event) => { setNewSource(event.target.value); if (error) setError(''); }}
            onKeyDown={(event) => { if (event.key === 'Enter') handleAdd(); }}
          />
          <button type="button" className="set-btn" onClick={handleAdd}>
            Add
          </button>
        </span>
        {error ? <p className="set-workspace-list__error">{error}</p> : null}
      </div>

      <div className="set-row">
        <button type="button" className="set-btn" onClick={handleRefresh}>
          Refresh
        </button>
      </div>
    </section>
  );
}

function SettingsPanel({ onClose, isPinned, onTogglePin, isOpen, onResetLiveUiState }) {
  const { getApiUrl, resetSettings } = useConfig();
  const dispatch = useDispatch();
  const preferences = useSelector(selectPreferences);
  const preferencesUrl = getApiUrl('preferences');
  const [section, setSection] = useState('general');
  const [resetConfirmationOpen, setResetConfirmationOpen] = useState(false);

  const preferencesSaveRequest = useSelector(selectPreferencesSaveRequest);
  return (
    <>
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

                <WorkspacesSettingsRow />

                <div className="set-row">
                  <div className="set-row__label">
                    <div className="set-row__title">Start view</div>
                    <div className="set-row__sub">The first screen after sign-in.</div>
                  </div>
                  <div className="set-seg" aria-label="Start view">
                    <button type="button" className="set-seg__btn">Overview</button>
                    <button type="button" className="set-seg__btn">Tasks</button>
                    <button type="button" className="set-seg__btn is-active">Chat</button>
                    <button type="button" className="set-seg__btn">Canvas</button>
                  </div>
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
                  <button type="button" className="set-btn set-btn--danger" onClick={() => setResetConfirmationOpen(true)}>Reset</button>
                </div>
              </section>
            ) : null}

            {section === 'appearance' ? (
              <section className="set-section is-active">
                <header className="set-section__head">
                  <h3>Appearance</h3>
                  <p>Density and font preferences.</p>
                </header>

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

            {section === 'marketplaces' ? (
              <MarketplacesSettingsRow />
            ) : null}

            {!['general', 'appearance', 'marketplaces'].includes(section) ? (
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
      {resetConfirmationOpen ? (
        <PopupModal
          eyebrow="Reset settings"
          title="Reset all settings?"
          message="This restores the default Progenitor settings and clears saved local preferences, including chat-related preferences."
          confirmLabel="Reset settings"
          onClose={() => setResetConfirmationOpen(false)}
          onConfirm={() => {
            resetSettings();
            // resetSettings() only clears ConfigContext/localStorage. Theme,
            // nav-collapse, pinned-panel, and notifications are also
            // backend-persisted now (see /config/preferences.json) - reset
            // those to their schema defaults too, preserving the profile
            // identity fields. Nav-collapse/pinned-panel also need their
            // live React state reset directly here, not left to the
            // reconciliation effect in AppRuntimeShell: that effect adopts
            // an incoming backend value only when it disagrees with the
            // localStorage CACHE, and resetSettings() just cleared that
            // cache to the same defaults being saved below - so the
            // effect would see no disagreement and never call
            // setIsNavCollapsed/setIsPinned, leaving a currently-collapsed
            // rail or pinned panel stuck open until an unrelated change.
            onResetLiveUiState?.();
            if (preferences.loaded) {
              dispatch(
                savePreferences({
                  preferencesUrl,
                  ...preferencesSaveRequest,
                  theme: 'system',
                  navCollapsed: false,
                  pinnedPanel: null,
                  notificationSounds: false,
                }),
              );
            }
            setResetConfirmationOpen(false);
          }}
        />
      ) : null}
    </>
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
      description: item.to === '/chat' ? 'Open chat workspace' : `Open ${item.to}`,
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

function InsightsPanel({ onClose, isPinned, onTogglePin, isOpen }) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const status = useSelector(selectUiStatus);
  const widgets = useSelector(selectUiWidgets);
  const [range, setRange] = useState('7d');
  const agentWidgets = widgets.filter((widget) => widget.type === 'agent' || widget.type === 'mcp');
  const scheduleWidgets = widgets.filter((widget) => widget.type === 'schedule');
  const running = agentWidgets.filter((widget) => ['running', 'busy', 'executing'].includes(widget.status || widget.data?.agentStatus)).length;
  const ready = agentWidgets.filter((widget) => (widget.status || widget.data?.agentStatus) === 'ready').length;
  const cycleCount = status?.cycle_count ?? 0;
  const totalActivity = Math.max(1, agentWidgets.length + scheduleWidgets.length + cycleCount);
  const runtimeOnline = Boolean(status?.progenitor_running);
  const runtimeSubtitle = status?.last_active
    ? `Status updated ${formatRelativeTimestamp(status.last_active)}`
    : 'Real-time status stream';
  const runtimeDelta = status?.last_active
    ? `Last active ${formatRelativeTimestamp(status.last_active)}`
    : runtimeOnline
      ? 'Live'
      : 'Awaiting signal';
  const refreshWidgets = useCallback(
    () => dispatch(fetchWidgets({ widgetsUrl: getApiUrl('widgets') })),
    [dispatch, getApiUrl],
  );

  return (
    <TopNavPane
      title="Token consumption"
      subtitle={runtimeSubtitle}
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
          <div className="ins-kpi__value">{runtimeOnline ? 'Online' : 'Offline'}</div>
          <div className="ins-kpi__delta ins-kpi__delta--muted">{runtimeDelta}</div>
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
  const dispatch = useDispatch();
  const { config, getApiUrl } = useConfig();
  const { setTheme } = useTheme();
  const preferences = useSelector(selectPreferences);
  const preferencesSaveRequest = useSelector(selectPreferencesSaveRequest);
  const preferencesUrl = getApiUrl('preferences');
  const [displayNameDraft, setDisplayNameDraft] = useState('');
  const [timezoneDraft, setTimezoneDraft] = useState('');
  const [profileError, setProfileError] = useState('');
  const themeSelectOptions = useMemo(
    () => config.theme.options.map((theme) => ({ value: theme, label: theme })),
    [config.theme.options],
  );
  // TIMEZONE_SELECT_OPTIONS is precomputed once at module load (419 entries);
  // only prepend a synthetic entry when the loaded value isn't in that list,
  // so this doesn't rebuild the array on every keystroke/render.
  const timezoneSelectOptions = useMemo(
    () =>
      timezoneDraft && !TIMEZONE_OPTIONS.includes(timezoneDraft)
        ? [{ value: timezoneDraft, label: timezoneDraft }, ...TIMEZONE_SELECT_OPTIONS]
        : TIMEZONE_SELECT_OPTIONS,
    [timezoneDraft],
  );

  useEffect(() => {
    if (!preferences.loaded) {
      return;
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- syncing local edit drafts from the loaded backend value, not derivable render state.
    setDisplayNameDraft(preferences.displayName);
    setTimezoneDraft(preferences.timezone);
  }, [preferences.loaded, preferences.displayName, preferences.timezone]);

  const saveProfile = useCallback(
    async (overrides = {}) => {
      // Guards against clobbering the stored document: this is a whole-
      // document PUT (see savePreferences/preferencesSaveRequest), and
      // before the initial fetch resolves preferencesSaveRequest is built
      // from the slice's un-loaded defaults, not what's actually saved.
      // Firing early would silently reset every other field (theme,
      // nav/pin state, ...) to those defaults. Matches the same guard on
      // persistNavCollapsed/handleTogglePin below. displayNameDraft/
      // timezoneDraft only populate once loaded too (see the sync effect
      // above), so in practice there's nothing meaningful to save yet.
      if (!preferences.loaded) {
        return;
      }
      setProfileError('');
      try {
        await dispatch(
          savePreferences({
            preferencesUrl,
            ...preferencesSaveRequest,
            displayName: displayNameDraft,
            timezone: timezoneDraft,
            ...overrides,
          }),
        ).unwrap();
      } catch {
        setProfileError('Failed to save profile.');
      }
    },
    [dispatch, preferencesUrl, preferencesSaveRequest, displayNameDraft, timezoneDraft, preferences.loaded],
  );

  // Commit-on-blur, not on every keystroke: typing a name shouldn't fire a
  // save per character. Skipped when nothing actually changed.
  const handleDisplayNameBlur = useCallback(() => {
    if (displayNameDraft === preferences.displayName) return;
    saveProfile();
  }, [displayNameDraft, preferences.displayName, saveProfile]);

  // Timezone is a discrete choice from a list, so - unlike free-text display
  // name - selecting a row IS the commit; no separate save step needed.
  // Pass the picked value as an override rather than relying on
  // timezoneDraft, since setTimezoneDraft hasn't re-rendered yet here.
  const handleTimezoneChange = useCallback(
    (value) => {
      setTimezoneDraft(value);
      saveProfile({ timezone: value });
    },
    [saveProfile],
  );

  const persistNotificationPreference = useCallback(
    (patch) => {
      // Same whole-document-clobber guard as saveProfile above.
      if (!preferences.loaded) {
        return;
      }
      dispatch(savePreferences({ preferencesUrl, ...preferencesSaveRequest, ...patch }));
    },
    [dispatch, preferencesUrl, preferencesSaveRequest, preferences.loaded],
  );

  return (
    <TopNavPane title="Account" className="topnav-pane--account" bodyClassName="account-pane" onClose={onClose} isPinned={isPinned} onTogglePin={onTogglePin} isOpen={isOpen}>
      <section className="set-section is-active">
        <header className="set-section__head">
          <h3>Profile</h3>
        </header>
        <div className="account-pane__id">
          <span className="account-pane__avatar" aria-hidden="true"><UserAvatarIcon /></span>
          <div className="account-pane__id-text">
            <div className="account-pane__name">{displayNameDraft || 'Unnamed'}</div>
            <div className="account-pane__email">me@tama.id.au</div>
          </div>
        </div>
        <div className="set-row">
          <span className="set-row__label">
            <span className="set-row__title">Display name</span>
            <span className="set-row__sub">Shown across the workspace and agent transcripts.</span>
          </span>
          <span className="set-inline-control">
            <input
              className="set-text-input"
              value={displayNameDraft}
              placeholder="Your name"
              onChange={(event) => setDisplayNameDraft(event.target.value)}
              onBlur={handleDisplayNameBlur}
              onKeyDown={(event) => {
                if (event.key === 'Enter') event.currentTarget.blur();
              }}
            />
          </span>
        </div>
        <div className="set-row">
          <span className="set-row__label">
            <span className="set-row__title">Timezone</span>
            <span className="set-row__sub">Used to schedule tasks and display timestamps.</span>
          </span>
          <span className="set-inline-control">
            <Select
              value={timezoneDraft}
              onChange={handleTimezoneChange}
              options={timezoneSelectOptions}
              searchable
              ariaLabel="Timezone"
            />
          </span>
        </div>
        {profileError ? <div className="task-alert">{profileError}</div> : null}
      </section>

      <section className="set-section is-active">
        <header className="set-section__head">
          <h3>Preferences</h3>
        </header>
        <div className="set-row">
          <div className="set-row__label">
            <div className="set-row__title">Theme</div>
            <div className="set-row__sub">Choose the interface palette.</div>
          </div>
          <Select
            value={config.theme.default}
            onChange={setTheme}
            options={themeSelectOptions}
            ariaLabel="Theme"
          />
        </div>
        <div className="set-row">
          <div className="set-row__label">
            <div className="set-row__title">Notification sounds</div>
            <div className="set-row__sub">Play a chime when an alert appears top-right.</div>
          </div>
          <label className={`set-switch ${preferences.notificationSounds ? 'is-on' : ''}`}>
            <input
              type="checkbox"
              checked={preferences.notificationSounds}
              onChange={(event) => persistNotificationPreference({ notificationSounds: event.target.checked })}
            />
            <span className="set-switch__knob" aria-hidden="true" />
          </label>
        </div>
      </section>
    </TopNavPane>
  );
}

// Transition durations must match the CSS transition on .topnav-pane.
const PANEL_EXIT_MS = 200;
const PANEL_ENTER_DELAY_MS = 16; // one rAF tick
function PanelHost({ activePanel, settingsOpen, quickChatConfig, quickChatSubmission, onQuickChatSubmissionConsumed, searchQuery, setActivePanel, setSettingsOpen, isPinned, onTogglePin, onResetLiveUiState }) {
  const widgets = useSelector(selectUiWidgets);
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
    if (!activePanel && !settingsOpen) return undefined;
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        if (settingsOpen) setSettingsOpen(false);
        else closePanel();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [activePanel, closePanel, settingsOpen, setSettingsOpen]);
  if (!displayed && !settingsOpen) return null;

  // Settings and Account are modal popups (centered window) — both portal to
  // document.body with a full-viewport scrim so the blurred backdrop covers
  // the whole app shell, including the top bar, not just the content area
  // below it (the right-side slide-in panels' scrim starts under the header
  // instead, since it never needs to blur the app's own chrome).
  const settingsOverlay = settingsOpen ? createPortal(
    <>
      <button type="button" className="modal-scrim" aria-label="Close settings" onClick={() => setSettingsOpen(false)} />
      <SettingsPanel onClose={() => setSettingsOpen(false)} isOpen onResetLiveUiState={onResetLiveUiState} />
    </>,
    document.body,
  ) : null;
  if (!displayed) return settingsOverlay;
  const accountOverlay = displayed === 'account' ? createPortal(
    <>
      <button type="button" className="modal-scrim" aria-label="Close account" onClick={closePanel} />
      <AccountPanel onClose={closePanel} isOpen={isOpen} />
    </>,
    document.body,
  ) : null;
  // Scrim: visible for the full lifetime of the pane (including enter/exit
  // transitions), full-width, so clicks never pass through and the page
  // behind is already grayed out before the panel finishes sliding into
  // place. Skipped when pinned (non-modal): the docked column stays clickable.
  const scrim = !isPinned ? (
    <button
      type="button"
      className="topnav-scrim is-open"
      aria-label="Close panel"
      onClick={closePanel}
    />
  ) : null;

  const shared = { isOpen, isPinned, onTogglePin };

  if (displayed === 'alerts') return <>{scrim}<AlertsPanel onClose={closePanel} {...shared} />{settingsOverlay}</>;
  if (displayed === 'quickChat') {
    return (
      <>
        {scrim}
        <QuickChatPanel
          onClose={closePanel}
          panelConfig={quickChatConfig}
          pendingSubmission={quickChatSubmission}
          onSubmissionConsumed={onQuickChatSubmissionConsumed}
          {...shared}
        />
        {settingsOverlay}
      </>
    );
  }
  if (displayed === 'settings') return settingsOverlay;
  if (displayed === 'account') return <>{accountOverlay}{settingsOverlay}</>;
  if (displayed === 'search') return <>{scrim}<SearchPanel query={searchQuery} widgets={widgets} onClose={closePanel} {...shared} />{settingsOverlay}</>;
  if (displayed === 'insights') return <>{scrim}<InsightsPanel onClose={closePanel} {...shared} />{settingsOverlay}</>;
  return null;
}

function AppRuntimeShell() {
  const dispatch = useDispatch();
  const realtime = useSelector(selectRealtime);
  const preferences = useSelector(selectPreferences);
  const preferencesSaveRequest = useSelector(selectPreferencesSaveRequest);
  const { getApiUrl } = useConfig();
  const preferencesUrl = getApiUrl('preferences');

  // Seed the whole profile once per app load - theme (useTheme), nav-collapse,
  // and pinned-panel all read from this same fetch.
  useEffect(() => {
    if (!realtime.connected) {
      return;
    }
    void dispatch(fetchPreferences({ preferencesUrl })).unwrap().catch(() => {});
  }, [dispatch, preferencesUrl, realtime.connected]);

  // Global alert toast: fires for every new backend alert on every page -
  // not just while Chat happens to be mounted. A chime plays alongside it
  // when preferences.notificationSounds is on (Account > Notification
  // sounds), matching /config/preferences.json.
  const { show } = useNotifications();
  const lastReceivedAlert = useSelector(selectLastReceivedAlert);
  const lastHandledAlertSeqRef = useRef(0);
  useEffect(() => {
    if (!lastReceivedAlert || lastReceivedAlert.seq === lastHandledAlertSeqRef.current) {
      return;
    }
    lastHandledAlertSeqRef.current = lastReceivedAlert.seq;
    show(lastReceivedAlert.type || 'info', lastReceivedAlert.title || 'Alert', lastReceivedAlert.message || '');
    if (preferences.notificationSounds) {
      playNotificationSound();
    }
  }, [lastReceivedAlert, preferences.notificationSounds, show]);

  const [activePanel, setActivePanel] = useState(() => {
    const cached = localStorage.getItem(PINNED_PANEL_STORAGE_KEY);
    return cached && PINNABLE_PANELS.has(cached) ? cached : null;
  });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [quickChatConfig, setQuickChatConfig] = useState(defaultQuickChatConfig);
  const searchQuery = '';
  const [quickChatSubmission, setQuickChatSubmission] = useState(null);
  const quickChatSubmissionIdRef = useRef(0);
  const quickChatInputRef = useRef(null);
  const [isNavCollapsed, setIsNavCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true',
  );
  const [isNavOpen, setIsNavOpen] = useState(false);
  // Mirrors activePanel's initial cache read: a panel only restores open+docked
  // (rather than open+floating) if it was the pinned one.
  const [isPinned, setIsPinned] = useState(() => {
    const cached = localStorage.getItem(PINNED_PANEL_STORAGE_KEY);
    return Boolean(cached && PINNABLE_PANELS.has(cached));
  });
  // Below 980px the sidebar becomes a fixed overlay drawer (CSS handles it).
  // Ensure the nav is not in icon-rail mode at those widths — the drawer is
  // either open (is-open class) or hidden off-screen, never a collapsed rail.
  // This is a transient viewport override, not a user preference change, so
  // it deliberately bypasses persistTogglePin/persistCollapse below.
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

  // Whatever panel is open+pinned right now is the one that reopens docked on
  // the next load.
  const pinnedPanelValue = activePanel && isPinned && PINNABLE_PANELS.has(activePanel) ? activePanel : null;

  // Live sync: whenever the backend-persisted profile's nav-collapse/pinned-
  // panel disagree with the local cache (initial load, an agent edit to
  // /config/preferences.json, the reset-settings button, or a change from
  // another device/tab), adopt them - UNLESS a local change happened before
  // the initial load resolved (see persistNavCollapsed/the pin-write-effect
  // below), in which case the user's fresher local choice wins and gets
  // pushed to the backend instead of getting silently overwritten by
  // whatever was already saved there. Safe from feedback loops otherwise:
  // the toggle handlers write localStorage synchronously before their own
  // save round-trips back through Redux, so by the time the snapshot
  // arrives the cached values already match and this is a no-op.
  const pendingNavChangeRef = useRef(false);
  const pendingPinChangeRef = useRef(false);
  useEffect(() => {
    if (!preferences.loaded) {
      return;
    }
    if (pendingNavChangeRef.current) {
      pendingNavChangeRef.current = false;
      if (preferences.navCollapsed !== isNavCollapsed) {
        dispatch(savePreferences({ preferencesUrl, ...preferencesSaveRequest, navCollapsed: isNavCollapsed }));
      }
    } else {
      const cachedNavCollapsed = localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true';
      if (preferences.navCollapsed !== cachedNavCollapsed) {
        setIsNavCollapsed(preferences.navCollapsed);
        localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(preferences.navCollapsed));
      }
    }

    if (pendingPinChangeRef.current) {
      pendingPinChangeRef.current = false;
      if (preferences.pinnedPanel !== pinnedPanelValue) {
        dispatch(savePreferences({ preferencesUrl, ...preferencesSaveRequest, pinnedPanel: pinnedPanelValue }));
      }
    } else {
      const cachedPinned = localStorage.getItem(PINNED_PANEL_STORAGE_KEY);
      if (preferences.pinnedPanel !== (cachedPinned && PINNABLE_PANELS.has(cachedPinned) ? cachedPinned : null)) {
        if (preferences.pinnedPanel && PINNABLE_PANELS.has(preferences.pinnedPanel)) {
          localStorage.setItem(PINNED_PANEL_STORAGE_KEY, preferences.pinnedPanel);
          setActivePanel(preferences.pinnedPanel);
          setIsPinned(true);
        } else {
          localStorage.removeItem(PINNED_PANEL_STORAGE_KEY);
          setIsPinned(false);
        }
      }
    }
    // isNavCollapsed/pinnedPanelValue/preferencesSaveRequest are read (for
    // the "push a pending local change" branch), never watched: this effect
    // exists to react to the BACKEND value changing, not the local one.
    // Depending on isNavCollapsed/pinnedPanelValue would re-run this effect
    // the instant a toggle fires - while Redux's preferences.navCollapsed/
    // pinnedPanel are still the pre-save (stale) value - and "adopt" that
    // stale value right back over the fresh local change, fighting the
    // write effects below. preferencesSaveRequest is memoized at the source
    // (selectPreferencesSaveRequest uses createSelector) so it wouldn't
    // cause that on its own, but it stays out of the deps anyway - this
    // effect has no reason to re-run for anything but a real backend change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preferences.loaded, preferences.navCollapsed, preferences.pinnedPanel, dispatch, preferencesUrl]);

  const persistNavCollapsed = useCallback(
    (next) => {
      localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(next));
      if (preferences.loaded) {
        dispatch(savePreferences({ preferencesUrl, ...preferencesSaveRequest, navCollapsed: next }));
      } else {
        pendingNavChangeRef.current = true;
      }
    },
    [dispatch, preferencesUrl, preferences.loaded, preferencesSaveRequest],
  );

  const handleTogglePin = useCallback(() => {
    setIsPinned((current) => !current);
  }, []);

  // Resets the live nav/pin UI directly, called from Settings' reset-to-
  // defaults confirm (see SettingsPanel) alongside the backend save -
  // needed because the reconciliation effect above only adopts an
  // incoming backend value when it disagrees with the localStorage cache,
  // and resetSettings() (ConfigContext) already cleared that cache to the
  // same defaults being saved, so the effect alone would never fire.
  const resetLiveUiState = useCallback(() => {
    setIsNavCollapsed(false);
    setIsPinned(false);
  }, []);

  // Persist pinnedPanelValue whenever it actually changes (pin toggled,
  // panel switched while pinned, or the panel closed/unpinned).
  const prevPinnedPanelRef = useRef(pinnedPanelValue);
  useEffect(() => {
    if (prevPinnedPanelRef.current === pinnedPanelValue) {
      return;
    }
    prevPinnedPanelRef.current = pinnedPanelValue;
    if (pinnedPanelValue) {
      localStorage.setItem(PINNED_PANEL_STORAGE_KEY, pinnedPanelValue);
    } else {
      localStorage.removeItem(PINNED_PANEL_STORAGE_KEY);
    }
    if (preferences.loaded) {
      dispatch(savePreferences({ preferencesUrl, ...preferencesSaveRequest, pinnedPanel: pinnedPanelValue }));
    } else {
      pendingPinChangeRef.current = true;
    }
  }, [pinnedPanelValue, dispatch, preferencesUrl, preferences.loaded, preferencesSaveRequest]);

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
        setActivePanel((current) => (current === 'quickChat' ? null : 'quickChat'));
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'q') {
        event.preventDefault();
        quickChatInputRef.current?.focus();
        quickChatInputRef.current?.select();
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
      const prompt = normalizeAiPrompt(event.detail?.prompt);
      if (prompt) {
        quickChatSubmissionIdRef.current += 1;
        setQuickChatSubmission({ id: quickChatSubmissionIdRef.current, message: prompt });
      }
      setQuickChatConfig(nextConfig);
      setActivePanel('quickChat');
    };
    window.addEventListener('progenitor:open-quick-chat', handleOpenQuickChat);
    return () => {
      window.removeEventListener('progenitor:open-quick-chat', handleOpenQuickChat);
    };
  }, []);

  const handleQuickChatSubmit = useCallback((event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const prompt = normalizeAiPrompt(form.get('prompt'));
    if (!prompt) {
      return;
    }
    event.currentTarget.reset();
    quickChatSubmissionIdRef.current += 1;
    setQuickChatSubmission({ id: quickChatSubmissionIdRef.current, message: prompt });
    setQuickChatConfig(defaultQuickChatConfig);
    setActivePanel('quickChat');
  }, []);
  const handleQuickChatSubmissionConsumed = useCallback(() => {
    setQuickChatSubmission(null);
  }, []);
  const handleOpenPanel = useCallback((panel) => {
    if (panel === 'insights') {
      // Widgets refreshed on mount by InsightsPanel
    }
  }, []);
  return (
    <div className={shellClass}>
      <PrimaryNav
        isCollapsed={isNavCollapsed}
        isOpen={isNavOpen}
        onClose={() => setIsNavOpen(false)}
        onToggleCollapse={() => setIsNavCollapsed((current) => {
          const next = !current;
          persistNavCollapsed(next);
          return next;
        })}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenAccount={() => setActivePanel('account')}
      />
      <Header
        activePanel={activePanel}
        setActivePanel={setActivePanel}
        onSubmitQuickChat={handleQuickChatSubmit}
        onOpenPanel={handleOpenPanel}
        onOpenQuickChat={() => {
          setQuickChatConfig(defaultQuickChatConfig);
        }}
        onToggleNav={() => setIsNavOpen((current) => !current)}
        quickChatInputRef={quickChatInputRef}
      />
      <main className="app-shell__content">
        <Outlet />
      </main>
      <PanelHost
        activePanel={activePanel}
        settingsOpen={settingsOpen}
        quickChatConfig={quickChatConfig}
        quickChatSubmission={quickChatSubmission}
        setSettingsOpen={setSettingsOpen}
        onQuickChatSubmissionConsumed={handleQuickChatSubmissionConsumed}
        searchQuery={searchQuery}
        setActivePanel={setActivePanel}
        isPinned={isPinned}
        onTogglePin={handleTogglePin}
        onResetLiveUiState={resetLiveUiState}
      />
      <NotificationContainer />
    </div>
  );
}

export { AppRuntimeShell };
export default AppRuntimeShell;
