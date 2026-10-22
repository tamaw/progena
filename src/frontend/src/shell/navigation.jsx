import { localDesignLibraryEnabled } from '../lib/localDesignLibrary';
import { AgentsNavIcon, InstructionsNavIcon, ChannelsNavIcon, ChatNavIcon, InboxNavIcon, DashboardNavIcon, InsightsIcon, KnowledgeNavIcon, CanvasesNavIcon, RepositoriesNavIcon, SchedulesNavIcon, RoutinesNavIcon, PluginsNavIcon, ProfilesNavIcon, TasksNavIcon, ToolsNavIcon, WorkspaceNavIcon } from '../components/navIcons';

// Grouped by owner/level (see the entity-relationship model):
//   Operate  — the surfaces you work in (Progenitor chat, overview, projects)
//   Automate — runtime: triggers, schedules, I/O, work in flight
//   Delegate — agents and the reusable building blocks they're composed of
// Sessions and Logs are intentionally dropped from the rail (still routable).
export const navGroups = [
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
      { to: '/canvases', icon: <CanvasesNavIcon />, label: 'Canvases', trackActivity: true },
      { to: '/insights', icon: <InsightsIcon />, label: 'Insights' },
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
      { to: '/instructions', icon: <InstructionsNavIcon />, label: 'Instructions', trackActivity: true },
    ],
  },
  {
    id: 'facilitate',
    label: 'Facilitate',
    items: [
      { to: '/plugins', icon: <PluginsNavIcon />, label: 'Plugins', trackActivity: true },
      { to: '/profiles', icon: <ProfilesNavIcon />, label: 'Profiles', trackActivity: true },
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
export const navItems = navGroups.flatMap((group) => group.items);

// Pages whose nav dot reflects "an event arrived while you weren't there"
// (see uiDataSlice's NAV_ACTIVITY_EVENT_ROUTES for the event->route mapping).
export const NAV_ACTIVITY_ROUTES = navItems.filter((item) => item.trackActivity).map((item) => item.to);

const breadcrumbRoutes = [
  ...navItems,
  { to: '/sessions', label: 'Sessions' },
  { to: '/logs', label: 'Logs' },
  ...(localDesignLibraryEnabled ? [{ to: '/design', label: 'Design library' }] : []),
];

export function getPageLabel(pathname) {
  if (pathname.startsWith('/solutions') || pathname.startsWith('/projects')) {
    return 'Projects';
  }
  const match = breadcrumbRoutes.find((item) => pathname.startsWith(item.to));
  return match?.label ?? 'Chat';
}
