import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright discovery / smoke suite for the Progenitor frontend.
 *
 * It runs against an ALREADY-RUNNING stack (postgres + progenitor + backend +
 * frontend). Start them per docs/LOCAL_STARTUP.md, then:
 *
 *   npm run test:e2e
 *
 * Override the target with FRONTEND_BASE_URL if the dev server is elsewhere.
 */
export default defineConfig({
  testDir: '../../tests/frontend/e2e',
  outputDir: '../../tests/frontend/test-results',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: [['list']],
  use: {
    baseURL: process.env.FRONTEND_BASE_URL || 'http://127.0.0.1:5173',
    headless: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
