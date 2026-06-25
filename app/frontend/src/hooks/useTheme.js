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
  const [currentTheme, setCurrentTheme] = useState(() => resolveTheme(config.theme.default));

  useEffect(() => {
    const resolved = resolveTheme(
      config.theme.default,
      window.matchMedia('(prefers-color-scheme: dark)').matches,
    );
    document.documentElement.dataset.theme = resolved;
    setCurrentTheme(resolved);

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
    setTheme(next);
  };

  return { currentTheme, setTheme, toggleTheme };
}
