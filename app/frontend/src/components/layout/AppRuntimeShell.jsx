import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { selectChatIndicators } from '../../store/uiDataSlice';
import { useAlertCenter } from '../../context/AlertCenterContext';
import { useChromeRuntime } from '../../context/ChromeRuntimeContext';
import { useConfig } from '../../context/ConfigContext';
import { useTheme } from '../../hooks/useTheme';
import { useApi } from '../../hooks/useApi';
import { renderMarkdownToHtml } from '../../lib/markdown';
import ChatInput from '../chat/ChatInput';
import NotificationContainer from '../common/NotificationContainer';
import {
  createSolutionFromName,
  createProjectFromName,
  loadStoredSolutions,
  saveStoredSolutions,
} from '../../data/solutions';

// Grouped by owner/level (see the entity-relationship model):
//   Operate  — the surfaces you work in (Progenitor chat, overview, projects)
//   Automate — runtime: triggers, schedules, I/O, work in flight
//   Delegate — agents and the reusable building blocks they're composed of
// Sessions and Logs are intentionally dropped from the rail (still routable).
const navGroups = [
  {
    id: 'operate',
    label: 'Operate',
    items: [
      { to: '/', end: true, icon: <ChatNavIcon />, label: 'Chat', dot: true },
      { to: '/dashboard', icon: <DashboardNavIcon />, label: 'Dashboard', countKey: 'widgets' },
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
      { to: '/schedules', icon: <SchedulesNavIcon />, label: 'Schedules', countKey: 'schedules' },
    ],
  },
  {
    id: 'delegate',
    label: 'Delegate',
    items: [
      { to: '/agents', icon: <AgentsNavIcon />, label: 'Agents', countKey: 'agents' },
      { to: '/channels', icon: <ChannelsNavIcon />, label: 'Channels' },
      { to: '/models', icon: <ModelsNavIcon />, label: 'Models' },
      { to: '/tools', icon: <ToolsNavIcon />, label: 'Tools', countKey: 'tools' },
      { to: '/skills', icon: <SkillsNavIcon />, label: 'Skills', countKey: 'skills' },
      { to: '/knowledge', icon: <KnowledgeNavIcon />, label: 'Knowledge', countKey: 'knowledge' },
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
const quickPrompts = [
  'Summarize the latest workspace activity',
  'What should I work on next?',
  'Draft a short status update',
];

function UserAvatarIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
}

function RailSettingsIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function DashboardNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="3" width="7" height="9" rx="1" />
      <rect x="14" y="3" width="7" height="5" rx="1" />
      <rect x="14" y="12" width="7" height="9" rx="1" />
      <rect x="3" y="16" width="7" height="5" rx="1" />
    </svg>
  );
}

function ChatNavIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path
        d="M1.5 4.6A1.7 1.7 0 0 1 3.2 2.9h9.6A1.7 1.7 0 0 1 14.5 4.6v5.2a1.7 1.7 0 0 1-1.7 1.7H5.2L2.8 14v-2.5h-1.3V4.6z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function WorkspaceNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="3" width="7.5" height="7.5" rx="1.6" />
      <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6" />
      <path d="M6.75 10.5v6.75h6.75" />
    </svg>
  );
}

function SequencesNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="6" cy="19" r="3" />
      <path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15" />
      <circle cx="18" cy="5" r="3" />
    </svg>
  );
}

function ProjectsNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
    </svg>
  );
}

function ModelsNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" />
      <path d="m3.3 7 8.7 5 8.7-5" />
      <path d="M12 22V12" />
    </svg>
  );
}

function AgentsNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 8V4H8" />
      <rect width="16" height="12" x="4" y="8" rx="2" />
      <path d="M2 14h2" />
      <path d="M20 14h2" />
      <path d="M15 13v2" />
      <path d="M9 13v2" />
    </svg>
  );
}

function ChannelsNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14 9a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z" />
      <path d="M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1" />
    </svg>
  );
}

function ToolsNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m15 12-8.373 8.373a1 1 0 1 1-3-3L12 9" />
      <path d="m18 15 4-4" />
      <path d="m21.5 11.5-1.914-1.914A2 2 0 0 1 19 8.172V7l-2.26-2.26a6 6 0 0 0-4.202-1.756L9 2.96l.92.82A6.18 6.18 0 0 1 12 8.4V10l2 2h1.172a2 2 0 0 1 1.414.586L18.5 14.5" />
    </svg>
  );
}

function SessionsNavIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 5.2V8l2.1 1.4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function SchedulesNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M8 2v4" />
      <path d="M16 2v4" />
      <path d="M3 10h18" />
      <path d="M8 15h.01" />
      <path d="M12 15h.01" />
      <path d="M16 15h.01" />
    </svg>
  );
}

function TasksNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m3 17 2 2 4-4" />
      <path d="m3 7 2 2 4-4" />
      <path d="M13 6h8" />
      <path d="M13 12h8" />
      <path d="M13 18h8" />
    </svg>
  );
}

function RepositoriesNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="6" x2="6" y1="3" y2="15" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path d="M18 9a9 9 0 0 1-9 9" />
    </svg>
  );
}

function KnowledgeNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="18" cy="5" r="3" />
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="19" r="3" />
      <path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4" />
    </svg>
  );
}

function SkillsNavIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
      <path d="M14 2v4a2 2 0 0 0 2 2h4" />
      <path d="M16 13H8" />
      <path d="M16 17H8" />
      <path d="M10 9H8" />
    </svg>
  );
}

function LogsNavIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M2.5 11.5c1.7 0 1.7-7 3.4-7s1.7 7 3.4 7 1.7-7 3.4-7" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M11 11l3 3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function InsightsIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M2 13h12M4 11V7M7 11V3M10 11V8M13 11V5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function SettingsGeneralIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 4v4l2.5 1.5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

function SettingsAppearanceIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 2v12M2 8h12" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

function SettingsAgentsIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <rect x="2.5" y="3" width="11" height="9" rx="2" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="6" cy="7.5" r="0.9" fill="currentColor" />
      <circle cx="10" cy="7.5" r="0.9" fill="currentColor" />
    </svg>
  );
}

function SettingsNotificationsIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="M3.5 11.5h9l-1-1.5V7a3.5 3.5 0 0 0-7 0v3l-1 1.5zM6.5 13a1.5 1.5 0 0 0 3 0" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

function SettingsKeyboardIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <rect x="1.5" y="4" width="13" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M4 7h.01M7 7h.01M10 7h.01M4 10h7" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

function SettingsIntegrationsIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="M6 6V3.5a1.5 1.5 0 0 1 3 0V6M10 8h2.5a1.5 1.5 0 0 1 0 3H10M5 8H2.5a1.5 1.5 0 0 0 0 3H5M7 10v2.5a1.5 1.5 0 0 0 3 0V10M5 8v0a3 3 0 0 1 6 0v0z" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

function SettingsTeamIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <circle cx="6" cy="6" r="2.2" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="11" cy="7" r="1.7" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M2 13c0-2 1.8-3.5 4-3.5s4 1.5 4 3.5M10 13c0-1.4 1-2.5 2.5-2.5s2.5 1.1 2.5 2.5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

function SettingsAboutIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 7v4M8 5v.01" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function QuickChatIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path
        d="M2.5 4.2A1.7 1.7 0 0 1 4.2 2.5h7.6A1.7 1.7 0 0 1 13.5 4.2v5.6a1.7 1.7 0 0 1-1.7 1.7H6.5L3.5 14v-2.5h-1V4.2z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function AlertsIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path
        d="M3.5 11.5h9l-1-1.5V7a3.5 3.5 0 0 0-7 0v3l-1 1.5zM6.5 13a1.5 1.5 0 0 0 3 0"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MoonIcon({ className }) {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" className={className} aria-hidden="true">
      <path
        d="M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SunIcon({ className }) {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" className={className} aria-hidden="true">
      <circle cx="8" cy="8" r="3" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path
        d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  );
}

