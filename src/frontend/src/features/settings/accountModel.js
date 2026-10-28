

// The full IANA tz database, as the runtime actually knows it - matches what
// the backend's zoneinfo-based validation accepts, so nothing typed here can
// fail to save. Computed once at module load, not per-render.
export const TIMEZONE_OPTIONS = (() => {
  try {
    return Intl.supportedValuesOf('timeZone').sort();
  } catch {
    // Very old browsers only: Intl.supportedValuesOf isn't universally
    // available. A minimal fallback still lets the dropdown function.
    return ['UTC', 'America/New_York', 'America/Los_Angeles', 'Europe/Paris', 'Asia/Tokyo', 'Australia/Sydney'];
  }
})();

export const TIMEZONE_SELECT_OPTIONS = TIMEZONE_OPTIONS.map((tz) => ({ value: tz, label: tz }));
