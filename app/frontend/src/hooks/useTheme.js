import { useEffect, useState } from 'react';
import { useConfig } from '../context/ConfigContext';

export function resolveTheme(theme, prefersDark) {
  if (theme === 'system') {
    return prefersDark ? 'dark' : 'light';
  }
  return theme;
}

export function useTheme() {
  const { config, setTheme } = useConfig();
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

  const toggleTheme = () => {
    const next = currentTheme === 'dark' ? 'light' : 'dark';
    setCurrentTheme(next); // immediate UI sync
    setTheme(next);        // persist to config
  };

  return { currentTheme, setTheme, toggleTheme };
}
