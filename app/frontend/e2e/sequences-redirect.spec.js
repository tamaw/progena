import { test, expect } from '@playwright/test';

test('redirects /sequences/:agentId to /routines/:agentId', async ({ page }) => {
  await page.goto('/sequences/routine-branch-join');
  await expect(page).toHaveURL(/\/routines\/routine-branch-join/);
});

test('redirects /sequences to /routines', async ({ page }) => {
  await page.goto('/sequences');
  await expect(page).toHaveURL(/\/routines/);
});
