import { test, expect } from '@playwright/test';

/**
 * PR #342 object-agent lifecycle. Approval/timeout have dedicated suites.
 * J13/J14 execute in nested-routines.spec.js; J15 delegation executes
 * in delegated-routines.spec.js rather than duplicate skip placeholders.
 */

// J11 runs in human-approval.spec.js; J12 in a2a-timeout.spec.js.

test.describe('PR342 journey 15: object agent registration and prompt edits', () => {
  test('creates a user object agent, edits its prompt, and confirms live profile updates', async ({ page }, testInfo) => {
    const run = `${Date.now().toString(36)}-${testInfo.workerIndex}`;
    const name = `pw342-${run}`;
    const slug = `object-${name}`;
    const firstPrompt = `pw342 initial prompt ${run}`;
    const editedPrompt = `pw342 edited prompt ${run}`;
    let createSubmitted = false;
    const pageErrors = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));

    async function closeQuickChatIfOpen() {
      const panel = page.locator('.topnav-pane--quick.is-open');
      if (await panel.count()) {
        await panel.getByRole('button', { name: 'Close' }).click();
        await expect(panel).toHaveCount(0, { timeout: 10_000 });
      }
      await expect(page.locator('.topnav-scrim.is-open')).toHaveCount(0, { timeout: 10_000 });
    }

    async function selectCreatedAgent() {
      await closeQuickChatIfOpen();
      const search = page.getByPlaceholder('Search agents...');
      await search.fill(name);
      const exactName = page.locator('.agents-agent-row__name').getByText(name, { exact: true });
      await expect(exactName).toHaveCount(1, { timeout: 30_000 });
      const row = exactName.locator('xpath=ancestor::button[contains(@class,"agents-agent-row")]');
      await expect(row).toHaveCount(1);
      await row.click();
      return row;
    }

    await page.goto('/agents');
    await expect(page.locator('.agents-pane')).toBeVisible();
    await expect(page.getByPlaceholder('Search agents...')).toBeVisible();
    await closeQuickChatIfOpen();
    await page.getByRole('button', { name: '+ New' }).click();
    await page.getByRole('button', { name: /Object agent/ }).click();
    await expect(page.locator('.agents-modal-backdrop')).toHaveCount(0, { timeout: 10_000 });

    await page.getByRole('textbox', { name: 'Agent name', exact: true }).fill(name);
    await page.getByPlaceholder('What this agent is for…').fill(`Disposable PR342 functional test agent ${run}`);
    await page.getByPlaceholder('You are…').fill(firstPrompt);
    await page.getByPlaceholder('shell, mcp, call_agent').fill('');
    await page.getByPlaceholder('/skills/review/').fill('');
    console.log(`PR342_TEST_RESOURCE id=${slug}`);
    await page.getByRole('button', { name: 'Create agent' }).click();
    createSubmitted = true;

    try {
      await selectCreatedAgent();
      await expect(page.locator('.agents-prompt')).toContainText(firstPrompt);

      await page.getByRole('button', { name: 'Edit manifest' }).click();
      const manifest = page.locator('.agents-json-field');
      await expect(manifest).toBeVisible();
      const raw = await manifest.inputValue();
      await manifest.fill(raw.replace(firstPrompt, editedPrompt));
      await page.getByRole('button', { name: 'Save', exact: true }).click();
      await expect(page.locator('.agents-prompt')).toContainText(editedPrompt, { timeout: 20_000 });

      // Refresh the profile from the persisted user document, then verify the
      // edited prompt is still what the registered profile exposes.
      await page.reload();
      await expect(page.locator('.agents-pane')).toBeVisible({ timeout: 30_000 });
      await selectCreatedAgent();
      await expect(page.locator('.agents-prompt')).toContainText(editedPrompt, { timeout: 20_000 });
    } finally {
      if (createSubmitted) {
        // Even if a later assertion fails, locate and remove only this test's
        // unique user object agent through the UI.
        await page.goto('/agents');
        await expect(page.locator('.agents-pane')).toBeVisible({ timeout: 30_000 });
        await closeQuickChatIfOpen();
        const search = page.getByPlaceholder('Search agents...');
        await search.fill(name);
        const exactName = page.locator('.agents-agent-row__name').getByText(name, { exact: true });
        if (await exactName.count()) {
          const row = exactName.locator('xpath=ancestor::button[contains(@class,"agents-agent-row")]');
          await expect(row).toHaveCount(1);
          await row.click();
          await expect(page.locator('.agents-prompt')).toContainText(firstPrompt, { timeout: 30_000 });
          await page.getByRole('button', { name: 'Delete agent' }).click();
          await page.getByRole('dialog').getByRole('button', { name: 'Delete agent', exact: true }).click();
          await expect(exactName).toHaveCount(0, { timeout: 30_000 });
        } else {
          // A submitted create request may have committed before the roster
          // snapshot settles. Try its known resource route and delete only if
          // the profile identifies the unique test agent.
          await page.goto(`/agents/${encodeURIComponent(slug)}/details`);
          const header = page.locator('.detail-header__title');
          if (await header.count() && (await header.first().textContent())?.trim() === name) {
            await page.getByRole('button', { name: 'Delete agent' }).click();
            await page.getByRole('dialog').getByRole('button', { name: 'Delete agent', exact: true }).click();
          }
        }
      }
    }
    expect(pageErrors, `Uncaught browser errors:\n${pageErrors.join('\n')}`).toEqual([]);
  });

});
