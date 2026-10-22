import { useCallback } from 'react';
import { useSelector } from 'react-redux';
import { useLocation } from 'react-router-dom';
import { selectUiAlerts } from '../features/alerts/state';
import { useTheme } from '../features/settings/useTheme';
import { AlertsIcon, ChevronRightIcon, InsightsIcon, MoonIcon, ProgenitorAiIcon, QuickChatIcon, SearchIcon, SunIcon } from '../components/navIcons';
import { getPageLabel } from './navigation';
import { ChromeIconButton } from '../components/panels/PanelFrame';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';

export function Header({ activePanel, setActivePanel, onSubmitQuickChat, onOpenPanel, onOpenQuickChat, onToggleNav, quickChatInputRef }) {
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
          label="Search"
          icon={<SearchIcon />}
          active={activePanel === 'search'}
          onClick={() => openPanel('search')}
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
