import { defineConfig } from 'vitest/config';

// Pure-logic unit tests run in the Node environment with a tiny localStorage
// shim (see vitest.setup.js) — no jsdom needed. Component/e2e layers can add
// their own environment later.
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'node',
    include: ['src/**/*.test.{js,jsx}'],
    setupFiles: ['./vitest.setup.js'],
    clearMocks: true,
  },
});
