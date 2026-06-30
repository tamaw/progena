/* eslint-disable react-refresh/only-export-components */
import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { useConfig } from './ConfigContext';

/**
 * Health check context that provides a single, cached health status
 * shared across all components. Polls every 5 seconds when unhealthy,
 * every 30 seconds when healthy.
 */

const HealthContext = createContext(null);

/**
 * Build full URL for an API path.
 */
function buildUrl(baseUrl, path) {
  const base = baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl;
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${base}${p}`;
}

export function HealthProvider({ children }) {
  const { config } = useConfig();
  const [isHealthy, setIsHealthy] = useState(false);
  const [lastCheck, setLastCheck] = useState(null);

  const baseUrl = config.api.baseUrl;
  const intervalIdRef = useRef(null);
  const isHealthyRef = useRef(false);

  useEffect(() => {
    let mounted = true;
    let currentInterval = 5000; // Start with 5s

    const scheduleNext = (interval) => {
      if (intervalIdRef.current) {
        clearTimeout(intervalIdRef.current);
      }
      if (mounted) {
        intervalIdRef.current = setTimeout(runCheck, interval);
      }
    };

    const runCheck = async () => {
      if (!mounted) return;

      try {
        const response = await fetch(buildUrl(baseUrl, '/health'), {
          method: 'GET',
          headers: { 'Accept': 'application/json' },
        });

        if (!mounted) return;

        const data = await response.json().catch(() => null);
        const status = data?.status;
        const healthy = status === 'ok' || status === 'healthy';

        isHealthyRef.current = healthy;
        setIsHealthy(healthy);
        setLastCheck(Date.now());
        currentInterval = healthy ? 30000 : 5000;
      } catch (e) {
        console.error('[HealthContext] Error:', e.message);
        if (mounted) {
          isHealthyRef.current = false;
          setIsHealthy(false);
          setLastCheck(Date.now());
          currentInterval = 5000;
        }
      }

      // Schedule next check
      if (mounted) {
        scheduleNext(currentInterval);
      }
    };

    runCheck(); // Initial check

    return () => {
      mounted = false;
      if (intervalIdRef.current) {
        clearTimeout(intervalIdRef.current);
        intervalIdRef.current = null;
      }
    };
  }, [baseUrl]);

  const value = { isHealthy, lastCheck };

  return (
    <HealthContext.Provider value={value}>
      {children}
    </HealthContext.Provider>
  );
}

/**
 * Hook to access the shared health status.
 * Returns { isHealthy, lastCheck }
 */
export function useHealth() {
  const context = useContext(HealthContext);
  if (!context) {
    throw new Error('useHealth must be used within a HealthProvider');
  }
  return context;
}
