import { test, expect } from '@playwright/test';
import { removeRoutines, seedGraphFixtures } from '../helpers/routineFixture.mjs';

let ids = {};
test.beforeAll(() => { ids = seedGraphFixtures(['routine-branch-join']); });
test.afterAll(() => removeRoutines(Object.values(ids)));

test('redirects /sequences/:agentId to /routines/:agentId', async ({ page }) => {
  const id = ids['routine-branch-join'];
  await page.goto(`/sequences/${id}`);
  await expect(page).toHaveURL(new RegExp(`/routines/${id}$`));
});

test('redirects /sequences to /routines', async ({ page }) => {
  await page.goto('/sequences');
  await expect(page).toHaveURL(/\/routines/);
});
