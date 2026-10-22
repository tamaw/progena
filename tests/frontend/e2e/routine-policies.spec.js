import { test, expect } from '@playwright/test';
import { seedPolicyRoutine } from '../helpers/policyFixture.mjs';
import { observeRoutineEvents, attachJsonEvidence } from '../helpers/routineFixture.mjs';
// Runs go through the runtime's background queue, which a full parallel suite
// can back up by minutes (one task per heartbeat cycle), so waits are generous.
test.setTimeout(900_000);
let fixture;
test.afterEach(() => { fixture?.cleanup(); fixture = null; });
async function lifecycle(page, requestId, status) {
  const read = () => page.evaluate((id) => window.__routineEvents.find((event) =>
    ['agent.routine.run.completed', 'agent.routine.run.failed', 'agent.routine.run.paused'].includes(event.type) && event.payload.request_id === id), requestId);
  await expect.poll(read, { timeout: 300_000 }).toBeTruthy();
  const event = await read();
  expect(event.type, JSON.stringify(event.payload)).toBe(`agent.routine.run.${status}`);
  return event.payload;
}
async function run(page, status) {
  const posted = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith(`/${fixture.id}/run`));
  await page.getByRole('button', { name: 'Run routine', exact: true }).click();
  return lifecycle(page, (await posted).headers()['x-request-id'], status);
}
async function openPolicy(page) {
  await page.getByRole('button', { name: 'Routine execution policy', exact: true }).click();
  const panel = page.getByRole('complementary', { name: 'Routine policy' });
  await expect(panel.getByLabel('Graph step limit')).toBeVisible();
  return panel;
}
async function save(panel) {
  await panel.getByRole('button', { name: 'Save policy', exact: true }).click();
  await expect(panel.getByRole('status')).toHaveText('Saved');
}
test('Policy edits persist; new runs record new revisions and enforce the graph step limit', async ({ page }, testInfo) => {
  fixture = seedPolicyRoutine();
  await observeRoutineEvents(page); await page.goto(`/routines/${fixture.id}`);
  let panel = await openPolicy(page);
  await panel.getByLabel('Graph step limit').fill('2');
  await panel.getByLabel('Cache enabled').check(); await panel.getByLabel('Cache TTL (seconds)').fill('60');
  await save(panel); await panel.getByRole('button', { name: 'Close policy' }).click(); await page.reload();
  panel = await openPolicy(page);
  await expect(panel.getByLabel('Graph step limit')).toHaveValue('2');
  await expect(panel.getByLabel('Cache enabled')).toBeChecked(); await expect(panel.getByLabel('Cache TTL (seconds)')).toHaveValue('60');
  await panel.getByRole('button', { name: 'Close policy' }).click();
  const failed = await run(page, 'failed');
  expect(failed.error).toContain('Recursion limit'); expect(failed.policy.values.graph_step_limit).toBe(2);
  panel = await openPolicy(page); await panel.getByLabel('Graph step limit').fill('20'); await save(panel);
  await panel.getByRole('button', { name: 'Close policy' }).click();
  const completed = await run(page, 'completed');
  expect(completed.policy.values.graph_step_limit).toBe(20);
  expect(completed.policy.revision_id).not.toBe(failed.policy.revision_id);
  expect(fixture.revisions()).toEqual(expect.arrayContaining([failed.policy.revision_id, completed.policy.revision_id]));
  await page.getByRole('button', { name: 'View the previous run', exact: true }).click();
  panel = await openPolicy(page);
  await expect(panel.getByLabel('Graph step limit')).toHaveValue('2');
  await expect(panel.getByLabel('Graph step limit')).toBeDisabled();
  await expect(panel).toContainText(failed.policy.revision_id);
  await attachJsonEvidence(testInfo, 'policy-new-runs', { failed, completed });
});
test('Approval resume retains the recorded policy after the shared policy changes', async ({ page }, testInfo) => {
  fixture = seedPolicyRoutine(true);
  await observeRoutineEvents(page); await page.goto(`/routines/${fixture.id}`);
  const paused = await run(page, 'paused');
  const panel = await openPolicy(page); await expect(panel.getByLabel('Graph step limit')).toHaveValue('20');
  await panel.getByLabel('Graph step limit').fill('1'); await save(panel);
  await panel.getByRole('button', { name: 'Close policy' }).click();
  expect(fixture.readPolicy().values.graph_step_limit).toBe(1); await page.reload();
  await page.locator('.canvas-node[data-node-id="decision"]').click();
  const approval = page.getByRole('region', { name: 'Pending approval' });
  await expect(approval.getByRole('button', { name: 'Approve', exact: true })).toBeEnabled();
  const posted = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith(`/${fixture.id}/approval`));
  await approval.getByRole('button', { name: 'Approve', exact: true }).click();
  const completed = await lifecycle(page, (await posted).headers()['x-request-id'], 'completed');
  expect(completed.policy.revision_id).toBe(paused.policy.revision_id); expect(completed.policy.values.graph_step_limit).toBe(20);
  await page.getByRole('button', { name: 'Close node details', exact: true }).click();
  await page.locator('.canvas-node[data-node-id="after"]').click();
  await expect(page.locator('.routine-node-inspector__run-output pre')).toContainText('42');
  await attachJsonEvidence(testInfo, 'policy-resume', { paused, completed });
});

test('Create a policy copy and persist the routine selection without editing the original policy', async ({ page }, testInfo) => {
  fixture = seedPolicyRoutine();
  await observeRoutineEvents(page); await page.goto(`/routines/${fixture.id}`);
  const panel = await openPolicy(page);
  await panel.getByRole('button', { name: 'Create a copy', exact: true }).click();
  await expect.poll(() => panel.getByLabel('Selected policy').inputValue()).not.toBe(fixture.policyId);
  const copyId = await panel.getByLabel('Selected policy').inputValue();
  fixture.registerCopy(copyId);
  await expect.poll(() => fixture.readGraph().policy_id).toBe(copyId);
  await panel.getByLabel('Default model profile', { exact: true }).fill('general');
  await panel.getByLabel('Maximum retries', { exact: true }).fill('3');
  await panel.getByLabel('Cache enabled').check();
  await save(panel);
  expect(fixture.readPolicy().values.retry.max_retries).toBe(0);
  await panel.getByRole('button', { name: 'Close policy' }).click(); await page.reload();
  const reopened = await openPolicy(page);
  await expect(reopened.getByLabel('Selected policy')).toHaveValue(copyId);
  await expect(reopened.getByLabel('Default model profile', { exact: true })).toHaveValue('general');
  await expect(reopened.getByLabel('Maximum retries', { exact: true })).toHaveValue('3');
  await expect(reopened.getByLabel('Cache TTL (seconds)')).toHaveValue('');
  const externalEdit = fixture.readPolicy(copyId);
  externalEdit.values.concurrency_limit = 2;
  fixture.writePolicy(copyId, externalEdit);
  await expect(reopened.getByLabel('Concurrency limit')).toHaveValue('2');
  await reopened.getByRole('button', { name: 'Close policy' }).click();
  const completed = await run(page, 'completed');
  expect(completed.policy.id).toBe(copyId);
  expect(completed.policy.values.default_model_profile).toBe('general');
  expect(completed.policy.values.cache_ttl).toBeNull();
  await attachJsonEvidence(testInfo, 'policy-copy', completed);
});
