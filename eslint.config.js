import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['src/frontend/dist', 'src/frontend/src/features/modules/vendor', 'tests/frontend/test-results', 'tests/frontend/playwright-report', 'tests/frontend/blob-report']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]' }],
    },
  },
  {
    // Application assembly supplies chrome dependencies to feature panels.
    files: ['src/frontend/src/features/**/*.{js,jsx}'],
    ignores: ['**/*.test.*'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [{
          group: ['**/shell/**', '**/store/uiDataSlice', '**/store/uiDataSlice.*', '**/store/store', '**/store/store.*'],
          message: 'Features import their owning state and shared infrastructure directly; application assembly owns chrome and store wiring.',
        }],
      }],
    },
  },
  {
    files: ['src/frontend/src/components/**/*.{js,jsx}', 'src/frontend/src/hooks/**/*.js', 'src/frontend/src/lib/**/*.js', 'src/frontend/src/realtime/**/*.js'],
    ignores: ['**/*.test.*'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [{
          group: ['**/features/**', '**/shell/**', '**/store/uiDataSlice', '**/store/uiDataSlice.*', '**/store/store', '**/store/store.*'],
          message: 'Shared code must remain independent of features and application assembly.',
        }],
      }],
    },
  },
  {
    files: [
      'src/frontend/src/features/**/models/**/*.js', 'src/frontend/src/features/**/*Model.js',
      'src/frontend/src/features/**/state.js', 'src/frontend/src/features/**/state/**/*.js', 'src/frontend/src/features/**/*State.js',
    ],
    ignores: ['**/*.test.*'],
    rules: {
      'no-restricted-imports': ['error', {
        paths: ['react', 'react-redux', 'react-router-dom', '@xyflow/react'],
        patterns: [{
          group: ['**/shell/**', '**/components/**', '**/*Page', '**/*Page.jsx', '**/store/uiDataSlice', '**/store/uiDataSlice.*', '**/store/store', '**/store/store.*'],
          message: 'Feature state and pure models must stay independent of UI and application assembly.',
        }],
      }],
    },
  },
  {
    // Static navigation configuration contains icon elements, not components.
    files: ['src/frontend/src/shell/navigation.jsx'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
  {
    // Node-context tooling: Playwright config/specs run under Node, not the browser.
    files: ['src/frontend/playwright.config.js', 'tests/frontend/**/*.{js,jsx}'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
])
