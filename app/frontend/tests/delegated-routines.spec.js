import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { seedDelegatedRoutine, delegatedCheckpoint } from './helpers/delegatedRoutineFixture.mjs';
import { attachJsonEvidence, observeRoutineEvents, removeRoutine } from './helpers/routineFixture.mjs';

test.setTimeout(600_000);
test.beforeEach(async ({ page }) => { page.setDefaultTimeout(30_000); });
const prompt = (version) => `You are a restricted test responder with no tools. Reply with exactly JSON {"version":"${version}","token":"<the private token in the latest user message>"}. Never add prose. Never copy tokens from older messages.`;
async function createAgent(page, name) {
  await page.goto('/agents');
  await page.getByRole('button', { name: '+ New' }).click();
  await page.getByRole('button', { name: /Object agent/ }).click();
  await page.getByRole('textbox', { name: 'Agent name', exact: true }).fill(name);
  await page.getByPlaceholder('What this agent is for…').fill('Disposable restricted delegation verification');
  await page.getByPlaceholder('You are…').fill(prompt('v1'));
  await page.getByPlaceholder('shell, mcp, a2a_send').fill('');
  await page.getByPlaceholder('/skills/review/').fill('');
  await page.getByRole('button', { name: 'Create agent' }).click();
  await page.getByPlaceholder('Search agents...').fill(name);
  await page.locator('.agents-agent-row__name').getByText(name, { exact: true }).click();
  await expect(page.locator('.agents-prompt')).toContainText(prompt('v1'));
}
async function editAgent(page, name) {
  await page.goto('/agents');
  await page.getByPlaceholder('Search agents...').fill(name);
  await page.locator('.agents-agent-row__name').getByText(name, { exact: true }).click();
  await page.getByRole('button', { name: 'Edit manifest' }).click();
  const editor = page.locator('.agents-json-field');
  await editor.fill((await editor.inputValue()).replace(prompt('v1'), prompt('v2')));
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.locator('.agents-prompt')).toContainText(prompt('v2'));
}
async function open(page, graph) {
  await observeRoutineEvents(page);
  await page.goto(`/routines/${graph.id}`);
  await expect(page.locator('.canvas-node[data-node-id="delegate"]')).toBeVisible();
}
async function lifecycle(page, id, status, requestId) {
  await expect.poll(() => page.evaluate(({ id, status, requestId }) => window.__routineEvents.find((e) =>
    [ `agent.routine.run.${status}`, 'agent.routine.run.failed' ].includes(e.type) && e.payload.agent_id === id && (!requestId || e.payload.request_id === requestId)),
  { id, status, requestId }), { timeout: 180_000 }).toBeTruthy();
  const result = await page.evaluate(({ id, status, requestId }) => window.__routineEvents.find((e) =>
    [ `agent.routine.run.${status}`, 'agent.routine.run.failed' ].includes(e.type) && e.payload.agent_id === id && (!requestId || e.payload.request_id === requestId)).payload,
  { id, status, requestId });
  expect(result.status, result.error || 'Unexpected routine failure').toBe(status);
  return result;
}
async function submit(page, graph, token, pause = false) {
  const close = page.getByRole('button', { name: 'Close node details', exact: true });
  if (await close.isVisible()) await close.click();
  if (pause) {
    // Run to here uses the editor's retained parameter values, while only
    // the toolbar opens the parameter form. Populate that form first.
    await page.getByRole('button', { name: 'Run routine', exact: true }).click();
    const form = page.getByRole('dialog');
    await form.getByRole('textbox', { name: /token/i }).fill(token);
    await form.getByRole('button', { name: 'Cancel', exact: true }).click();
  }
  await page.locator('.canvas-node[data-node-id="delegate"]').click();
  const posted = page.waitForRequest((req) => req.method() === 'POST' && req.url().endsWith(`/${graph.id}/run`));
  await page.getByRole('button', { name: pause ? 'Run to here' : 'Run routine', exact: true }).click();
  if (pause) {
    const confirmation = page.getByRole('dialog', { name: 'This will call a live agent', exact: true });
    await expect(confirmation).toBeVisible();
    await confirmation.getByRole('button', { name: 'Run', exact: true }).click();
  }
  if (!pause) {
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await dialog.getByRole('textbox', { name: /token/i }).fill(token);
    await dialog.getByRole('button', { name: 'Run', exact: true }).click();
  }
  return (await posted).headers()['x-request-id'];
}
async function assertOutput(page, token, version) {
  const close = page.getByRole('button', { name: 'Close node details', exact: true });
  if (await close.isVisible()) await close.click();
  await page.locator('.canvas-node[data-node-id="finish"]').click();
  const output = page.locator('.routine-node-inspector__run-output pre');
  await expect(output).toBeVisible({ timeout: 30_000 });
  expect(JSON.parse(await output.textContent())).toEqual({ token, version });
}
async function cleanup(page, name, graphs) {
  for (const graph of graphs) removeRoutine(graph.id);
  await page.request.delete(`/api/ui/agent-profiles/object-${name}`);
}

