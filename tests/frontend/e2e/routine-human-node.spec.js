import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { seedRoutine, removeRoutine } from '../helpers/routineFixture.mjs';

const databaseUrl = process.env.PROGENA_DATABASE_URL
  || 'postgresql://progena:progena@127.0.0.1:15433/progena';

function queryRoutine(id) {
  const raw = execFileSync('psql', [databaseUrl, '-v', 'ON_ERROR_STOP=1', '-q', '-t', '-A'], {
    input: `SELECT value->>'payload' FROM store WHERE prefix='progena.state' AND key='routine:${id}';`,
  }).toString().trim();
  if (!raw) return null;
  return JSON.parse(raw);
}

async function openRoutine(page, id) {
  await page.goto(`/routines/${id}`);
  await expect(page.locator('.routine-chain')).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('.canvas-node').first()).toBeVisible({ timeout: 20_000 });
}

/** Open a connector's `+` menu and pick a node kind by its menu label. */
async function insert(connector, label) {
  await connector.locator('.routine-connector__btn').click();
  await connector.getByRole('menuitem', { name: label }).click();
}

test.describe('human node persistence', () => {
  let graph;
  test.afterEach(() => { if (graph) removeRoutine(graph.id); graph = null; });

  test('human node with blank allowed_responses persists', async ({ page }) => {
    graph = seedRoutine();
    await openRoutine(page, graph.id);

    // Insert a Human step after the first node
    await insert(page.locator('.routine-chain > .routine-connector').first(), 'Human');

    // Fill in the approval message
    await page.locator('.agents-graph-node__field input[placeholder*="should a person approve"], .agents-graph-node__field textarea[placeholder*="should a person approve"]').fill('Please approve this');

    // Leave allowed_responses blank (default)
    // Close inspector to trigger autosave
    await page.keyboard.press('Escape');

    // Wait for the save to complete
    await expect(page.getByRole('status').filter({ hasText: /^Saved$/ })).toBeVisible({ timeout: 30_000 });

    // Assert persistence against the DB document
    const doc = queryRoutine(graph.id);
    expect(doc).toBeTruthy();
    const humanNode = doc.nodes.find((n) => n.kind === 'human');
    expect(humanNode).toBeTruthy();
    expect(humanNode.config.message).toBe('Please approve this');
    expect(humanNode.config).not.toHaveProperty('allowed_responses');

    // Reload and verify the human node is still there
    await page.reload();
    await expect(page.locator('.routine-chain')).toBeVisible({ timeout: 20_000 });
    const labels = await page.locator('.canvas-node .routine-step-card__label').allTextContents();
    expect(labels).toContain('Human approval');
  });
});
