import { test, expect } from '@playwright/test';
import { observeRoutineEvents, removeRoutine, seedRoutine } from '../helpers/routineFixture.mjs';

// The routines list filters by each routine's latest run, not its catalog
// status. Each fixture ends in a different real run state; the filters are
// checked after a reload, so they come from the hydrated run history.
test.setTimeout(420_000);
const fixtures = [];
test.afterEach(() => { for (const graph of fixtures.splice(0)) removeRoutine(graph.id); });

async function openRoutine(page, graph) {
  await page.goto(`/routines/${graph.id}`);
  await expect(page.locator('.canvas-node[data-node-id="first"]')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Run routine', exact: true })).toBeEnabled();
}

async function lifecycle(page, routineId, status) {
  await expect.poll(() => page.evaluate(({ routineId, status }) =>
    window.__routineEvents.some((e) => e.type === `agent.routine.run.${status}` && e.payload.agent_id === routineId),
  { routineId, status }), { timeout: 180_000 }).toBe(true);
}

async function visibleFixtures(pane, filter) {
  await pane.getByRole('button', { name: filter, exact: true }).click();
  const shown = [];
  for (const graph of fixtures) {
    if (await pane.getByRole('button', { name: new RegExp(graph.name) }).count()) shown.push(graph.name);
  }
  return shown.sort();
}

test('routines list filters by the latest run status', async ({ page }) => {
  const seed = (options) => { const graph = seedRoutine(options); fixtures.push(graph); return graph; };
  const failed = seed({ failing: true });
  const paused = seed();
  const completed = seed();
  // No sleep node exists: a failing node retried after a fixed 60s backoff
  // holds the run in `started`, then fails it.
  const running = seed({ failing: true, retry: { max_retries: 1, initial_interval: 60, max_interval: 60, jitter: false, retry_on: 'all' } });
  await observeRoutineEvents(page);

  await openRoutine(page, failed);
  await page.getByRole('button', { name: 'Run routine', exact: true }).click();
  await lifecycle(page, failed.id, 'failed');

  await openRoutine(page, paused);
  await page.locator('.canvas-node[data-node-id="first"]').click();
  await page.getByRole('button', { name: 'Run to here', exact: true }).click();
  await lifecycle(page, paused.id, 'paused');

  await openRoutine(page, completed);
  await page.getByRole('button', { name: 'Run routine', exact: true }).click();
  await lifecycle(page, completed.id, 'completed');

  await openRoutine(page, running);
  await page.getByRole('button', { name: 'Run routine', exact: true }).click();
  await lifecycle(page, running.id, 'started');

  await page.goto('/routines');
  const pane = page.locator('.routines-pane');
  await expect(pane).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('.agents-pane')).toHaveCount(0);
  await expect(pane.getByRole('button', { name: new RegExp(completed.name) })).toBeVisible({ timeout: 30_000 });

  const all = [failed.name, paused.name, completed.name, running.name].sort();
  await expect.poll(() => visibleFixtures(pane, 'Running'), { timeout: 30_000 }).toEqual([running.name]);
  await expect.poll(() => visibleFixtures(pane, 'Failed'), { timeout: 30_000 }).toEqual([failed.name]);
  await expect.poll(() => visibleFixtures(pane, 'Paused'), { timeout: 30_000 }).toEqual([paused.name]);
  await expect.poll(() => visibleFixtures(pane, 'All'), { timeout: 30_000 }).toEqual(all);
  // When the backoff ends the run fails, and the list moves it live.
  await expect.poll(() => visibleFixtures(pane, 'Failed'), { timeout: 150_000 }).toEqual([failed.name, running.name].sort());
  await expect.poll(() => visibleFixtures(pane, 'Running'), { timeout: 10_000 }).toEqual([]);
});