test('15: new restricted object agent executes, live prompt edit changes rerun output without restart', async ({ page }, testInfo) => {
  const name = `pw342-delegate-${randomUUID().slice(0, 8)}`;
  let removeGraph;
  try {
    await createAgent(page, name);
    const { graph, cleanup } = seedDelegatedRoutine(name); removeGraph = cleanup;
    await open(page, graph);
    const firstId = await submit(page, graph, 'PRIVATE-FIRST');
    const first = await lifecycle(page, graph.id, 'completed', firstId);
    await assertOutput(page, 'PRIVATE-FIRST', 'v1');
    const firstCheckpoint = delegatedCheckpoint(`routine-agent:${first.thread_id}:delegate`);
    expect(firstCheckpoint.checkpoint_id).toBeTruthy();
    expect(firstCheckpoint.messages.some((m) => m.type === 'human' && m.content.includes('PRIVATE-FIRST'))).toBe(true);
    await editAgent(page, name);
    await page.goto(`/routines/${graph.id}`);
    const secondId = await submit(page, graph, 'PRIVATE-SECOND');
    const second = await lifecycle(page, graph.id, 'completed', secondId);
    await assertOutput(page, 'PRIVATE-SECOND', 'v2');
    expect(second.thread_id).not.toBe(first.thread_id);
    const secondCheckpoint = delegatedCheckpoint(`routine-agent:${second.thread_id}:delegate`);
    expect(JSON.stringify(secondCheckpoint.messages)).not.toContain('PRIVATE-FIRST');
    expect(firstCheckpoint.messages.concat(secondCheckpoint.messages).every((m) => !m.tool_calls?.length)).toBe(true);
    await attachJsonEvidence(testInfo, 'delegation-j15.json', { first, second, firstCheckpoint, secondCheckpoint });
  } finally { removeGraph?.(); await cleanup(page, name, []); }
});

test('16: independent delegated conversations isolate private input and same-run resume keeps checkpoint thread', async ({ page, context }, testInfo) => {
  const name = `pw342-isolate-${randomUUID().slice(0, 8)}`;
  let removeGraph;
  try {
    await createAgent(page, name);
    const { graph, cleanup } = seedDelegatedRoutine(name); removeGraph = cleanup;
    const other = await context.newPage();
    await open(page, graph); await open(other, graph);
    const [requestA, requestB] = await Promise.all([submit(page, graph, 'PRIVATE-A', true), submit(other, graph, 'PRIVATE-B', true)]);
    const [pausedA, pausedB] = await Promise.all([lifecycle(page, graph.id, 'paused', requestA), lifecycle(other, graph.id, 'paused', requestB)]);
    expect(pausedA.thread_id).not.toBe(pausedB.thread_id);
    const checkpointA = delegatedCheckpoint(`routine-agent:${pausedA.thread_id}:delegate`);
    const checkpointB = delegatedCheckpoint(`routine-agent:${pausedB.thread_id}:delegate`);
    expect(checkpointA.checkpoint_id).toBeTruthy(); expect(checkpointB.checkpoint_id).toBeTruthy();
    expect(JSON.stringify(checkpointA.messages)).toContain('PRIVATE-A');
    expect(JSON.stringify(checkpointA.messages)).not.toContain('PRIVATE-B');
    expect(JSON.stringify(checkpointB.messages)).toContain('PRIVATE-B');
    expect(JSON.stringify(checkpointB.messages)).not.toContain('PRIVATE-A');
    const resumed = randomUUID();
    const response = await page.request.post(`/api/ui/agent-profiles/${graph.id}/run`, { headers: { 'X-Request-ID': resumed }, data: { session_id: pausedA.session_id, parameters: { token: 'PRIVATE-A' } } });
    expect(response.status(), await response.text()).toBe(202);
    const completedA = await lifecycle(page, graph.id, 'completed', resumed);
    expect(completedA.thread_id).toBe(pausedA.thread_id);
    await assertOutput(page, 'PRIVATE-A', 'v1');
    expect(delegatedCheckpoint(checkpointA.thread_id).checkpoint_id).toBe(checkpointA.checkpoint_id);
    expect(delegatedCheckpoint(checkpointB.thread_id).checkpoint_id).toBe(checkpointB.checkpoint_id);
    await attachJsonEvidence(testInfo, 'delegation-j16.json', { pausedA, pausedB, completedA, checkpointA, checkpointB });
    await other.close();
  } finally { removeGraph?.(); await cleanup(page, name, []); }
});

