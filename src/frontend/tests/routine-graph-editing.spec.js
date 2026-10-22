import { test, expect } from '@playwright/test';
import { removeRoutines, seedGraphFixtures } from './helpers/routineFixture.mjs';

/**
 * Routine graph editing — the fan-out scope.
 *
 * A fan-out is the one node kind that owns a scope, and the renderer infers
 * that scope's extent from the chain rather than from the node: the body is
 * whatever runs between the fan-out and the first noop. So inserting one has
 * to establish its boundary in the same edit, or the new box silently swallows
 * every step after it. Its Send target is likewise implicit: the compiler hands
 * `_build_send_router` the fan-out's single outgoing edge, so the edge is the
 * target, and the edge-splicing is the only thing keeping it honest when the
 * body's first step comes and goes.
 *
 * These drive the real `+` menu on a seeded copy of the routine-private-context
 * fixture (tests/fixtures/routine-graphs.json). The tests inspect edits in the
 * JSON editor without applying or persisting them.
 *
 * Requires the running stack (see playwright.config.js).
 */

// A plain linear chain, so anything the fan-out swallows is obvious.
let ids = {};
test.beforeAll(() => { ids = seedGraphFixtures(['routine-private-context']); });
test.afterAll(() => removeRoutines(Object.values(ids)));
const CHAIN_AFTER = ['Build private trace', 'Run shared subgraph', 'Return public answer'];

const canvas = (page) => page.locator('.routine-chain');
const status = (page) => page.locator('.agents-graph-validation-action');
const foreachBody = (page) => page.locator('.routine-foreach__body');

async function openRoutine(page) {
  await page.goto(`/routines/${ids['routine-private-context']}`);
  await expect(canvas(page)).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('.canvas-node').first()).toBeVisible({ timeout: 20_000 });
  await expect(status(page)).toHaveAttribute('aria-label', 'Graph is valid');
}

/** Open a connector's `+` menu and pick a node kind by its menu label. */
async function insert(connector, label) {
  await connector.locator('.routine-connector__btn').click();
  await connector.getByRole('menuitem', { name: label }).click();
}

/**
 * Node labels in chain order, so a swallowed step shows up as a reorder. Read
 * from the label element rather than the whole card, whose text also carries
 * the type icon and the `!` an incomplete step wears.
 */
const labels = (locator) => locator.locator('.canvas-node .routine-step-card__label').allTextContents();

/** The serialized document the backend would receive. */
async function serialized(page) {
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  const raw = await page.locator('.agents-graph-editor textarea').inputValue();
  const doc = JSON.parse(raw);
  await page.getByRole('button', { name: 'Collapse JSON editor' }).click();
  await expect(page.locator('.agents-graph-editor')).toHaveCount(0);
  return doc;
}

/**
 * Where a Send fan-out goes. The target is not a field on the node: it is the
 * fan-out's normal outgoing edge. Its separate foreach_empty edge preserves
 * the continuation when the collection is empty.
 */
function sendEdge(doc) {
  const fanout = doc.nodes.find((node) => node.kind === 'foreach');
  const outgoing = doc.edges.filter((edge) => edge.from === fanout.id && edge.level === 'normal');
  expect(outgoing).toHaveLength(1);
  return outgoing[0];
}

test.describe('routine graph editing', () => {
  test('adding a fan-out scopes itself instead of swallowing the rest of the chain', async ({ page }) => {
    await openRoutine(page);
    const before = await labels(canvas(page));
    expect(before.slice(1)).toEqual(CHAIN_AFTER);

    // The connector between the first and second step.
    await insert(canvas(page).locator('> .routine-connector').nth(1), 'Each');

    await expect(page.locator('.routine-foreach')).toHaveCount(1);
    // An empty scope: a box holding nothing but the `+` that fills it.
    await expect(foreachBody(page).locator('.canvas-node')).toHaveCount(0);
    await expect(foreachBody(page).locator('> .routine-connector')).toHaveCount(1);

    // Every step that followed is still outside the box, in order.
    const after = await labels(canvas(page));
    expect(after.slice(-CHAIN_AFTER.length)).toEqual(CHAIN_AFTER);
    expect(after).toContain('Join each');
    await expect(status(page)).toHaveAttribute('aria-label', 'Graph is valid');

    // The Send needs a real target even while the body is empty; the
    // terminator stands in until a step is added.
    const doc = await serialized(page);
    const fanout = doc.nodes.find((node) => node.kind === 'foreach');
    const stop = doc.nodes.find((node) => node.label === 'Join each');
    expect(sendEdge(doc)).toEqual({ from: fanout.id, to: stop.id, level: 'normal' });
    expect(doc.edges).toContainEqual({ from: fanout.id, to: stop.id, level: 'foreach_empty' });
  });

  test("the body's first step becomes the Send target, and giving it up restores the terminator", async ({ page }) => {
    await openRoutine(page);
    await insert(canvas(page).locator('> .routine-connector').nth(1), 'Each');
    await insert(foreachBody(page).locator('> .routine-connector').first(), 'Eval');

    // The step landed inside the scope, not after it.
    await expect(foreachBody(page).locator('.canvas-node')).toHaveCount(1);
    expect(await labels(canvas(page))).toEqual([
      'Inject context', 'Each', 'Eval', 'Join each', ...CHAIN_AFTER,
    ]);
    await expect(status(page)).toHaveAttribute('aria-label', 'Graph is valid');

    const withBody = await serialized(page);
    const bodyId = withBody.nodes.find((node) => node.label === 'Eval').id;
    expect(sendEdge(withBody).to).toBe(bodyId);

    // Removing it must hand the target back rather than leave it dangling.
    // Insertion already selects the draft Eval and opens its inspector.
    // Closing the draft inspector discards this incomplete node.
    await expect(page.getByRole('complementary', { name: 'Eval details' })).toBeVisible();
    await page.getByRole('button', { name: 'Delete step' }).click();
    await expect(foreachBody(page).locator('.canvas-node')).toHaveCount(0);

    const emptied = await serialized(page);
    const stopId = emptied.nodes.find((node) => node.label === 'Join each').id;
    expect(sendEdge(emptied).to).toBe(stopId);
    expect(emptied.edges).toContainEqual({ from: emptied.nodes.find((node) => node.kind === 'foreach').id, to: stopId, level: 'foreach_empty' });
    expect(emptied.nodes.some((node) => node.id === bodyId)).toBe(false);
  });
});
