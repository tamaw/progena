import { execFileSync } from 'node:child_process';
import { test, expect } from '@playwright/test';

/**
 * A user's first message in a new chat creates its `session` document (Postgres,
 * from the checkpoint write); the `created` event triggers a workflow that asks
 * Progenitor for a short title and writes it back onto the session, which the
 * sidebar then shows.
 *
 * Runs against the live stack with a real LLM and reads run records from the
 * local Postgres (docs/LOCAL_STARTUP.md). Everything it creates is deleted.
 */

const RUN = Date.now().toString(36);
const WORKFLOW_ID = `workflow_pw${RUN}`;
const FIRST_MESSAGE = `Help me plan a three day hiking trip in the Grampians next month (${RUN})`;

test.setTimeout(300_000);

function sql(query) {
  return execFileSync('psql', ['-h', '127.0.0.1', '-p', '15433', '-U', 'progenitor', '-d', 'progenitor', '-qAtc', query], {
    env: { ...process.env, PGPASSWORD: process.env.PGPASSWORD || 'progenitor' },
    encoding: 'utf8',
  }).trim();
}

function runsOf(workflowId) {
  const rows = sql(`select value->'payload' from store where key like 'workflow_run:%' and value->'payload'->>'workflow_id' = '${workflowId}' order by value->'payload'->>'queued_at'`);
  return rows ? rows.split('\n').map((line) => JSON.parse(line)) : [];
}

function sessionWithFirstMessage(text) {
  const row = sql(`select value->'payload' from store where prefix = 'progenitor.state' and key like 'session:%' and value->'payload'->>'first_message' = '${text}'`);
  return row ? JSON.parse(row) : null;
}

function sessionEvents(threadId) {
  return sql(`select string_agg(op, ',' order by seq) from document_events where prefix = 'progenitor.state' and key = 'session:${threadId}'`);
}

function titleWorkflow() {
  const now = new Date().toISOString();
  return {
    id: WORKFLOW_ID,
    title: `pw-chat-title-${RUN}`,
    description: '',
    enabled: true,
    paused: false,
    version: 3,
    graph: {
      nodes: {
        started: { kind: 'event', title: 'Chat started', document_type: 'session', operations: ['created'], match: { 'after.source': 'ui' } },
        chat: { kind: 'unpack', title: 'Session' },
        name: {
          kind: 'extract',
          title: 'Name the chat',
          template: 'A user opened a new chat with this message:\n\n{first_message}\n\nGive the chat a short title of at most six words.',
          schema: { type: 'object', required: ['title'], properties: { title: { type: 'string' } } },
        },
        answer: { kind: 'unpack', title: 'Answer' },
        rename: { kind: 'document_write', title: 'Rename chat', document_type: 'session' },
      },
      edges: [
        { from: { node: 'started', port: 'after' }, to: { node: 'chat', port: 'value' } },
        { from: { node: 'chat', port: 'first_message' }, to: { node: 'name', port: 'first_message' } },
        { from: { node: 'name', port: 'result' }, to: { node: 'answer', port: 'value' } },
        { from: { node: 'chat', port: 'thread_id' }, to: { node: 'rename', port: 'id' } },
        { from: { node: 'answer', port: 'title' }, to: { node: 'rename', port: 'title' } },
      ],
    },
    ui: { nodes: {}, viewport: { x: 0, y: 0, zoom: 1 } },
    created_at: now,
    updated_at: now,
  };
}

test.afterEach(async ({ request }) => {
  await request.delete(`/api/ui/workflows/${WORKFLOW_ID}`);
});

test('the first message of a new chat triggers a workflow that renames the chat', async ({ page, request }) => {
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));

  const saved = await request.put('/api/ui/workflows/content', { data: { workflow: titleWorkflow() } });
  expect(saved.status(), await saved.text()).toBe(202);
  await expect.poll(() => sql(`select count(*) from store where key = 'workflow:${WORKFLOW_ID}'`)).toBe('1');

  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();
  const input = page.locator('textarea.chat-input');
  await input.click();
  await page.keyboard.type(FIRST_MESSAGE);
  await page.keyboard.press('Enter');
  await expect(page.getByText(FIRST_MESSAGE).first()).toBeVisible();

  // A second message in the same chat is not a new conversation.
  await expect(page.locator('textarea.chat-input')).toBeEnabled({ timeout: 120_000 });
  await expect.poll(() => runsOf(WORKFLOW_ID).map((run) => run.status).join(','),
    { timeout: 180_000, intervals: [2000] }).toMatch(/^(completed|failed)$/);
  await input.click();
  await page.keyboard.type('Thanks, keep it brief.');
  await page.keyboard.press('Enter');

  const [run] = runsOf(WORKFLOW_ID);
  expect(run.status, run.last_error || '').toBe('completed');
  expect(run.trigger_event.document_type).toBe('session');
  expect(run.trigger_event.after.source).toBe('ui');
  expect(run.trigger_event.after.first_message).toBe(FIRST_MESSAGE);

  const { title } = run.node_outputs.name.result;
  expect(title.trim().length).toBeGreaterThan(0);
  expect(title.trim().split(/\s+/).length).toBeLessThanOrEqual(8);
  expect(run.node_outputs.rename.found).toBe(true);
  test.info().annotations.push({ type: 'generated-title', description: title });

  // The title landed on the session document and the sidebar shows it live.
  const session = sessionWithFirstMessage(FIRST_MESSAGE);
  expect(page.url()).toContain(`/chat/${session.thread_id}`);
  expect(session.title).toBe(title);
  await expect(page.getByText(title, { exact: true }).first()).toBeVisible({ timeout: 15_000 });

  // One creation, one rename; turns rewriting `content` log nothing.
  await expect(page.locator('textarea.chat-input')).toBeEnabled({ timeout: 120_000 });
  await page.waitForTimeout(15_000);
  expect(sessionEvents(session.thread_id)).toBe('created,updated');
  expect(sessionWithFirstMessage(FIRST_MESSAGE).content).toContain('Thanks, keep it brief.');
  expect(runsOf(WORKFLOW_ID)).toHaveLength(1);

  await page.reload();
  await expect(page.getByText(title, { exact: true }).first()).toBeVisible({ timeout: 15_000 });
  expect(errors).toEqual([]);
});
