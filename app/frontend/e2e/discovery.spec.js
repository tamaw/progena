import { test, expect } from '@playwright/test';

/**
 * Discovery suite: drives the live SPA to prove the refactor (useApi split,
 * uiDataSlice split, AgentsPage breakup, notification CSS module) did not break
 * page loading, data wiring, or interaction.
 *
 * The strongest signal is "no uncaught JS on mount": a broken import or render
 * from the extraction work would surface here as a `pageerror`.
 */

// Every top-level route declared in src/App.jsx.
const ROUTES = [
  ['Conversation', '/'],
  ['Dashboard', '/dashboard'],
  ['Models', '/models'],
  ['Channels', '/channels'],
  ['Agents', '/agents'],
  ['Routines', '/routines'],
  ['Tools', '/tools'],
  ['Tasks', '/tasks'],
  ['Kanban', '/kanban'],
  ['Logs', '/logs'],
  ['Plugins', '/plugins'],
  ['Prompts', '/prompts'],
  ['Sessions', '/sessions'],
  ['Schedules', '/schedules'],
  ['Repositories', '/repositories'],
  ['Knowledge', '/knowledge'],
  ['Workflows', '/workflows'],
  ['Project', '/solutions/main/projects/backend-runtime'],
];

/** Capture uncaught exceptions for the lifetime of the page. */
function trackPageErrors(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  return errors;
}

test.describe('route discovery — every page mounts without uncaught JS', () => {
  for (const [name, path] of ROUTES) {
    test(`${name} (${path})`, async ({ page }) => {
      const errors = trackPageErrors(page);
      await page.goto(path, { waitUntil: 'load' });
      // The SPA mounted real content into #root (more than an empty shell).
      await expect(page.locator('#root')).not.toBeEmpty({ timeout: 10_000 });
      // Let initial effects / lazy chunks / first realtime frame settle.
      await page.waitForTimeout(1200);
      expect(errors, `Uncaught JS on ${path}:\n${errors.join('\n')}`).toEqual([]);
    });
  }
});

test.describe('refactored surfaces — prove interactive behaviour', () => {
  test('Conversation renders the chat input (useChatApi split)', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/');
    await expect(page.locator('.chat-input-container')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('Channels renders its list pane (useListSelection + DetailCards)', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/channels');
    await expect(page.getByPlaceholder('Search channels...')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('Agents opens an object agent draft without relying on live registry contents', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/agents');
    await expect(page.locator('.agents-pane')).toBeVisible();
    await page.getByRole('button', { name: '+ New' }).click();
    await expect(page.getByRole('dialog', { name: 'New agent' })).toBeVisible();
    await page.getByRole('button', { name: /^Object agent/ }).click();

    await expect(page.getByRole('textbox', { name: 'Agent name' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'System prompt' })).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('Routines renders the graph canvas for the selected agent (RoutineGraphEditor)', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/routines');
    // The graph is a CSS step chain, not React Flow -- `@xyflow/react` is still
    // in package.json but no longer imported anywhere under src/.
    await expect(page.locator('.routine-chain').first()).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('.canvas-node').first()).toBeVisible({ timeout: 20_000 });
    expect(errors).toEqual([]);
  });

  test('Agent search remains responsive while the registry is unavailable', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/agents');
    const search = page.getByPlaceholder('Search agents...');
    await search.fill('zzz-no-such-agent-zzz');
    await expect(search).toHaveValue('zzz-no-such-agent-zzz');
    expect(errors).toEqual([]);
  });
  test('Workflows search shows its no-match state regardless of stored workflows', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/workflows');

    await page.getByPlaceholder('Search workflows').fill('zzz-no-such-workflow-zzz');
    await expect(page.getByText('No workflows match the current filters.')).toBeVisible();
    expect(errors).toEqual([]);
  });

});
