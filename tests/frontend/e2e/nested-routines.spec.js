import { test, expect } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { observeRoutineEvents } from '../helpers/routineFixture.mjs';
import { seedNestedFixtures, removeNestedFixtures, nestedChildThreads, parentCheckpointChannels, parentRunHistory, childRevisionGraph } from '../helpers/nestedRoutineFixture.mjs';
// Disposable fixtures exercise live runtime/UI; setup does not author user data.
// J14 uses Run to here as a controllable native checkpoint pause, not human approval.
test.setTimeout(600_000);
test.beforeEach(({page}) => page.setDefaultTimeout(30_000));
async function attachEvidence(testInfo, name, evidence) {
  const path = testInfo.outputPath(`${name}.json`);
  await writeFile(path, JSON.stringify(evidence, null, 2));
  await testInfo.attach(name, { path, contentType: 'application/json' });
}
async function open(page, graph) { await page.goto(`/routines/${graph.id}`); await expect(page.locator(`.canvas-node[data-node-id="${graph.entry}"]`)).toBeVisible({ timeout: 30_000 }); }
async function run(page, graph, status = 'completed', target) {
  const posted = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith(`/${graph.id}/run`));
  if (target) { const close = page.getByRole('button', { name: 'Close node details' }); if (await close.isVisible()) await close.click(); await page.locator(`.canvas-node[data-node-id="${target}"]`).click(); await page.getByRole('button', { name: 'Run to here', exact: true }).click(); await page.getByRole('dialog').getByRole('button', { name: 'Run', exact: true }).click(); }
  else await page.getByRole('button', { name: 'Run routine', exact: true }).click();
  const requestId = (await posted).headers()['x-request-id'];
  await expect.poll(() => page.evaluate(({requestId,status}) => window.__routineEvents.find((e) => [ `agent.routine.run.${status}`, 'agent.routine.run.failed' ].includes(e.type) && e.payload.request_id === requestId)?.payload, { requestId, status }), { timeout: 180_000 }).toBeTruthy();
  const result = await page.evaluate(({requestId,status}) => window.__routineEvents.find((e) => [ `agent.routine.run.${status}`, 'agent.routine.run.failed' ].includes(e.type) && e.payload.request_id === requestId).payload, { requestId, status });
  expect(result.status, result.error).toBe(status);
  return result;
}
async function output(page, id, value) { const close = page.getByRole('button', { name: 'Close node details' }); if (await close.isVisible()) await close.click(); await page.locator(`.canvas-node[data-node-id="${id}"]`).click(); await expect(page.locator('.routine-node-inspector__run-output pre')).toHaveText(value, { timeout: 30_000 }); }
async function editChild(page, child) {
  await open(page, child); await page.getByRole('button', { name: 'Edit as JSON' }).click();
  const editor = page.locator('.agents-graph-editor__textarea'); const graph = JSON.parse(await editor.inputValue()); graph.nodes[0].config.expression = 'value + payload.offset + 100';
  await editor.fill(JSON.stringify(graph, null, 2)); await page.getByRole('button', { name: 'Apply changes' }).click();
  await expect(page.locator('.agents-graph-editor__saved')).toHaveText('Graph saved.', { timeout: 30_000 }); await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
}
for (const declaredSchemas of [false, true]) test(`J13: structured parent/child mappings and edited child rerun (declared schemas ${declaredSchemas})`, async ({ page }, testInfo) => {
  const fixtures = seedNestedFixtures({ declaredSchemas }); await observeRoutineEvents(page);
  try {
    await open(page, fixtures.parent); const first = await run(page, fixtures.parent); await output(page, 'final', '42');
    await editChild(page, fixtures.child); await open(page, fixtures.parent); const second = await run(page, fixtures.parent); await output(page, 'final', '142');
    expect(second.session_id).not.toBe(first.session_id); const threads = nestedChildThreads(fixtures.child.id); expect(new Set(threads.map((t) => t.thread_id.split(':')[2])).size).toBe(2);
    await attachEvidence(testInfo, 'nested-revision-evidence', { first, second, threads });
  } finally { removeNestedFixtures(fixtures); }
});
test('J14: paused parent uses latest child and records exact revisions for each invocation', async ({ page }, testInfo) => {
  const fixtures = seedNestedFixtures({ repeatedChild: true }); await observeRoutineEvents(page);
  try {
    await open(page, fixtures.parent); const paused = await run(page, fixtures.parent, 'paused', 'nested_first'); const before = nestedChildThreads(fixtures.child.id); expect(before).toHaveLength(1);
    const childPage = await page.context().newPage(); await editChild(childPage, fixtures.child); await childPage.close(); const completed = await run(page, fixtures.parent, 'completed', 'final'); expect(completed.session_id).toBe(paused.session_id);
    const after = nestedChildThreads(fixtures.child.id);
    const close = page.getByRole('button', { name: 'Close node details' });
    if (await close.isVisible()) await close.click();
    await page.locator('.canvas-node[data-node-id="final"]').click();
    const result = page.locator('.routine-node-inspector__run-output pre');
    await expect(result).toBeVisible({ timeout: 30_000 });
    const resumedOutput = await result.innerText();
    expect(resumedOutput, 'The next child invocation resolves its current live revision').toBe('42142');
    const history = parentRunHistory(paused.thread_id);
    const firstCall = history.nested_first[0].routine;
    const secondCall = history.nested_second[0].routine;
    expect(firstCall.agent_id).toBe(fixtures.child.id);
    expect(secondCall.agent_id).toBe(fixtures.child.id);
    expect(firstCall.revision_id).toMatch(/^[a-f0-9]{64}$/);
    expect(secondCall.revision_id).not.toBe(firstCall.revision_id);
    expect(secondCall.request_id).not.toBe(firstCall.request_id);
    expect(firstCall.parent).toEqual({ thread_id: paused.thread_id, node_id: 'nested_first' });
    expect(secondCall.parent).toEqual({ thread_id: paused.thread_id, node_id: 'nested_second' });
    expect(firstCall.thread_id.split(':')[2]).toBe(firstCall.revision_id);
    expect(secondCall.thread_id.split(':')[2]).toBe(secondCall.revision_id);
    expect(childRevisionGraph(fixtures.child.id, firstCall.revision_id).nodes[0].config.expression).toBe('value + payload.offset');
    expect(childRevisionGraph(fixtures.child.id, secondCall.revision_id).nodes[0].config.expression).toBe('value + payload.offset + 100');
    await expect.poll(() => page.evaluate((threadId) => window.__routineEvents.filter((e) => e.type === 'agent.routine.run_state.loaded' && e.payload.thread_id === threadId).at(-1)?.payload.run_meta?.nested_second?.[0]?.routine?.revision_id, paused.thread_id)).toBe(secondCall.revision_id);
    await page.getByRole('button', { name: 'Close node details' }).click();
    await page.locator('.canvas-node[data-node-id="nested_first"]').click();
    await expect(page.locator('.routine-node-inspector__run-stats')).toContainText(firstCall.revision_id);
    await page.getByRole('button', { name: 'Close node details' }).click();
    await page.locator('.canvas-node[data-node-id="nested_second"]').click();
    await expect(page.locator('.routine-node-inspector__run-stats')).toContainText(secondCall.revision_id);
    await page.reload();
    const fresh = await run(page, fixtures.parent);
    await output(page, 'final', '142142');
    expect(fresh.session_id).not.toBe(paused.session_id);
    expect(fresh.revision_id).toBe(paused.revision_id);
    const freshHistory = parentRunHistory(fresh.thread_id);
    expect(freshHistory.nested_first[0].routine.revision_id).toBe(secondCall.revision_id);
    expect(freshHistory.nested_second[0].routine.revision_id).toBe(secondCall.revision_id);
    expect(parentRunHistory(paused.thread_id)).toEqual(history);
    await attachEvidence(testInfo, 'paused-child-revision-evidence', { paused, completed, fresh, resumedOutput, before, after,
      history, freshHistory, allChildThreads: nestedChildThreads(fixtures.child.id), parentChannels: parentCheckpointChannels(paused.thread_id) });
  } finally { removeNestedFixtures(fixtures); }
});
