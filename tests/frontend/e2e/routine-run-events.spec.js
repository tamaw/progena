import { test, expect } from '@playwright/test';
import { observeRoutineEvents, removeRoutine, seedRoutine } from '../helpers/routineFixture.mjs';
import { openApiClient } from '../helpers/apiClient.mjs';

// Resume and repair scenarios enqueue two executions. Each can wait behind
// other live journeys before its millisecond-scale graph execution starts.
test.setTimeout(420_000);
let fixture;
test.afterEach(() => { if (fixture) removeRoutine(fixture.id); fixture = null; });

async function openRoutine(page, graph) {
  await observeRoutineEvents(page);
  await page.goto(`/routines/${graph.id}`);
  await expect(page.locator('.canvas-node[data-node-id="first"]')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Run routine', exact: true })).toBeEnabled();
}

async function lifecycle(page, routineId, status, requestId) {
  await expect.poll(() => page.evaluate(({ routineId, status, requestId }) =>
    window.__routineEvents.find((e) => e.type === `agent.routine.run.${status}`
      && e.payload.agent_id === routineId && (!requestId || e.payload.request_id === requestId)),
  { routineId, status, requestId }), { timeout: 180_000 }).toBeTruthy();
  return page.evaluate(({ routineId, status, requestId }) =>
    window.__routineEvents.find((e) => e.type === `agent.routine.run.${status}`
      && e.payload.agent_id === routineId && (!requestId || e.payload.request_id === requestId)).payload,
  { routineId, status, requestId });
}

test('full run pushes output and completion to other tabs on Routines without discovery/state polling', async ({ page, context }) => {
  fixture = seedRoutine();
  const background = await context.newPage();
  await observeRoutineEvents(background);
  await background.goto('/routines');
  await openRoutine(page, fixture);
  const requests = [];
  page.on('request', (request) => { if (/\/(runs|run-state)(\?|$)/.test(request.url())) requests.push(request.url()); });
  const posted = page.waitForRequest((request) => request.method() === 'POST' && new URL(request.url()).pathname.endsWith(`/${fixture.id}/run`));
  await page.getByRole('button', { name: 'Run routine', exact: true }).click();
  const requestId = (await posted).headers()['x-operation-id'];
  await lifecycle(page, fixture.id, 'started', requestId);
  const completed = await lifecycle(page, fixture.id, 'completed', requestId);
  await lifecycle(background, fixture.id, 'completed', requestId);
  await page.locator('.canvas-node[data-node-id="second"]').click();
  await expect(page.locator('.routine-node-inspector__run-output pre')).toHaveText('10', { timeout: 20_000 });
  await expect(page.getByRole('button', { name: 'Run routine', exact: true })).toBeEnabled();
  expect(requests).toEqual([]);
  const leaked = await background.evaluate((thread) => window.__routineEvents.filter((e) =>
    e.type === 'agent.routine.run_state.loaded' && e.payload.thread_id === thread), completed.thread_id);
  expect(leaked).toEqual([]);
  await background.close();
});

test('Run to here pauses, leaving unsubscribes, resume completes on the Routines topic, reopening/reconnecting hydrates output', async ({ page, browser, baseURL }) => {
  await page.clock.install();
  fixture = seedRoutine();
  await openRoutine(page, fixture);
  await page.locator('.canvas-node[data-node-id="first"]').click();
  await page.getByRole('button', { name: 'Run to here', exact: true }).click();
  const paused = await lifecycle(page, fixture.id, 'paused');
  await expect(page.locator('.routine-node-inspector__run-output pre')).toHaveText('9', { timeout: 20_000 });
  await expect(page.getByRole('button', { name: 'Run to here', exact: true })).toBeEnabled();
  // Another tab on Routines watches the `routines` topic, which the Dashboard does not.
  const watcherContext = await browser.newContext({ baseURL });
  const watcher = await watcherContext.newPage();
  await observeRoutineEvents(watcher);
  await watcher.goto('/routines');
  const unsubscribe = page.waitForRequest((request) => new URL(request.url()).pathname.endsWith('/run-subscription') && request.postDataJSON()?.subscribed === false);
  await page.getByRole('link', { name: 'Dashboard', exact: true }).click();
  await unsubscribe;
  await page.evaluate(() => { window.__routineEvents = []; });
  const requestId = crypto.randomUUID();
  const api = await openApiClient();
  const response = await page.request.post(api.url(`/api/ui/agent-profiles/${fixture.id}/run`), {
    headers: { 'X-Operation-Id': requestId }, data: { session_id: paused.session_id, parameters: {} },
  });
  api.close();
  expect(response.status()).toBe(202);
  await lifecycle(watcher, fixture.id, 'completed', requestId);
  await watcherContext.close();
  expect(await page.evaluate((id) => window.__routineEvents.filter((e) =>
    e.type === 'agent.routine.run_state.loaded' && e.payload.agent_id === id), fixture.id)).toEqual([]);
  await page.goto(`/routines/${fixture.id}`);
  await page.locator('.canvas-node[data-node-id="second"]').click();
  await expect(page.locator('.routine-node-inspector__run-output pre')).toHaveText('10', { timeout: 30_000 });
  await page.evaluate(() => { window.__routineEvents = []; window.__routineDisconnects = 0; });
  // Chromium's offline toggle can leave an established SSE socket alive.
  // Close it explicitly and let the real heartbeat watchdog reopen it;
  // browser time advances, but the replacement SSE and its data are live.
  await page.evaluate(() => {
    const source = window.__routineSources.findLast((s) => s.url.includes('/api/ui/stream'));
    source.close();
    source.dispatchEvent(new Event('error'));
  });
  await page.clock.fastForward(60_000);
  await expect.poll(() => page.evaluate((id) => window.__routineEvents.some((e) =>
    e.type === 'agent.routine.subscription.updated' && e.payload.agent_id === id && e.payload.subscribed), fixture.id),
  { timeout: 30_000 }).toBe(true);
  await expect.poll(() => page.evaluate((id) => window.__routineEvents.some((e) =>
    e.type === 'agent.routine.run_state.loaded' && e.payload.agent_id === id && e.payload.found), fixture.id),
  { timeout: 30_000 }).toBe(true);
  await expect(page.locator('.routine-node-inspector__run-output pre')).toHaveText('10', { timeout: 30_000 });
});

test('execution error reaches other tabs on Routines, clears the indicator, and a repaired retry succeeds', async ({ page, context }) => {
  fixture = seedRoutine({ failing: true });
  const background = await context.newPage();
  await observeRoutineEvents(background);
  await background.goto('/routines');
  await openRoutine(page, fixture);
  await page.getByRole('button', { name: 'Run routine', exact: true }).click();
  const failed = await lifecycle(page, fixture.id, 'failed');
  expect(failed.error).toBeTruthy();
  await lifecycle(background, fixture.id, 'failed', failed.request_id);
  await expect(page.getByRole('button', { name: 'Run routine', exact: true })).toBeEnabled();
  expect(await page.evaluate((id) => window.__routineEvents.some((e) =>
    e.type === 'agent.routine.run.completed' && e.payload.request_id === id), failed.request_id)).toBe(false);
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  const json = page.locator('.agents-graph-editor__textarea');
  const graph = JSON.parse(await json.inputValue());
  graph.nodes.find((node) => node.id === 'first').config.expression = '9';
  await json.fill(JSON.stringify(graph, null, 2));
  await page.getByRole('button', { name: 'Apply changes' }).click();
  await expect(page.locator('.agents-graph-editor__saved')).toHaveText('Graph saved.', { timeout: 30_000 });
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
  const retryPosted = page.waitForRequest((request) => request.method() === 'POST' && new URL(request.url()).pathname.endsWith(`/${fixture.id}/run`));
  await page.getByRole('button', { name: 'Run routine', exact: true }).click();
  const retryId = (await retryPosted).headers()['x-operation-id'];
  expect(retryId).not.toBe(failed.request_id);
  await lifecycle(page, fixture.id, 'completed', retryId);
  await lifecycle(background, fixture.id, 'completed', retryId);
  await page.locator('.canvas-node[data-node-id="second"]').click();
  await expect(page.locator('.routine-node-inspector__run-output pre')).toHaveText('10', { timeout: 20_000 });
  await background.close();
});
