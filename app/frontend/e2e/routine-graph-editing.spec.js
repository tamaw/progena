import { test, expect } from '@playwright/test';

/**
 * Routine graph editing — the fan-out scope.
 *
 * A fan-out is the one node kind that owns a scope, and the renderer infers
 * that scope's extent from the chain rather than from the node: the body is
 * whatever runs between the fan-out and the first noop. So inserting one has
 * to establish its boundary in the same edit, or the new box silently swallows
 * every step after it. Its Send target is likewise a node id kept in
 * `config.target`, not an edge, so nothing in the edge-splicing keeps it
 * honest when the body's first step changes.
 *
 * These drive the real `+` menu on a gallery routine. Gallery routines are not
 * persisted — `handleSaveGraph` returns early for them — so the edits are
 * local to the page and reset on reload.
 *
 * Requires the running stack (see playwright.config.js).
 */

// A plain linear chain, so anything the fan-out swallows is obvious.
const ROUTINE = '/routines/routine-private-context';
const CHAIN_AFTER = ['Build private trace', 'Run shared subgraph', 'Return public answer'];

const canvas = (page) => page.locator('.routine-chain');
const status = (page) => page.locator('.agents-graph-validation');
const foreachBody = (page) => page.locator('.routine-foreach__body');

async function openRoutine(page) {
  await page.goto(ROUTINE);
  await expect(canvas(page)).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('.canvas-node').first()).toBeVisible({ timeout: 20_000 });
  await expect(status(page)).toHaveText('Valid graph');
}

/** Open a connector's `+` menu and pick a node kind by its menu label. */
async function insert(connector, label) {
  await connector.locator('.routine-connector__btn').click();
  await connector.getByRole('menuitem', { name: label }).click();
}

/** Node labels in chain order, so a swallowed step shows up as a reorder. */
const labels = (locator) => locator.locator('.canvas-node').allTextContents()
  .then((all) => all.map((text) => text.replace(/^[^A-Za-z]+/, '').trim()));

/** The serialized document the backend would receive. */
async function serialized(page) {
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  const raw = await page.locator('.agents-graph-editor textarea').inputValue();
  const doc = JSON.parse(raw);
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
  await expect(page.locator('.agents-graph-editor')).toHaveCount(0);
  return doc;
}

test.describe('routine graph editing', () => {
  test('adding a fan-out scopes itself instead of swallowing the rest of the chain', async ({ page }) => {
    await openRoutine(page);
    const before = await labels(canvas(page));
    expect(before.slice(1)).toEqual(CHAIN_AFTER);

    // The connector between the first and second step.
    await insert(canvas(page).locator('> .routine-connector').nth(1), 'Send fan-out');

    await expect(page.locator('.routine-foreach')).toHaveCount(1);
    // An empty scope: a box holding nothing but the `+` that fills it.
    await expect(foreachBody(page).locator('.canvas-node')).toHaveCount(0);
    await expect(foreachBody(page).locator('> .routine-connector')).toHaveCount(1);

    // Every step that followed is still outside the box, in order.
    const after = await labels(canvas(page));
    expect(after.slice(-CHAIN_AFTER.length)).toEqual(CHAIN_AFTER);
    expect(after).toContain('Join fan-out');
    await expect(status(page)).toHaveText('Valid graph');

    // The Send needs a real target even while the body is empty; the
    // terminator stands in until a step is added.
    const doc = await serialized(page);
    const fanout = doc.nodes.find((node) => node.kind === 'foreach');
    const stop = doc.nodes.find((node) => node.label === 'Join fan-out');
    expect(fanout.config.target).toBe(stop.id);
    expect(doc.edges).toContainEqual({ from: fanout.id, to: stop.id });
  });

  test("the body's first step becomes the Send target, and giving it up restores the terminator", async ({ page }) => {
    await openRoutine(page);
    await insert(canvas(page).locator('> .routine-connector').nth(1), 'Send fan-out');
    await insert(foreachBody(page).locator('> .routine-connector').first(), 'Code');

    // The step landed inside the scope, not after it.
    await expect(foreachBody(page).locator('.canvas-node')).toHaveCount(1);
    expect(await labels(canvas(page))).toEqual([
      'Inject context', 'Foreach', 'Code', 'Join fan-out', ...CHAIN_AFTER,
    ]);
    await expect(status(page)).toHaveText('Valid graph');

    const withBody = await serialized(page);
    const fanoutId = withBody.nodes.find((node) => node.kind === 'foreach').id;
    const bodyId = withBody.nodes.find((node) => node.label === 'Code').id;
    expect(withBody.nodes.find((node) => node.id === fanoutId).config.target).toBe(bodyId);


    // Removing it must hand the target back rather than leave it dangling.
    await foreachBody(page).locator('.canvas-node').first().click();
    await page.getByRole('button', { name: 'Delete step' }).click();
    await expect(foreachBody(page).locator('.canvas-node')).toHaveCount(0);

    const emptied = await serialized(page);
    const stopId = emptied.nodes.find((node) => node.label === 'Join fan-out').id;
    expect(emptied.nodes.find((node) => node.id === fanoutId).config.target).toBe(stopId);
    expect(emptied.nodes.some((node) => node.id === bodyId)).toBe(false);
  });
});
