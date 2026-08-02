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

  test('Agents list loads real agents and selecting one renders its detail body (AgentDetailBodies)', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/agents');
    await expect(page.locator('.agents-pane')).toBeVisible();
    await expect(page.getByPlaceholder('Search agents...')).toBeVisible();

    // Real agent rows arrive via the realtime snapshot.
    const rows = page.locator('.agents-agent-row');
    await expect(rows.first()).toBeVisible({ timeout: 20_000 });
    await rows.first().click();
    // The extracted A2A/ACP/Subagent detail body renders into the detail pane.
    await expect(page.locator('.detail-pane__body').first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('Routines renders the React Flow graph canvas for the selected agent (AgentGraph extraction)', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/routines');
    // The extracted graph module mounts React Flow for the auto-selected agent.
    await expect(page.locator('.react-flow').first()).toBeVisible({ timeout: 20_000 });
    expect(errors).toEqual([]);
  });

  test('Agent search filters the list (uiDataSlice agents domain still wired)', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/agents');
    const rows = page.locator('.agents-agent-row');
    await expect(rows.first()).toBeVisible({ timeout: 20_000 });
    expect(await rows.count()).toBeGreaterThan(0);

    // A query that should match nothing collapses the list to its empty state.
    await page.getByPlaceholder('Search agents...').fill('zzz-no-such-agent-zzz');
    await expect(rows).toHaveCount(0);
    await expect(page.getByText('No agents match this view.')).toBeVisible();
    expect(errors).toEqual([]);
  });
});
