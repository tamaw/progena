import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import EventDebugPanel from './EventDebugPanel';
import { navItems } from './navigation';
import { TopNavPane } from '../components/panels/PanelFrame';
import { AlertsPanel } from '../features/alerts/AlertsPanel';
import { QuickChatPanel } from '../features/chat/QuickChatPanel';
import { SettingsPanel } from '../features/settings/SettingsPanel';
import { SearchPanel } from '../features/search/SearchPanel';
import { InsightsPanel } from '../features/insights/InsightsPanel';
import { AccountPanel } from '../features/settings/AccountPanel';

// Transition durations must match the CSS transition on .topnav-pane.
const PANEL_EXIT_MS = 200;

const PANEL_ENTER_DELAY_MS = 16;

// one rAF tick
export function PanelHost({ activePanel, settingsOpen, quickChatConfig, quickChatSubmission, quickChatDraft, quickChatInlineActive, onQuickChatSubmissionConsumed, onQuickChatDraftConsumed, onQuickChatInlineSettled, searchQuery, setActivePanel, setSettingsOpen, isPinned, onTogglePin, onResetLiveUiState }) {
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
  if (!displayed && !settingsOpen && !quickChatInlineActive) return null;

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
  const quickChatVisible = displayed === 'quickChat';
  const quickChatHost = quickChatVisible || quickChatInlineActive ? (
    <QuickChatPanel
      onClose={closePanel}
      panelConfig={quickChatConfig}
      pendingSubmission={quickChatSubmission}
      onSubmissionConsumed={onQuickChatSubmissionConsumed}
      pendingComposerDraft={quickChatDraft}
      onComposerDraftConsumed={onQuickChatDraftConsumed}
      onInlineSubmissionSettled={onQuickChatInlineSettled}
      isBackground={!quickChatVisible}
      isOpen={quickChatVisible && isOpen}
      isPinned={quickChatVisible && isPinned}
      onTogglePin={onTogglePin}
    />
  ) : null;
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

  let panelContent = null;
  if (displayed === 'alerts') panelContent = <AlertsPanel onClose={closePanel} {...shared} />;
  if (displayed === 'search') panelContent = <SearchPanel navigationItems={navItems} query={searchQuery} onClose={closePanel} {...shared} />;
  if (displayed === 'insights') panelContent = <InsightsPanel onClose={closePanel} {...shared} />;
  if (displayed === 'events') panelContent = (
    <TopNavPane title="Event stream" subtitle="Redux UI events · Ctrl/Cmd+Shift+E" onClose={closePanel} bodyClassName="event-debug" {...shared}>
      <EventDebugPanel />
    </TopNavPane>
  );
  const showScrim = ['alerts', 'quickChat', 'search', 'insights', 'events'].includes(displayed);
  return (
    <>
      {quickChatHost}
      {showScrim ? scrim : null}
      {panelContent}
      {accountOverlay}
      {settingsOverlay}
    </>
  );
}
