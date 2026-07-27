import { useCallback, useEffect, useState } from 'react';
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

  useEffect(() => {
    const resolved = resolveTheme(
      config.theme.default,
      window.matchMedia('(prefers-color-scheme: dark)').matches,
    );
    document.documentElement.dataset.theme = resolved;
    // eslint-disable-next-line react-hooks/set-state-in-effect
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
  // a reset, or a change from another device/tab), adopt it. Safe from
  // feedback loops: setTheme below updates config.theme.default synchronously
  // before its own save round-trips back through this same selector, so by
  // the time the snapshot arrives the values already match and this is a
  // no-op.
  useEffect(() => {
    if (!preferences.loaded) {
      return;
    }
    if (preferences.theme && preferences.theme !== config.theme.default) {
      setConfigTheme(preferences.theme);
    }
  }, [preferences.loaded, preferences.theme, config.theme.default, setConfigTheme]);

  // Persist locally (instant, synchronous localStorage via ConfigContext) and
  // mirror to the backend profile so it's part of /config/preferences.json
  // and syncs cross-device. Skipped until the initial fetch resolves, so an
  // early toggle can't clobber fields (display_name, nav/pin state) this
  // client hasn't loaded from the backend yet.
  const saveRequest = useSelector(selectPreferencesSaveRequest);
  const setTheme = useCallback(
    (next) => {
      setConfigTheme(next);
      if (!preferences.loaded) {
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
