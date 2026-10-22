import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { startGreetingsFixture } from '../helpers/greetingsFixture.mjs';
import { queryNestedEvidence, parentRunHistory, childRevisionGraph } from '../helpers/nestedRoutineFixture.mjs';
import { delegatedCheckpoint } from '../helpers/delegatedRoutineFixture.mjs';
import { attachJsonEvidence, observeRoutineEvents, removeRoutine } from '../helpers/routineFixture.mjs';

test.describe.configure({ mode: 'serial' });
// Completion notifications are delivered by the runtime heartbeat, which a full
// parallel suite can keep busy for minutes, so the async-completion wait is generous.
test.setTimeout(720_000);
test.beforeEach(({ page }) => page.setDefaultTimeout(30_000));
const quote = (value) => `'${value.replaceAll("'", "''")}'`;
const suffix = randomUUID().replaceAll('-', '');
const peerName = `pw342-greetings-${suffix.slice(0, 8)}`;
const objectName = `pw342-a2a-object-${suffix.slice(0, 8)}`;
let peer;
const graphs = [];
function persist(graph) {
  queryNestedEvidence(`INSERT INTO store(prefix,key,value,created_at,updated_at) VALUES ('progena.state',${quote(`routine:${graph.id}`)},${quote(JSON.stringify({ payload: graph }))}::jsonb,now(),now());`);
  graphs.push(graph); return graph;
}
async function chat(page, text) {
  if (!await page.locator('.qchat-input textarea').isVisible()) await page.getByRole('button', { name: 'Quick chat', exact: true }).click();
  await page.getByRole('button', { name: 'New chat', exact: true }).click();
  await page.locator('.qchat-input textarea').fill(text);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeEnabled({ timeout: 180_000 });
  const reply = await page.locator('.topnav-pane--quick').innerText();
  await page.locator('.topnav-pane--quick').getByRole('button', { name: 'Close', exact: true }).click();
  return reply;
}
async function run(page, graph, status) {
  const posted = page.waitForRequest((request) => request.method() === 'POST' && request.url().endsWith(`/${graph.id}/run`));
  await page.getByRole('button', { name: 'Run routine', exact: true }).click();
  const dialog = page.getByRole('dialog'); if (await dialog.isVisible()) await dialog.getByRole('button', { name: 'Run', exact: true }).click();
  const requestId = (await posted).headers()['x-request-id'];
  await expect.poll(() => page.evaluate((id) => window.__routineEvents.find((event) =>
    ['agent.routine.run.failed', 'agent.routine.run.completed'].includes(event.type) && event.payload.request_id === id)?.payload, requestId), { timeout: 180_000 }).toBeTruthy();
  const result = await page.evaluate((id) => window.__routineEvents.find((event) =>
    ['agent.routine.run.failed', 'agent.routine.run.completed'].includes(event.type) && event.payload.request_id === id).payload, requestId);
  expect(result.status, result.error).toBe(status); return result;
}
async function edit(page, modify) {
  const close = page.getByRole('button', { name: 'Close node details' }); if (await close.isVisible()) await close.click();
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  const editor = page.locator('.agents-graph-editor__textarea'); const graph = JSON.parse(await editor.inputValue()); modify(graph);
  await editor.fill(JSON.stringify(graph, null, 2)); await page.getByRole('button', { name: 'Apply changes' }).click();
  await expect(page.locator('.agents-graph-editor__saved')).toHaveText('Graph saved.', { timeout: 30_000 });
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
}
const asyncTasks = () => JSON.parse(queryNestedEvidence("SELECT value->'payload' FROM store WHERE prefix='progena.state' AND key='async_subagent_tasks';") || '[]');
test.beforeAll(async ({ browser }) => {
  test.setTimeout(240_000);
  peer = await startGreetingsFixture();
  const page = await browser.newPage({ baseURL: test.info().project.use.baseURL });
  try {
    await page.goto('/routines');
    await chat(page, `Register the disposable A2A peer using register_a2a_agent with name ${peerName} and URL ${peer.url}. Preserve existing resources. Do not run anything.`);
  } finally { await page.close(); }
});
test.afterAll(async ({ browser }) => {
  test.setTimeout(240_000);
  try {
    for (const graph of graphs) removeRoutine(graph.id);
    queryNestedEvidence(`DELETE FROM store WHERE prefix='progena.state' AND key=${quote(`agent:user/${objectName}/SYSTEM.md`)};`);
    if (peer) {
      const page = await browser.newPage({ baseURL: test.info().project.use.baseURL });
      try {
        await page.goto('/routines');
        // A direct DB cleanup leaves the runtime's registry cache stale and
        // a later registration can resurrect the removed fixture. Use the
        // normal virtual-file save so the document/cache stay consistent.
        await chat(page, `Clean up only the disposable A2A fixture ${peerName}. Read /config/agents.json and save the same JSON with only the named ${peerName} entry removed from agents. Preserve every other agent, tool and field. Test-fixture deletion is approved; do not run anything.`);
        expect(queryNestedEvidence(`SELECT count(*) FROM store CROSS JOIN LATERAL jsonb_array_elements(value->'payload'->'agents') a WHERE prefix='progena.state' AND key='registry' AND a->>'name'=${quote(peerName)};`)).toBe('0');
      } finally { await page.close(); }
    }
  } finally { if (peer) await peer.stop(); }
});