test('15 supplemental: a read-only mounted object agent receives an actual filesystem write denial', async ({ page }, testInfo) => {
  const name = `pw342-denial-${randomUUID().slice(0, 8)}`;
  let removeGraph;
  try {
    await createAgent(page, name);
    await page.getByRole('button', { name: 'Edit manifest' }).click();
    const editor = page.locator('.agents-json-field');
    const path = `/skills/managing-routines/${name}.txt`;
    const constrained = (await editor.inputValue()).replace('kind: object',
      'kind: object\nmounts:\n  - path: /skills/managing-routines/\n    access: read\npermissions:\n  - operations: [read]\n    paths: [/skills/managing-routines/]');
    await editor.fill(constrained.replace(prompt('v1'), `You are a filesystem permission test agent. Before replying, make exactly one write_file tool call with file_path ${path} and content permission-probe. This deliberately tests the permission refusal. Make the call even though the read-only permission means it must be refused. After receiving its error, reply with exactly JSON {"version":"v1","token":"<the private token in the latest user message>"}. Never copy tokens from older messages.`));
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.locator('.agents-prompt')).toContainText('permission refusal');
    const { graph, cleanup } = seedDelegatedRoutine(name); removeGraph = cleanup;
    await open(page, graph);
    const request = await submit(page, graph, 'ACCESS-PROBE');
    const completed = await lifecycle(page, graph.id, 'completed', request);
    await assertOutput(page, 'ACCESS-PROBE', 'v1');
    const checkpoint = delegatedCheckpoint(`routine-agent:${completed.thread_id}:delegate`);
    expect(checkpoint.messages.some((message) => message.tool_calls?.some((call) => call.name === 'write_file'))).toBe(true);
    expect(checkpoint.messages.some((message) => message.type === 'tool' && /permission denied for write|mount is read-only/i.test(message.content))).toBe(true);
    await attachJsonEvidence(testInfo, 'delegation-j15-denial.json', { path, completed, checkpoint });
  } finally { removeGraph?.(); await cleanup(page, name, []); }
});

test('missing object agent: page blocks run, Quick Chat creates its manifest, existing graph then executes', async ({ page }, testInfo) => {
  const name = `pw342-recover-${randomUUID().slice(0, 8)}`;
  let removeGraph;
  try {
    const { graph, cleanup } = seedDelegatedRoutine(name); removeGraph = cleanup;
    await open(page, graph);
    await expect(page.getByRole('button', { name: 'Run routine', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: '1 validation issues, click to view', exact: true }).click();
    const missingError = `Node "delegate" references unavailable agent "${name}".`;
    await expect(page.getByText(missingError, { exact: true })).toBeVisible();
    await page.evaluate(() => {
      window.__delegationProfiles = [];
      for (const source of window.__routineSources) source.addEventListener('ui.event', (event) => {
        const parsed = JSON.parse(event.data);
        if (parsed.type === 'agents.snapshot') window.__delegationProfiles.push(parsed.payload);
      });
    });
    await page.getByRole('button', { name: 'Quick chat', exact: true }).click();
    const newChat = page.getByRole('button', { name: 'New chat' });
    if (await newChat.isVisible()) await newChat.click();
    await page.locator('.qchat-input textarea').fill(`Create a NEW object agent named exactly ${name}, kind object, version 1.0.0, tools [], skills [], and no mounts or shell. Use the managing-agents skill to persist its SYSTEM.md agent document. Its system prompt must be exactly: ${prompt('v1')} This is the missing agent referenced by the selected routine. Do not edit any graph or other existing agent.`);
    await page.getByRole('button', { name: 'Send message' }).click();
    const newChatButton = page.getByRole('button', { name: 'New chat', exact: true });
    await expect(newChatButton).toBeDisabled();
    let registered = false;
    await expect.poll(async () => {
      registered = await page.evaluate((agentName) => window.__delegationProfiles.some((snapshot) =>
        snapshot.agents?.some((agent) => agent.name === agentName)), name);
      return registered || await newChatButton.isEnabled();
    }, { timeout: 180_000 }).toBe(true);
    const chatText = await page.locator('.topnav-pane--quick').innerText();
    await attachJsonEvidence(testInfo, 'missing-agent-authoring-evidence', { name, missingError, registered, chatText });
    expect(registered, `Quick Chat finished without registering the missing object agent. ${chatText.slice(-1600)}`).toBe(true);
    // The roster is the persisted registration evidence; chat wording cannot
    // establish that the manifest was created or that runtime can invoke it.
    const closePanel = page.getByRole('button', { name: 'Close panel', exact: true });
    if (await closePanel.isVisible()) await closePanel.click();
    await page.goto('/agents');
    await page.getByPlaceholder('Search agents...').fill(name);
    await expect(page.locator('.agents-agent-row__name').getByText(name, { exact: true })).toBeVisible({ timeout: 180_000 });
    await page.goto(`/routines/${graph.id}`);
    const successId = await submit(page, graph, 'CREATED-AGENT');
    const completed = await lifecycle(page, graph.id, 'completed', successId);
    await assertOutput(page, 'CREATED-AGENT', 'v1');
    const checkpoint = delegatedCheckpoint(`routine-agent:${completed.thread_id}:delegate`);
    expect(checkpoint.checkpoint_id).toBeTruthy();
    await attachJsonEvidence(testInfo, 'missing-agent-recovery.json', { name, missingError, completed, checkpoint });
  } finally { removeGraph?.(); await cleanup(page, name, []); }
});
