import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../context/ConfigContext';
import { savePreferences, selectPreferences, selectPreferencesSaveRequest } from '../store/uiDataSlice';

export function resolveTheme(theme, prefersDark) {
  if (theme === 'system') {
    return prefersDark ? 'dark' : 'light';
  }
  return theme;
}

export function useTheme() {
  const { config, setTheme: setConfigTheme, getApiUrl } = useConfig();
  const dispatch = useDispatch();
  const preferences = useSelector(selectPreferences);
  const preferencesUrl = getApiUrl('preferences');
  const [currentTheme, setCurrentTheme] = useState(() =>
    resolveTheme(
      config.theme.default,
      typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches,
    ),
  );

  // useLayoutEffect (not useEffect): must set the DOM attribute that drives
  // CSS *before* the browser paints, not after - otherwise every mount
  // flashes the wrong theme for one frame while the effect queue drains.
  useLayoutEffect(() => {
    const resolved = resolveTheme(
      config.theme.default,
      window.matchMedia('(prefers-color-scheme: dark)').matches,
    );
    document.documentElement.dataset.theme = resolved;
    setCurrentTheme(resolved); // sync local state with config change

    if (config.theme.default === 'system') {
      const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
      const handleChange = () => {
        const next = resolveTheme('system', mediaQuery.matches);
        document.documentElement.dataset.theme = next;
        setCurrentTheme(next);
      };
      mediaQuery.addEventListener('change', handleChange);
      return () => mediaQuery.removeEventListener('change', handleChange);
    }

    return undefined;
  }, [config.theme.default]);

  // Live sync: whenever the backend-persisted profile's theme disagrees with
  // the local value (initial load, an agent edit to /config/preferences.json,
  // a reset, or a change from another device/tab), adopt it - UNLESS a local
  // change happened before the initial load resolved (see setTheme below),
  // in which case the user's fresher local choice wins and gets pushed to
  // the backend instead of getting silently overwritten by whatever was
  // already saved there. Safe from feedback loops otherwise: setTheme
  // updates config.theme.default synchronously before its own save
  // round-trips back through this same selector, so by the time that
  // snapshot arrives the values already match and this is a no-op.
  const pendingLocalChangeRef = useRef(false);
  const saveRequest = useSelector(selectPreferencesSaveRequest);
  useEffect(() => {
    if (!preferences.loaded) {
      return;
    }
    if (pendingLocalChangeRef.current) {
      pendingLocalChangeRef.current = false;
      if (preferences.theme !== config.theme.default) {
        dispatch(savePreferences({ preferencesUrl, ...saveRequest, theme: config.theme.default }));
      }
      return;
    }
    if (preferences.theme && preferences.theme !== config.theme.default) {
      setConfigTheme(preferences.theme);
    }
    // config.theme.default/saveRequest are read (for the "push a pending
    // local change" branch), never watched: this effect exists to react to
    // the BACKEND value changing, not the local one. Depending on
    // config.theme.default would re-run this effect the instant the user
    // toggles theme - while Redux's preferences.theme is still the pre-save
    // (stale) value - and "adopt" that stale value right back over the
    // fresh local change, flickering the theme back and forth until the
    // real snapshot arrives. saveRequest is memoized at the source
    // (selectPreferencesSaveRequest uses createSelector) so it wouldn't
    // cause that on its own, but it stays out of the deps anyway - this
    // effect has no reason to re-run for anything but a real backend change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preferences.loaded, preferences.theme, setConfigTheme, dispatch, preferencesUrl]);

  // Persist locally (instant, synchronous localStorage via ConfigContext) and
  // mirror to the backend profile so it's part of /config/preferences.json
  // and syncs cross-device. The save itself is skipped until the initial
  // fetch resolves, so an early toggle can't clobber fields (display_name,
  // nav/pin state) this client hasn't loaded from the backend yet - but it's
  // remembered (pendingLocalChangeRef) so the effect above pushes it once
  // loaded, instead of the load overwriting it.
  const setTheme = useCallback(
    (next) => {
      setConfigTheme(next);
      if (!preferences.loaded) {
        pendingLocalChangeRef.current = true;
        return;
      }
      dispatch(savePreferences({ preferencesUrl, ...saveRequest, theme: next }));
    },
    [setConfigTheme, dispatch, preferencesUrl, preferences.loaded, saveRequest],
  );

  const toggleTheme = useCallback(() => {
    const next = currentTheme === 'dark' ? 'light' : 'dark';
    setCurrentTheme(next); // immediate UI sync
    setTheme(next); // persist locally + backend
  }, [currentTheme, setTheme]);

  return { currentTheme, setTheme, toggleTheme };
}
