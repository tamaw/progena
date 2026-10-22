import { afterEach, describe, expect, it, vi } from 'vitest';

async function catalogueEnabled(hostname, development = true) {
  vi.resetModules();
  vi.stubEnv('DEV', development);
  vi.stubGlobal('window', { location: { hostname } });
  return (await import('@frontend/lib/localDesignLibrary')).localDesignLibraryEnabled;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe('local design library access', () => {
  it.each(['localhost', '127.0.0.1', '[::1]'])('allows loopback development at %s', async (hostname) => {
    expect(await catalogueEnabled(hostname)).toBe(true);
  });

  it.each(['192.168.1.10', 'app.example.com', 'localhost.example.com', '127.0.0.1.example.com'])('rejects non-loopback hosts including lookalikes: %s', async (hostname) => {
    expect(await catalogueEnabled(hostname)).toBe(false);
  });

  it('rejects production even when opened on localhost', async () => {
    expect(await catalogueEnabled('localhost', false)).toBe(false);
  });
});
