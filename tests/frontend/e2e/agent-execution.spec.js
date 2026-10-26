import { test, expect } from '@playwright/test';
import { attachJsonEvidence, observeRoutineEvents } from '../helpers/routineFixture.mjs';
import { delegatedCheckpoint } from '../helpers/delegatedRoutineFixture.mjs';
import { readAgentRun, seedAgentExecution } from '../helpers/agentExecutionFixture.mjs';

test.setTimeout(420_000);
let fixture;
test.afterEach(() => { fixture?.cleanup(); fixture = null; });
async function lifecycle(page, status, requestId) {
  await expect.poll(() => page.evaluate(({ status, requestId }) => window.__routineEvents.find((event) =>
    (event.type === `agent.routine.run.${status}` || event.type === 'agent.routine.run.failed') && event.payload.request_id === requestId)?.payload,
  { status, requestId }), { timeout: 180_000 }).toBeTruthy();
  const failed = await page.evaluate((requestId) => window.__routineEvents.find((event) =>
    event.type === 'agent.routine.run.failed' && event.payload.request_id === requestId)?.payload, requestId);
  expect(failed, JSON.stringify(failed)).toBeUndefined();
  return page.evaluate(({ status, requestId }) => window.__routineEvents.find((event) =>
    event.type === `agent.routine.run.${status}` && event.payload.request_id === requestId).payload,
  { status, requestId });
}
async function run(page) {
  const posted = page.waitForRequest((request) => request.method() === 'POST' && request.url().endsWith(`/${fixture.graph.id}/run`));
  await page.getByRole('button', { name: 'Run routine', exact: true }).click();
  return (await posted).headers()['x-operation-id'];
}
async function selectNode(page, id) {
  const close = page.getByRole('button', { name: 'Close node details', exact: true });
  if (await close.isVisible()) await close.click();
  await page.locator(`.canvas-node[data-node-id="${id}"]`).click();
}
async function output(page, expected = 'E2E_AGENT_DONE') {
  await selectNode(page, 'after');
  const inspector = page.locator('.routine-node-inspector__run-output pre');
  await expect(inspector).toContainText(expected, { timeout: 30_000 });
  return inspector.textContent();
}

test('same object agent waits by default, or continues with a persisted background task', async ({ page }, testInfo) => {
  page.setDefaultTimeout(30_000);
  fixture = seedAgentExecution();
  await observeRoutineEvents(page);
  await page.goto(`/routines/${fixture.graph.id}`);
  await expect(page.locator('.canvas-node[data-node-id="delegate"]')).toBeVisible({ timeout: 30_000 });
  await selectNode(page, 'delegate');
  await expect(page.getByLabel('Execution', { exact: true })).toHaveValue('wait');
  const waitId = await run(page);
  const waiting = await lifecycle(page, 'started', waitId);
  await expect.poll(fixture.entered, { timeout: 90_000 }).toBe(true);
  const blocked = delegatedCheckpoint(waiting.thread_id);
  expect(blocked.run_meta.after).toBeUndefined();
  expect(await page.evaluate((id) => window.__routineEvents.some((event) =>
    event.type === 'agent.routine.run.completed' && event.payload.request_id === id), waitId)).toBe(false);
  fixture.release();
  await lifecycle(page, 'completed', waitId);
  const waited = await output(page);
  expect(waited).toContain('E2E_AGENT_DONE');
  fixture.reset();
  await selectNode(page, 'delegate');
  await page.getByLabel('Execution', { exact: true }).selectOption('background');
  await expect(page.getByRole('status').filter({ hasText: /^Saved$/ })).toBeVisible({ timeout: 30_000 });
  await page.reload();
  await selectNode(page, 'delegate');
  await expect(page.getByLabel('Execution', { exact: true })).toHaveValue('background');
  const backgroundId = await run(page);
  const completed = await lifecycle(page, 'completed', backgroundId);
  const background = JSON.parse(await output(page, 'task_id'));
  expect(background).toMatchObject({ agent_name: fixture.name, status: 'running' });
  const task = background;
  const runningTask = readAgentRun(task);
  expect(runningTask.status).toBe('running');
  expect(delegatedCheckpoint(completed.thread_id).run_meta.after).toHaveLength(1);
  await expect.poll(fixture.entered, { timeout: 90_000 }).toBe(true);
  expect(readAgentRun(task).status).toBe('running');
  fixture.release();
  await expect.poll(() => readAgentRun(task)?.status, { timeout: 90_000 }).toBe('success');
  const finishedTask = readAgentRun(task);
  const content = finishedTask.values.messages.at(-1).content;
  const text = Array.isArray(content) ? content.filter((block) => block.type === 'text').map((block) => block.text).join('') : content;
  expect(text).toContain('E2E_AGENT_DONE');
  await attachJsonEvidence(testInfo, 'agent-execution', { blocked, waited, background, runningTask, finishedTask });
});
