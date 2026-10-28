import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { writeFileSync } from 'node:fs';
import { observeRoutineEvents, removeRoutine } from '../helpers/routineFixture.mjs';

const databaseUrl = process.env.PROGENA_DATABASE_URL || 'postgresql://progena:progena@127.0.0.1:15433/progena';
const projectRoot = fileURLToPath(new URL('../../../', import.meta.url));
const evidenceScript = fileURLToPath(new URL('../helpers/routineToolEvidence.py', import.meta.url));

test.setTimeout(600_000);
const created = [];
test.afterEach(() => { for (const id of created.splice(0)) removeRoutine(id); });

async function chat(page, prompt, { fresh = false } = {}) {
  await page.getByRole('button', { name: '+ New', exact: true }).click();
  if (fresh && await page.getByRole('button', { name: 'New chat', exact: true }).isVisible()) {
    await page.getByRole('button', { name: 'New chat', exact: true }).click();
  }
  await page.locator('.qchat-input textarea, .qchat-input input').first().fill(prompt);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
}
async function closeChat(page) {
  const close = page.getByRole('button', { name: 'Close panel', exact: true });
  if (await close.isVisible()) await close.click();
  await expect(page.locator('.topnav-scrim')).toBeHidden();
}
async function readGraph(page) {
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  const graph = JSON.parse(await page.locator('.agents-graph-editor textarea').inputValue());
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
  return { ...graph, id: decodeURIComponent(new URL(page.url()).pathname.split('/').at(-1)) };
}
async function saveGraph(page, graph) {
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  await page.locator('.agents-graph-editor textarea').fill(JSON.stringify(graph));
  await page.getByRole('button', { name: 'Apply changes' }).click();
  await expect(page.locator('.agents-graph-editor__saved')).toHaveText('Graph saved.', { timeout: 30_000 });
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
}
async function create(page, name, number) {
  await page.goto('/routines');
  await chat(page, `Create a NEW separate routine named exactly ${name}. One deterministic eval node only, expression ${number}, output_key result; no input parameters. Use the managing-routines skill and fresh generated routine/node IDs. Do not modify any existing routine.`, { fresh: true });
  const row = page.getByRole('button', { name: new RegExp(name) });
  await expect(row).toHaveCount(1, { timeout: 180_000 });
  await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeEnabled({ timeout: 180_000 });
  await closeChat(page);
  await row.click();
  await expect(page.locator('.canvas-node')).toHaveCount(1, { timeout: 30_000 });
  const graph = await readGraph(page);
  graph.id = decodeURIComponent(new URL(page.url()).pathname.split('/').at(-1));
  graph.name = name;
  created.push(graph.id);
  expect(graph.nodes[0].config.expression).toBe(String(number));
  return graph;
}

test('19 quick-chat routines survive rapid switching, SSE reconnect, and sequential UI/agent edits', async ({ page }, testInfo) => {
  await page.clock.install();
  await observeRoutineEvents(page);
  const suffix = Date.now().toString(36);
  const first = await create(page, `pw342-j19-first-${suffix}`, 19);
  const second = await create(page, `pw342-j19-second-${suffix}`, 29);
  const graphRequests = [];
  page.on('request', (request) => { if (request.url().includes('/graph')) graphRequests.push(request.url()); });
  // Queue selections in adjacent animation frames while finite detail loads are active.
  for (const graph of [first, second, first, second]) {
    await page.getByRole('button', { name: new RegExp(graph.name) }).click();
  }
  await expect(page).toHaveURL(new RegExp(second.id));
  await expect(page.locator(`.canvas-node[data-node-id="${second.nodes[0].id}"]`)).toBeVisible();
  expect((await readGraph(page)).id).toBe(second.id);
  await page.evaluate(() => {
    window.__routineEvents = [];
    const source = window.__routineSources.findLast((item) => item.url.includes('/api/ui/stream'));
    source.close(); source.dispatchEvent(new Event('error'));
  });
  await page.clock.fastForward(60_000);
  await expect.poll(() => page.evaluate((id) => window.__routineEvents.some((event) =>
    event.type === 'agent.routine.subscription.updated' && event.payload.agent_id === id && event.payload.subscribed), second.id),
  { timeout: 30_000 }).toBe(true);
  const userLabel = `User edit ${suffix}`;
  second.nodes[0].label = userLabel;
  await saveGraph(page, second);
  const editSubmitted = Date.now() / 1000;
  await chat(page, `Modify ONLY routine ${second.id}, file /routines/${second.id}.json. Read the current persisted file first, preserve its current node label exactly, and change only the eval expression from 29 to 39. This is an edit, not creation. Do not rename it or change other routines.`, { fresh: true });
  // Closing quick chat cancels its active agent stream. Keep it mounted until
  // the edit is actually persisted and the assistant turn completes.
  await expect.poll(() => {
    const raw = execFileSync('psql', [databaseUrl, '-Atc',
      `SELECT value->'payload'->'nodes' FROM store WHERE prefix='progena.state' AND key='routine:${second.id.replaceAll("'", "''")}'`], { encoding: 'utf8' });
    return JSON.parse(raw.trim())[0].config.expression;
  }, { timeout: 180_000, intervals: [1_000] }).toBe('39');
  await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeEnabled({ timeout: 180_000 });
  await closeChat(page);
  expect((await readGraph(page)).nodes[0].config.expression).toBe('39');
  expect((await readGraph(page)).nodes[0].label).toBe(userLabel);
  const calls = JSON.parse(execFileSync('bash', ['-c', 'source .venv/bin/activate && python "$1" "$2"', 'routine-evidence', evidenceScript, second.id], { cwd: projectRoot, encoding: 'utf8' }));
  const edited = calls.find((item) => item.calls.some((call) => ['edit_file', 'write_file'].includes(call.name)));
  expect(edited, 'Checkpointed tool calls must contain the agent edit').toBeTruthy();
  const writes = edited.calls.filter((call) => ['edit_file', 'write_file'].includes(call.name));
  expect(edited.calls.some((call) => call.name === 'read_file' && call.order < writes.at(-1).order), 'Read-before-write checkpoint evidence').toBe(true);
  await page.getByRole('button', { name: 'Run routine', exact: true }).click();
  await expect.poll(() => page.evaluate((id) => window.__routineEvents.some((event) => event.type === 'agent.routine.run.completed' && event.payload.agent_id === id), second.id), { timeout: 180_000 }).toBe(true);
  await page.locator(`.canvas-node[data-node-id="${second.nodes[0].id}"]`).click();
  await expect(page.locator('.routine-node-inspector__run-output pre')).toHaveText('39');
  const evidencePath = testInfo.outputPath('j19-evidence.json');
  writeFileSync(evidencePath, JSON.stringify({ first: first.id, second: second.id, graphRequests, calls, events: await page.evaluate(() => window.__routineEvents) }, null, 2));
  await testInfo.attach('j19-evidence.json', { path: evidencePath, contentType: 'application/json' });
});
