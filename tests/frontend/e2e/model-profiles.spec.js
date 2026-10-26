import { test, expect } from '@playwright/test';
import { observeRoutineEvents, attachJsonEvidence } from '../helpers/routineFixture.mjs';
import { seedModelProfileRoutine } from '../helpers/modelProfileFixture.mjs';
import { readAgentRun } from '../helpers/agentExecutionFixture.mjs';

test.setTimeout(420_000);
let fixture;
test.afterEach(() => { fixture?.cleanup(); fixture = null; });
async function selectNode(page, id) {
  const close = page.getByRole('button', { name: 'Close node details', exact: true });
  if (await close.isVisible()) await close.click();
  await page.locator(`.canvas-node[data-node-id="${id}"]`).click();
}
async function run(page, expected) {
  const posted = page.waitForRequest((request) => request.method() === 'POST' && request.url().endsWith(`/${fixture.graph.id}/run`));
  await page.getByRole('button', { name: 'Run routine', exact: true }).click();
  const requestId = (await posted).headers()['x-operation-id'];
  await expect.poll(() => page.evaluate((id) => window.__routineEvents.find((event) =>
    ['agent.routine.run.completed', 'agent.routine.run.failed'].includes(event.type) && event.payload.request_id === id), requestId), { timeout: 180_000 }).toBeTruthy();
  const event = await page.evaluate((id) => window.__routineEvents.find((event) =>
    ['agent.routine.run.completed', 'agent.routine.run.failed'].includes(event.type) && event.payload.request_id === id), requestId);
  expect(event.type, JSON.stringify(event.payload)).toBe(`agent.routine.run.${expected}`);
  return event.payload;
}
for (const [kind, execution] of [['prompt', 'wait'], ['classify', 'wait'], ['agent', 'wait'], ['agent', 'background']]) {
  test(`${kind} ${execution}: selected profile persists, executes, and a removed profile fails explicitly`, async ({ page }, testInfo) => {
    page.setDefaultTimeout(30_000);
    fixture = seedModelProfileRoutine(kind, execution);
    await observeRoutineEvents(page);
    await page.goto(`/routines/${fixture.graph.id}`);
    await selectNode(page, 'call');
    const selector = page.getByLabel('Model profile', { exact: true });
    await expect(selector).toHaveValue('');
    await expect(selector.locator(`option[value="${fixture.profile}"]`)).toHaveText(`${fixture.profile} — ${fixture.deployment}`);
    await selector.selectOption(fixture.profile);
    await expect(page.getByRole('status').filter({ hasText: /^Saved$/ })).toBeVisible();
    await page.reload();
    await selectNode(page, 'call');
    await expect(page.getByLabel('Model profile', { exact: true })).toHaveValue(fixture.profile);
    const completed = await run(page, 'completed');
    await selectNode(page, 'after');
    await expect(page.getByLabel('Model profile', { exact: true })).toHaveCount(0);
    const output = page.locator('.routine-node-inspector__run-output pre');
    await expect(output).toContainText(execution === 'background' ? 'task_id' : 'PROFILE_DONE');
    let background;
    if (execution === 'background') {
      const task = JSON.parse(await output.textContent());
      expect(readAgentRun(task).model_profile).toBe(fixture.profile);
      await expect.poll(() => readAgentRun(task)?.status, { timeout: 90_000, intervals: [1_000] }).toBe('success');
      background = readAgentRun(task);
      expect(JSON.stringify(background.values.messages.at(-1).content)).toContain('PROFILE_DONE');
    }
    fixture.removeProfile();
    await page.reload();
    await selectNode(page, 'call');
    await expect(page.getByLabel('Model profile', { exact: true })).toHaveValue(fixture.profile);
    const failed = await run(page, 'failed');
    expect(failed.error).toContain('Unknown model profile');
    await attachJsonEvidence(testInfo, `profile-${kind}-${execution}`, { completed, failed, background, profile: fixture.profile, deployment: fixture.deployment });
  });
}
