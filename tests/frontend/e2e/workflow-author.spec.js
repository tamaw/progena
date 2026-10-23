import { execFileSync } from 'node:child_process';
import { test, expect } from '@playwright/test';

/**
 * Progena authors workflows from quick chat, then they run end to end:
 * a cron workflow on demand (▶) and on a real backend clock fire, and an
 * event → extract → agent workflow on a real kanban card creation.
 *
 * Runs against the live stack with a real LLM (like the routine journeys) and
 * reads run records from the local Postgres used by docs/LOCAL_STARTUP.md.
 * Everything it creates is named with a per-run suffix and deleted afterwards.
 */

const RUN = Date.now().toString(36);
const CRON_TITLE = `pw-cron-${RUN}`;
const EVENT_TITLE = `pw-event-${RUN}`;
const CARD_TITLE = `pw-card-${RUN} Production database is down for all customers`;
const created = [];
const quickChatThreadIds = [];

test.describe.configure({ mode: 'serial' });
test.setTimeout(600_000);

function sql(query) {
  return execFileSync('psql', ['-h', '127.0.0.1', '-p', '15433', '-U', 'progena', '-d', 'progena', '-qAtc', query], {
    env: { ...process.env, PGPASSWORD: process.env.PGPASSWORD || 'progena' },
    encoding: 'utf8',
  }).trim();
}

function workflowByTitle(title) {
  const row = sql(`select value->'payload' from store where key like 'workflow:%' and value->'payload'->>'title' = '${title}'`);
  return row ? JSON.parse(row) : null;
}

function runsOf(workflowId) {
  const rows = sql(`select value->'payload' from store where key like 'workflow_run:%' and value->'payload'->>'workflow_id' = '${workflowId}' order by value->'payload'->>'queued_at'`);
  return rows ? rows.split('\n').map((line) => JSON.parse(line)) : [];
}

function trackPageErrors(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  return errors;
}

function sqlLiteral(text) {
  return `'${text.replaceAll("'", "''")}'`;
}

// Quick chat prefixes the typed text with a page-context block.
function quickChatThreadForMessage(text) {
  const row = sql(`select value->'payload'->>'thread_id' from store where key like 'session:%' and strpos(value->'payload'->>'first_message', ${sqlLiteral(text)}) > 0`);
  return row || null;
}

async function askProgena(page, text) {
  await page.goto('/workflows');
  await page.getByRole('button', { name: '+ New' }).click();
  const input = page.locator('.qchat-input textarea, .qchat-input input').first();
  await expect(input).toBeVisible({ timeout: 10_000 });
  await input.fill(text);
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect.poll(() => quickChatThreadForMessage(text), { timeout: 30_000, intervals: [500] }).toBeTruthy();
  const threadId = quickChatThreadForMessage(text);
  expect(threadId).toBeTruthy();
  quickChatThreadIds.push(threadId);
  await expect(input).toBeEnabled({ timeout: 300_000 });
}

async function closeQuickChat(page) {
  const closePanel = page.getByRole('button', { name: 'Close panel' });
  if (await closePanel.isVisible()) await closePanel.click();
  await expect(page.locator('.topnav-scrim')).toBeHidden();
}
async function runCards(page, title) {
  await page.goto('/workflows');
  await page.getByRole('button', { name: new RegExp(title) }).click();
  await page.getByRole('button', { name: 'Run history' }).click();
  return page.locator('.workflow-run-card');
}

function sqlRows(query) {
  const out = sql(query);
  return out ? out.split('\n') : [];
}

// Remove everything the tests created: the quick chats that asked Progena,
// each workflow with its runs and run threads, and the kanban card (through the
// card route, so board bookkeeping such as card_order is updated too).
test.afterAll(async ({ request }) => {
  for (const threadId of quickChatThreadIds) {
    await request.delete(`/api/ui/chats/${threadId}`);
  }
  const threads = [...quickChatThreadIds];
  // A turn that outlives its test still creates the workflow, after `created`
  // was last updated, so look the titles up too (the cron one fires every minute).
  const workflowIds = new Set(created);
  for (const title of [CRON_TITLE, EVENT_TITLE]) {
    const workflow = workflowByTitle(title);
    if (workflow) workflowIds.add(workflow.id);
  }
  for (const id of workflowIds) {
    threads.push(...runsOf(id).map((run) => run.thread_id).filter(Boolean));
    await request.delete(`/api/ui/workflows/${id}`);
    sql(`delete from store where key like 'workflow_run:%' and value->'payload'->>'workflow_id' = '${id}'`);
  }
  if (threads.length) {
    const list = [...new Set(threads)].map((thread) => `'${thread}'`).join(',');
    for (const table of ['checkpoint_writes', 'checkpoint_blobs', 'checkpoints']) {
      sql(`delete from ${table} where thread_id in (${list})`);
    }
  }
  for (const cardId of sqlRows(`select value->'payload'->>'id' from store where key like 'kanban_card:%' and value->'payload'->>'title' like 'pw-card-${RUN}%'`)) {
    await request.delete(`/api/ui/kanban/cards/${cardId}`);
  }
});

