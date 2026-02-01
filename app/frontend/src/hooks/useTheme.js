import { useEffect } from 'react';
import { useConfig } from '../context/ConfigContext';

export function useTheme() {
  const { config, setTheme } = useConfig();

  useEffect(() => {
    const applyTheme = (theme) => {
      const root = document.documentElement;
      if (theme === 'system') {
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        root.dataset.theme = prefersDark ? 'dark' : 'light';
      } else {
        root.dataset.theme = theme;
      }
    };

    applyTheme(config.theme.default);

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleChange = () => {
      if (config.theme.default === 'system') {
        applyTheme('system');
      }
    };
    
    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, [config.theme.default]);

  const toggleTheme = () => {
    const current = document.documentElement.dataset.theme;
    const newTheme = current === 'dark' ? 'light' : 'dark';
    setTheme(newTheme);
  };

  const currentTheme = document.documentElement.dataset.theme || 'dark';

  return { currentTheme, setTheme, toggleTheme };
}
