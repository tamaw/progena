import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

// Pure-logic unit tests run in the Node environment with a tiny localStorage
// shim; component tests select jsdom through their file-level environment.
export default defineConfig({
  root: fileURLToPath(new URL('../..', import.meta.url)),
  resolve: {
    alias: { '@frontend': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'node',
    include: ['tests/frontend/unit/**/*.test.{js,jsx}'],
    setupFiles: ['./tests/frontend/unit/setup.js'],
    clearMocks: true,
  },
});