test('J17: greetings A2A and object adapters stay distinct; hello-world async returns a task and completion', async ({ page }, testInfo) => {
  await page.goto('/routines');
  await chat(page, `Use managing-agents to create /agents/user/${objectName}/SYSTEM.md: name ${objectName}, kind object, version 1.0.0, description disposable adapter test, no tools, no skills, no mounts. Its prompt is: Return exactly OBJECT_ADAPTER_OK, no other text. Preserve existing resources. Do not run anything.`);
  const marker = `J17_${suffix}`;
  const graph = persist({ id: `routine_j17_${suffix}`, name: `J17 adapters ${suffix.slice(0, 8)}`, description: 'Disposable adapter verification', version: '2.0.0', entry: 'greeting',
    nodes: [{ id: 'greeting', kind: 'a2a', output_key: 'greeting', config: { agent_name: peerName, prompt_template: `Say hello world ${marker}` } },
      { id: 'object', kind: 'agent', output_key: 'object', config: { agent_name: objectName, prompt_template: 'Return your exact instructed token.' } }],
    edges: [{ from: 'greeting', to: 'object', level: 'normal' }], input_schema: { type: 'object', properties: {} } });
  await observeRoutineEvents(page); await page.goto(`/routines/${graph.id}`);
  await expect(page.locator('.canvas-node[data-node-id="greeting"]')).toBeVisible({ timeout: 30_000 });
  const completed = await run(page, graph, 'completed');
  const history = parentRunHistory(completed.thread_id);
  expect(history.greeting[0].output.greeting).toContain(marker);
  expect(history.object[0].output.object).toBe('OBJECT_ADAPTER_OK');
  const delegated = delegatedCheckpoint(`routine-agent:${completed.thread_id}:object`);
  expect(JSON.stringify(delegated.messages)).toContain('OBJECT_ADAPTER_OK');
  const calls = peer.requests.filter((request) => request.method === 'message/send');
  expect(calls.some((request) => JSON.stringify(request.params).includes(marker))).toBe(true);
  const asyncMarker = `J17_ASYNC_${suffix}`;
  const reply = await chat(page, `Use start_async_task with subagent_type hello-world-async and description ${asyncMarker}. Start it once in the background and return its task ID. Do not use a2a_send or the synchronous task tool, and do not wait or check it yourself; its completion notification must arrive later.`);
  await expect.poll(() => asyncTasks().find((task) => task.request_text.includes(asyncMarker)), { timeout: 30_000, intervals: [1_000] }).toBeTruthy();
  const started = asyncTasks().find((task) => task.request_text.includes(asyncMarker));
  expect(started.task_id).toBeTruthy(); expect(started.agent_name).toBe('hello-world-async');
  await expect.poll(() => asyncTasks().find((task) => task.task_id === started.task_id)?.notification_sent_at, { timeout: 360_000, intervals: [1_000] }).toBeTruthy();
  const finished = asyncTasks().find((task) => task.task_id === started.task_id);
  expect(finished.status).toBe('success'); expect(finished.summary || finished.latest_message).toContain('HELLO_FROM_ASYNC_SUBAGENT');
  await attachJsonEvidence(testInfo, 'j17-adapter-evidence', { completed, history, delegated, calls, reply, started, finished });
});

