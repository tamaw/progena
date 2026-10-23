import { useCallback, useEffect, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import { selectChatIndicators } from '../features/chat/state/chats';
import { selectInboxUnreadCount } from '../features/inbox/state';
import { selectUnseenNavRoutes } from '../store/uiDataSlice';
import { setActiveNavRoute } from '../store/navigationActions';
import { CHAT_STATUS } from '../features/chat/models/chatStatus';
import { createSolutionFromName, createProjectFromName, loadStoredSolutions, saveStoredSolutions } from '../features/projects/solutions';
import { ChevronLeftIcon, ChevronRightIcon, RailSettingsIcon, UserAvatarIcon } from '../components/navIcons';
import { navGroups, NAV_ACTIVITY_ROUTES } from './navigation';
import { SidebarCreateModal } from '../features/projects/SidebarCreateModal';

export function PrimaryNav({ isCollapsed, isOpen, onClose, onToggleCollapse, onOpenSettings, onOpenAccount }) {
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
        <Link to="/chat" className="app-shell__brand-link" aria-label="Progena AI home">
          <span className="app-shell__brand-mark" aria-hidden="true">🧬</span>
          <span className="app-shell__brand-name">Progena<span className="app-shell__brand-ai">AI</span></span>
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
