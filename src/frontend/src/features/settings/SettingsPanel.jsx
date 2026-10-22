import { useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { savePreferences, selectPreferences, selectPreferencesSaveRequest } from './state/preferences';
import { useConfig } from '../../context/ConfigContext';
import { SettingsAboutIcon, SettingsAgentsIcon, SettingsAppearanceIcon, SettingsGeneralIcon, SettingsIntegrationsIcon, SettingsKeyboardIcon, SettingsMarketplacesIcon, SettingsTeamIcon } from '../../components/navIcons';
import PopupModal from '../../components/PopupModal';
import ProvidersSettings from './ProvidersSettings';
import { TopNavPane } from '../../components/panels/PanelFrame';
import { WorkspacesSettingsRow } from './WorkspacesSettingsRow';
import { MarketplacesSettingsRow } from '../plugins/MarketplacesSettingsRow';

const settingsSections = [
  { id: 'general', label: 'General', icon: <SettingsGeneralIcon /> },
  { id: 'appearance', label: 'Appearance', icon: <SettingsAppearanceIcon /> },
  { id: 'marketplaces', label: 'Marketplaces', icon: <SettingsMarketplacesIcon /> },
  { id: 'providers', label: 'Providers', icon: <SettingsIntegrationsIcon /> },
  { id: 'agents', label: 'Agents & Models', icon: <SettingsAgentsIcon /> },
  { id: 'keyboard', label: 'Keyboard', icon: <SettingsKeyboardIcon /> },
  { id: 'integrations', label: 'Integrations', icon: <SettingsIntegrationsIcon /> },
  { id: 'team', label: 'Team', icon: <SettingsTeamIcon /> },
  { id: 'about', label: 'About', icon: <SettingsAboutIcon /> },
];

export function SettingsPanel({ onClose, isPinned, onTogglePin, isOpen, onResetLiveUiState }) {
  const { config, getApiUrl, resetSettings, setIdleTimeoutMinutes, setDensity } = useConfig();
  const idleTimeoutMinutes = config.session.idleTimeoutMinutes;
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
                    <div className="set-row__sub">Pause live updates after {idleTimeoutMinutes} min without activity.</div>
                  </div>
                  <input
                    className="set-slider"
                    type="range"
                    min="5"
                    max="120"
                    step="5"
                    value={idleTimeoutMinutes}
                    aria-label="Session timeout in minutes"
                    onChange={(event) => setIdleTimeoutMinutes(Number(event.target.value))}
                  />
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
                    <div className="set-row__sub">Scale text size across the app.</div>
                  </div>
                  <div className="set-seg" role="group" aria-label="Interface density">
                    {config.density.options.map((option) => (
                      <button
                        key={option}
                        type="button"
                        className={`set-seg__btn${config.density.default === option ? ' is-active' : ''}`}
                        aria-pressed={config.density.default === option}
                        onClick={() => setDensity(option)}
                      >
                        {option.charAt(0).toUpperCase() + option.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>
              </section>
            ) : null}

            {section === 'marketplaces' ? (
              <MarketplacesSettingsRow />
            ) : null}
            {section === 'providers' ? (
              <ProvidersSettings />
            ) : null}

            {!['general', 'appearance', 'marketplaces', 'providers'].includes(section) ? (
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
