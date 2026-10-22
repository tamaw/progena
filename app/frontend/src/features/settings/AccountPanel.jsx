import { useCallback, useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { savePreferences, selectPreferences, selectPreferencesSaveRequest } from './state/preferences';
import { useConfig } from '../../context/ConfigContext';
import { useTheme } from './useTheme';
import Select from '../../components/Select';
import { UserAvatarIcon } from '../../components/navIcons';
import { TIMEZONE_OPTIONS, TIMEZONE_SELECT_OPTIONS } from './accountModel';
import { TopNavPane } from '../../components/panels/PanelFrame';

export function AccountPanel({ onClose, isPinned, onTogglePin, isOpen }) {
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