function ChevronDownIcon() {
  return (
    <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
      <path d="M3 5l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function ChevronLeftIcon() {
  return (
    <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
      <path d="M7.5 3 4.5 6l3 3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
      <path d="M4.5 3 7.5 6l-3 3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

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

function CloseIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M13.5 8a5.5 5.5 0 1 1-1.7-3.98M13.5 2.5v3h-3" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ExpandUpIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M5 11 11 5M7 5h4v4" fill="none" stroke="currentColor" strokeWidth="1.45" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4.5 3.5h7A1.5 1.5 0 0 1 13 5v6a1.5 1.5 0 0 1-1.5 1.5h-7A1.5 1.5 0 0 1 3 11V5a1.5 1.5 0 0 1 1.5-1.5z" fill="none" stroke="currentColor" strokeWidth="1.1" opacity=".55" />
    </svg>
  );
}

function PanelIconButton({ label, children, onClick, type = 'button', disabled = false }) {
  return (
    <button type={type} className="topnav-pane__icon-btn" aria-label={label} title={label} onClick={onClick} disabled={disabled}>
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
  className = '',
  bodyClassName = 'topnav-pane__body',
}) {
  return (
    <>
      <button type="button" className="topnav-scrim is-open" aria-label={`Close ${title}`} onClick={onClose} />
      <aside className={`topnav-pane is-open ${className}`.trim()} aria-modal="true" role="dialog" aria-labelledby={`${title.replace(/\s+/g, '-').toLowerCase()}-title`}>
        <header className="topnav-pane__head">
          <div className="topnav-pane__heading">
            <h2 className="topnav-pane__title" id={`${title.replace(/\s+/g, '-').toLowerCase()}-title`}>
              {icon ? <span className="topnav-pane__title-icon">{icon}</span> : null}
              {title}
              {badge ? <span className="topnav-pane__badge">{badge}</span> : null}
            </h2>
            {subtitle ? <p className="topnav-pane__sub">{subtitle}</p> : null}
          </div>
          <div className="topnav-pane__actions">
            {actions}
            <PanelIconButton label={`Close ${title}`} onClick={onClose}><CloseIcon /></PanelIconButton>
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

function Header({ activePanel, setActivePanel, onSearch, onOpenPanel, onToggleNav }) {
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
          label="Insights"
          icon={<InsightsIcon />}
          active={activePanel === 'insights'}
          onClick={() => openPanel('insights')}
        />
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
          label="Quick chat"
          icon={<QuickChatIcon />}
          active={activePanel === 'quickChat'}
          onClick={() => openPanel('quickChat')}
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
    return {
      agents: Math.max(7, agentCount),
      knowledge: Math.max(6, runtime.widgets.length),
      repositories: 6,
      schedules: 6,
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
          aria-label={isCollapsed ? 'Expand navigation' : 'Collapse navigation'}
          title={isCollapsed ? 'Expand navigation' : 'Collapse navigation'}
          onClick={onToggleCollapse}
        >
          {isCollapsed ? <ChevronRightIcon /> : <ChevronLeftIcon />}
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

function AlertsPanel({ onClose }) {
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

function QuickChatPanel({ onClose }) {
  const { createSession, sendMessageStream, cancelStream } = useApi();
  const { config } = useConfig();
  const navigate = useNavigate();
  const [history, setHistory] = useState([]);
  const [scratchSession, setScratchSession] = useState(null);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState('');
  const [composerSeed, setComposerSeed] = useState({ key: 0, message: '' });
  const sessionPromiseRef = useRef(null);
  const isQuickChatMountedRef = useRef(false);
  const activeAssistantIdRef = useRef(null);

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

  useEffect(() => {
    isQuickChatMountedRef.current = true;
    let cancelled = false;
    startFreshQuickSession()
      .then((session) => {
        if (!cancelled && session) {
          setScratchSession(session);
        }
        return session;
      });

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
        text,
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
  }, [ensureQuickSession, history, isSending, sendMessageStream]);

  const promoteToChat = useCallback(() => {
    if (history.length === 0) return;

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
    onClose();
    navigate('/');
  }, [config.storage.chatHistory, config.storage.quickChatHandoff, history, navigate, onClose, scratchSession?.session_id]);

  const resetQuickChat = useCallback(() => {
    cancelStream();
    activeAssistantIdRef.current = null;
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
      title="Quick chat"
      icon={<QuickChatIcon />}
      className="topnav-pane--quick"
      bodyClassName="qchat-pane__body"
      onClose={onClose}
      actions={(
        <>
          <PanelIconButton label="Reset quick chat" onClick={resetQuickChat} disabled={isSending}>
            <RefreshIcon />
          </PanelIconButton>
          <PanelIconButton label="Send to chat" onClick={promoteToChat} disabled={history.length === 0 || !scratchSession || isSending}>
            <ExpandUpIcon />
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
          {quickPrompts.map((prompt) => (
            <button key={prompt} type="button" className="qchat-chip" onClick={() => selectPrompt(prompt)}>
              {prompt}
            </button>
          ))}
        </div>
      ) : null}
      <ChatInput
        key={composerSeed.key}
        className="qchat-input"
        placeholder="Ask anything..."
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

function SettingsPanel({ onClose }) {
  const { config, setApiUrl, setNotifications, setSounds, setTheme } = useConfig();
  const [apiUrl, setApiUrlDraft] = useState(config.api.baseUrl);
  const [section, setSection] = useState('general');

  return (
    <TopNavPane title="Settings" className="topnav-pane--settings" bodyClassName="settings-pane__body" onClose={onClose}>
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

function SearchPanel({ query, widgets, onClose }) {
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
    <TopNavPane title="Search" subtitle="Visual command palette" className="topnav-pane--search" onClose={onClose}>
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

function InsightsPanel({ runtime, onClose }) {
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
      className="topnav-pane--insights"
      bodyClassName="ins-pane__body"
      actions={(
        <>
          <SegmentedControl
            className="ins-range"
            ariaLabel="Insight range"
            value={range}
            onChange={setRange}
            items={insightRanges.map((value) => ({ value, label: value }))}
          />
          <button type="button" className="topnav-pane__text-btn" onClick={refreshWidgets}>Refresh</button>
        </>
      )}
    >
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

function AccountPanel({ onClose }) {
  return (
    <TopNavPane title="Account" className="topnav-pane--account" bodyClassName="account-pane" onClose={onClose}>
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

function PanelHost({ activePanel, searchQuery, setActivePanel, runtime }) {
  const closePanel = useCallback(() => setActivePanel(null), [setActivePanel]);

  useEffect(() => {
    if (!activePanel) return undefined;
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') closePanel();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [activePanel, closePanel]);

  if (activePanel === 'alerts') return <AlertsPanel onClose={closePanel} />;
  if (activePanel === 'quickChat') return <QuickChatPanel onClose={closePanel} />;
  if (activePanel === 'settings') return <SettingsPanel onClose={closePanel} />;
  if (activePanel === 'account') return <AccountPanel onClose={closePanel} />;
  if (activePanel === 'search') {
    return <SearchPanel query={searchQuery} widgets={runtime.widgets} onClose={closePanel} />;
  }
  if (activePanel === 'insights') {
    return <InsightsPanel runtime={runtime} onClose={closePanel} />;
  }
  return null;
}

function AppRuntimeShell() {
  const [activePanel, setActivePanel] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isNavCollapsed, setIsNavCollapsed] = useState(false);
  const [isNavOpen, setIsNavOpen] = useState(false);
  const runtime = useChromeRuntime();

  useEffect(() => {
    const handleKeyDown = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
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
    <div className={`app-shell ${isNavCollapsed ? 'is-nav-collapsed' : ''}`}>
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
        onToggleNav={() => setIsNavOpen((current) => !current)}
      />
      <main className="app-shell__content">
        <Outlet />
      </main>
      {isNavOpen ? <button type="button" className="app-shell__nav-scrim" aria-label="Close navigation" onClick={() => setIsNavOpen(false)} /> : null}
      <PanelHost
        activePanel={activePanel}
        searchQuery={searchQuery}
        setActivePanel={setActivePanel}
        runtime={runtime}
      />
      <NotificationContainer />
    </div>
  );
}

export { AppRuntimeShell };
export default AppRuntimeShell;
