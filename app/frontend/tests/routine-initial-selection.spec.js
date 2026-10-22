import { test, expect } from '@playwright/test';

const routineList = (page) => page.locator('.pane-card-row');
const graphNodes = (page) => page.locator('.routine-chain .canvas-node');

async function expectRoutineGraph(page) {
  await expect(page.locator('.routine-chain')).toBeVisible({ timeout: 20_000 });
  await expect(graphNodes(page).first()).toBeVisible({ timeout: 20_000 });
}

test.describe('routine initial selection', () => {
  test('loads the auto-selected graph after reload and after switching routines', async ({ page }) => {
    // The routine catalog is shared test data; this scenario only reads it.
    await page.goto('/routines');
    await expect(page).toHaveURL(/\/routines\/[^/]+$/);
    await expectRoutineGraph(page);
    const initiallySelectedUrl = page.url();

    // A cold page load can deliver the selected detail before a roster
    // snapshot whose routine entries intentionally have graph:null.
    await page.reload();
    await expect(page).toHaveURL(initiallySelectedUrl);
    await expectRoutineGraph(page);

    const expandList = page.getByRole('button', { name: 'Expand Routines list' });
    if (await expandList.isVisible()) await expandList.click();
    const selectedRow = routineList(page).and(page.locator('.active'));
    await expect(selectedRow).toBeVisible();
    const selectedName = await selectedRow.locator('.pane-card-row__title').innerText();
    const otherRows = page.locator('.pane-card-row:not(.active)');
    await expect(otherRows.first()).toBeVisible();

    await otherRows.first().click();
    await expect(page).not.toHaveURL(initiallySelectedUrl);
    await expectRoutineGraph(page);

    const expandAgain = page.getByRole('button', { name: 'Expand Routines list' });
    if (await expandAgain.isVisible()) await expandAgain.click();
    await routineList(page).filter({ has: page.getByText(selectedName, { exact: true }) }).click();
    await expect(page).toHaveURL(initiallySelectedUrl);
    await expectRoutineGraph(page);
  });
});
