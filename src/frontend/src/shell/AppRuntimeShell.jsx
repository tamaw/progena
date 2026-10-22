import { useCallback, useEffect, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { Outlet } from 'react-router-dom';
import { fetchPreferences, savePreferences, selectPreferences, selectPreferencesSaveRequest } from '../features/settings/state/preferences';
import { selectLastReceivedAlert } from '../features/alerts/state';
import { selectLastRoutineRunNotification } from '../features/agents/state/agents';
import { selectRealtime } from '../realtime/realtimeSlice';
import { useConfig } from '../context/ConfigContext';
import { useNotifications } from '../context/NotificationContext';
import { playNotificationSound } from '../lib/notificationSound';
import NotificationContainer from '../components/NotificationContainer';
import ConnectionRecoveryModal from './ConnectionRecoveryModal';
import { defaultQuickChatConfig, normalizeAiPrompt, normalizeQuickChatConfig } from '../features/chat/models/quickChatConfig';
import { Header } from './Header';
import { PrimaryNav } from './PrimaryNav';
import { PanelHost } from './PanelHost';
import { quickChatMemory } from '../features/chat/models/quickChatMemory';

// Only these panels persist as preferences.pinnedPanel. The hidden event
// debugger may dock for this session, but never restores itself on reload.
const PINNABLE_PANELS = new Set(['quickChat', 'alerts', 'insights']);

const SIDEBAR_COLLAPSED_STORAGE_KEY = 'progenitor_sidebar_collapsed';

const PINNED_PANEL_STORAGE_KEY = 'progenitor_pinned_panel';

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

export function AppRuntimeShell() {
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
  const lastRoutineRun = useSelector(selectLastRoutineRunNotification);
  const lastHandledRoutineRef = useRef(null);
  useEffect(() => {
    if (!lastRoutineRun || lastHandledRoutineRef.current === lastRoutineRun.request_id) return;
    lastHandledRoutineRef.current = lastRoutineRun.request_id;
    const failed = lastRoutineRun.status === 'failed';
    show(failed ? 'error' : 'success', failed ? 'Routine failed' : 'Routine completed',
      lastRoutineRun.error || lastRoutineRun.name || 'Routine finished successfully');
    if (preferences.notificationSounds) playNotificationSound();
  }, [lastRoutineRun, preferences.notificationSounds, show]);
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
  const [quickChatDraft, setQuickChatDraft] = useState(null);
  const quickChatSubmissionIdRef = useRef(0);
  const pendingQuickChatRef = useRef(null);
  const [quickChatInlineActive, setQuickChatInlineActive] = useState(false);
  const quickChatInlineBusyRef = useRef(false);
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
      } else {
        // Leaving mobile width: the drawer's open/closed state is meaningless
        // at desktop width (no drawer, just the grid-column rail) — a stale
        // isNavOpen=true would otherwise desync PrimaryNav's isCollapsed prop
        // (forced false while "open") from the outer grid's collapsed-width
        // column, rendering full labels squeezed into a 52px rail.
        setIsNavOpen(false);
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
      if ((event.metaKey || event.ctrlKey) && event.shiftKey && !event.altKey && event.code === 'KeyE') {
        event.preventDefault();
        if (!event.repeat) setActivePanel((current) => (current === 'events' ? null : 'events'));
        return;
      }
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
      const inline = event.detail?.inline === true;
      const clientId = typeof event.detail?.clientId === 'string' ? event.detail.clientId : null;
      if (inline && quickChatInlineBusyRef.current) {
        window.dispatchEvent(new CustomEvent('progenitor:quick-chat-inline-status', {
          detail: { clientId, status: 'failed', error: 'Another quick-chat edit is already running.' },
        }));
        return;
      }
      if (prompt) {
        if (pendingQuickChatRef.current) {
          if (inline) {
            window.dispatchEvent(new CustomEvent('progenitor:quick-chat-inline-status', {
              detail: { clientId, status: 'failed', error: 'Quick chat is busy. Try again when it finishes.' },
            }));
          } else {
            quickChatSubmissionIdRef.current += 1;
            setQuickChatDraft({ id: quickChatSubmissionIdRef.current, message: prompt });
          }
          if (!inline) {
            setQuickChatConfig(nextConfig);
            setActivePanel('quickChat');
          }
          return;
        }
        quickChatSubmissionIdRef.current += 1;
        const pending = { id: quickChatSubmissionIdRef.current, message: prompt, inline, clientId };
        pendingQuickChatRef.current = pending;
        setQuickChatSubmission(pending);
        if (inline) {
          setQuickChatConfig(nextConfig);
          quickChatInlineBusyRef.current = true;
          setQuickChatInlineActive(true);
        }
      }
      const draft = normalizeAiPrompt(event.detail?.draft);
      if (draft) {
        quickChatSubmissionIdRef.current += 1;
        setQuickChatDraft({ id: quickChatSubmissionIdRef.current, message: draft });
      }
      if (!inline) {
        setQuickChatConfig(nextConfig);
        setActivePanel('quickChat');
      }
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
    const pending = { id: quickChatSubmissionIdRef.current, message: prompt };
    if (pendingQuickChatRef.current) {
      setQuickChatDraft(pending);
    } else {
      pendingQuickChatRef.current = pending;
      setQuickChatSubmission(pending);
    }
    setQuickChatConfig(defaultQuickChatConfig);
    setActivePanel('quickChat');
  }, []);
  const handleQuickChatSubmissionConsumed = useCallback((id) => {
    if (pendingQuickChatRef.current?.id === id) pendingQuickChatRef.current = null;
    setQuickChatSubmission((current) => current?.id === id ? null : current);
  }, []);
  const handleQuickChatDraftConsumed = useCallback(() => {
    setQuickChatDraft(null);
  }, []);
  const handleQuickChatInlineSettled = useCallback((clientId, status, error) => {
    quickChatInlineBusyRef.current = false;
    setQuickChatInlineActive(false);
    window.dispatchEvent(new CustomEvent('progenitor:quick-chat-inline-status', {
      detail: { clientId, status, error },
    }));
  }, []);
  const handleOpenPanel = useCallback(() => {}, []);
  return (
    <div className={shellClass}>
      <PrimaryNav
        // The collapsed icon-only rail is a desktop space-saving preference;
        // it's meaningless for the mobile/tablet slide-over drawer (which is
        // already a full-width overlay), and the drawer's own toggle button
        // is repurposed to "close" while open, so a collapsed desktop
        // preference would otherwise make the labels permanently unreachable
        // at narrow viewports. Force expanded whenever the drawer is open.
        isCollapsed={isNavCollapsed && !isNavOpen}
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
        quickChatDraft={quickChatDraft}
        quickChatInlineActive={quickChatInlineActive}
        onQuickChatInlineSettled={handleQuickChatInlineSettled}
        setSettingsOpen={setSettingsOpen}
        onQuickChatSubmissionConsumed={handleQuickChatSubmissionConsumed}
        onQuickChatDraftConsumed={handleQuickChatDraftConsumed}
        searchQuery={searchQuery}
        setActivePanel={setActivePanel}
        isPinned={isPinned}
        onTogglePin={handleTogglePin}
        onResetLiveUiState={resetLiveUiState}
      />
      <NotificationContainer />
      <ConnectionRecoveryModal />
    </div>
  );
}

export default AppRuntimeShell;