test('Progena builds a cron workflow that runs on demand and on schedule', async ({ page, request }) => {
  const errors = trackPageErrors(page);
  await askProgena(page,
    `Create a NEW workflow titled exactly "${CRON_TITLE}". Trigger: a cron node firing every minute ("* * * * *") in timezone Europe/London. ` +
    'Then one agent node whose inline template is: "Automated workflow test. Reply with exactly one line: CRON_OK {scheduled_for}. Do not call any tools." ' +
    "Wire the cron node's scheduled_for output into the agent's scheduled_for input. Do not edit any other workflow.");

  await expect.poll(() => workflowByTitle(CRON_TITLE)?.id, { timeout: 240_000, intervals: [2000] }).toBeTruthy();
  const workflow = workflowByTitle(CRON_TITLE);
  created.push(workflow.id);
  const nodes = Object.values(workflow.graph.nodes);
  expect(nodes.find((node) => node.kind === 'cron'), JSON.stringify(workflow.graph))
    .toMatchObject({ expression: '* * * * *', timezone: 'Europe/London' });
  expect(nodes.some((node) => node.kind === 'agent')).toBe(true);

  await closeQuickChat(page);
  await page.goto('/workflows');
  const row = page.getByRole('button', { name: new RegExp(CRON_TITLE) });
  await expect(row).toContainText('cron * * * * *');
  await row.click();
  await expect(page.locator('.react-flow__node .node-type-icon.cron')).toBeVisible();

  await page.getByRole('button', { name: 'Run workflow now' }).click();
  await expect.poll(() => runsOf(workflow.id).some((run) => run.source === 'manual' && run.status === 'completed'),
    { timeout: 180_000, intervals: [2000] }).toBe(true);
  await expect.poll(() => runsOf(workflow.id).some((run) => run.source === 'cron' && run.status === 'completed'),
    { timeout: 240_000, intervals: [3000] }).toBe(true);
  // Stop the every-minute schedule before inspecting.
  await request.post(`/api/ui/workflows/${workflow.id}/pause`);

  const scheduled = runsOf(workflow.id).find((run) => run.source === 'cron' && run.status === 'completed');
  // A clock fire is due on a whole minute, and the template saw that exact time.
  expect(scheduled.trigger_event.scheduled_for).toMatch(/T\d\d:\d\d:00\+\d\d:\d\d$/);
  expect(scheduled.last_output).toContain(`CRON_OK ${scheduled.trigger_event.scheduled_for}`);

  const cards = await runCards(page, CRON_TITLE);
  await expect(cards.filter({ hasText: 'CRON_OK' }).first()).toBeVisible({ timeout: 30_000 });
  expect(errors).toEqual([]);
});

test('Progena builds an event workflow that extracts and reports on a new card', async ({ page, request }) => {
  const errors = trackPageErrors(page);
  await askProgena(page,
    `Create a NEW workflow titled exactly "${EVENT_TITLE}". Trigger: an event node on document_type kanban_card, operations ["created"], ` +
    `with match {"after.title": "${CARD_TITLE}"} so it only fires for that card. ` +
    'Unpack the event "after" document, then an extract node with an inline template that asks to rate the urgency of the card titled {title}, ' +
    'with an inline schema: an object with required string "priority" (enum low, medium, high) and required string "reason". ' +
    'Then an unpack of the extract result, then an agent node with inline template ' +
    '"Automated workflow test. Reply with exactly one line: EVENT_OK {priority}. Do not call any tools." wired from the unpacked priority. ' +
    'Do not edit any other workflow.');

  await expect.poll(() => workflowByTitle(EVENT_TITLE)?.id, { timeout: 300_000, intervals: [2000] }).toBeTruthy();
  const workflow = workflowByTitle(EVENT_TITLE);
  created.push(workflow.id);
  const kinds = Object.values(workflow.graph.nodes).map((node) => node.kind);
  expect(kinds, JSON.stringify(workflow.graph)).toEqual(expect.arrayContaining(['agent', 'event', 'extract']));

  await closeQuickChat(page);
  const response = await request.post('/api/ui/kanban/cards', { data: { title: CARD_TITLE } });
  expect(response.status()).toBeLessThan(300);

  // Only this test's card can trigger the workflow (match on after.title);
  // still select its run explicitly rather than the oldest run.
  const runForCard = () => runsOf(workflow.id).find((candidate) => candidate.trigger_event?.after?.title === CARD_TITLE);
  await expect.poll(() => runForCard()?.status ?? '',
    { timeout: 240_000, intervals: [3000] }).toMatch(/completed|failed/);
  const run = runForCard();
  expect(run.status, run.last_error || '').toBe('completed');
  expect(run.source).toBe('document');
  expect(workflow.graph.nodes[run.trigger_node_id]?.match).toEqual({ 'after.title': CARD_TITLE });
  const [extractId] = Object.entries(workflow.graph.nodes).find(([, node]) => node.kind === 'extract');
  const { priority } = run.node_outputs[extractId].result;
  expect(['low', 'medium', 'high']).toContain(priority);
  expect(run.last_output).toContain(`EVENT_OK ${priority}`);

  const cards = await runCards(page, EVENT_TITLE);
  await expect(cards.filter({ hasText: 'EVENT_OK' }).first()).toBeVisible({ timeout: 30_000 });
  expect(errors).toEqual([]);
});