test('J12: greetings sleep times out twice; increasing timeout succeeds and preserves the failed revision', async ({ page }, testInfo) => {
  const marker = `J12_${suffix}`;
  const graph = persist({ id: `routine_j12_${suffix}`, name: `J12 timeout ${suffix.slice(0, 8)}`, description: 'Controlled greetings sleep and timeout recovery', version: '2.0.0', entry: 'greeting',
    nodes: [{ id: 'greeting', kind: 'a2a', output_key: 'greeting', config: { agent_name: peerName, prompt_template: `sleep 3 seconds then greet me ${marker}`, timeout: 1, retry: { max_retries: 1, initial_interval: 0.1, jitter: false } } }], edges: [], input_schema: { type: 'object', properties: {} } });
  await observeRoutineEvents(page); await page.goto(`/routines/${graph.id}`);
  await expect(page.locator('.canvas-node[data-node-id="greeting"]')).toBeVisible({ timeout: 30_000 });
  const failed = await run(page, graph, 'failed');
  expect(failed.error).toMatch(/timeout|timed out/i);
  const attempts = peer.requests.filter((request) => request.method === 'message/send' && JSON.stringify(request.params).includes(marker));
  expect(attempts).toHaveLength(2);
  await expect(page.getByRole('button', { name: 'Run routine', exact: true })).toBeEnabled();
  await page.locator('.canvas-node[data-node-id="greeting"]').click();
  // Preserve the failure display before checking recovery. A timeout can
  // currently arrive globally while its node's run_meta is missing entirely.
  await expect.poll(() => page.evaluate((threadId) => window.__routineEvents.some((event) =>
    event.type === 'agent.routine.run_state.loaded' && event.payload.thread_id === threadId && event.payload.found), failed.thread_id), { timeout: 30_000 }).toBe(true);
  await expect(page.locator('.routine-node-inspector__run-error')).toContainText(/timeout|timed out/i, { timeout: 30_000 });
  const failedInspector = await page.locator('.routine-node-inspector').innerText();
  await testInfo.attach('timeout-inspector-before-repair', { body: await page.screenshot(), contentType: 'image/png' });
  const oldCheckpoint = delegatedCheckpoint(failed.thread_id);
  const oldRevision = childRevisionGraph(graph.id, failed.revision_id);
  await edit(page, (edited) => { edited.nodes[0].config.timeout = 5; });
  const completed = await run(page, graph, 'completed');
  expect(completed.thread_id).not.toBe(failed.thread_id); expect(completed.revision_id).not.toBe(failed.revision_id);
  await page.locator('.canvas-node[data-node-id="greeting"]').click();
  await expect(page.locator('.routine-node-inspector__run-output pre')).toContainText('3 seconds', { timeout: 30_000 });
  expect(peer.requests.filter((request) => request.method === 'message/send' && JSON.stringify(request.params).includes(marker))).toHaveLength(3);
  expect(delegatedCheckpoint(failed.thread_id)).toEqual(oldCheckpoint);
  expect(childRevisionGraph(graph.id, failed.revision_id)).toEqual(oldRevision);
  await attachJsonEvidence(testInfo, 'j12-timeout-evidence', { failed, completed, attempts, failedInspector, oldCheckpoint, oldRevision, completedHistory: parentRunHistory(completed.thread_id) });
  expect(failedInspector, 'The failed node inspector must expose its timeout error').toMatch(/timeout|timed out/i);
});
