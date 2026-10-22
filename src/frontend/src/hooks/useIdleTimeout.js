import { useCallback, useEffect, useRef, useState } from 'react';

const ACTIVITY_EVENTS = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'];
const CHECK_INTERVAL_MS = 1000;

// Reports `idle` once no user input has arrived for `timeoutMs`. Input seen
// while idle is ignored: only `resume` ends the idle state, so the pause
// dialog cannot be dismissed by the pointer moving towards it. Elapsed time is
// compared against the wall clock, so a throttled background tab still trips.
export default function useIdleTimeout(timeoutMs) {
  const lastActivityRef = useRef(null);
  const [idle, setIdle] = useState(false);

  useEffect(() => {
    if (idle) return undefined;
    lastActivityRef.current ??= Date.now();
    const markActive = () => {
      lastActivityRef.current = Date.now();
    };
    ACTIVITY_EVENTS.forEach((name) => window.addEventListener(name, markActive, { passive: true }));
    const timer = setInterval(() => {
      if (Date.now() - lastActivityRef.current >= timeoutMs) setIdle(true);
    }, CHECK_INTERVAL_MS);
    return () => {
      ACTIVITY_EVENTS.forEach((name) => window.removeEventListener(name, markActive));
      clearInterval(timer);
    };
  }, [idle, timeoutMs]);

  const resume = useCallback(() => {
    lastActivityRef.current = null;
    setIdle(false);
  }, []);
  return [idle, resume];
}
