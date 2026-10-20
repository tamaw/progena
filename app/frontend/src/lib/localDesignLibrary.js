// A development server exposed to a LAN or public hostname must not expose the catalogue.
export const localDesignLibraryEnabled = import.meta.env.DEV
  && ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);
