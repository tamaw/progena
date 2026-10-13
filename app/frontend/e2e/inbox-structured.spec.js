import { test, expect } from '@playwright/test';
import {
  inboxSmokeDatabaseUrl,
  seedInboxMessage,
  removeInboxMessage,
  updateInboxBody,
  uniqueInboxId,
} from './helpers/inboxFixture.mjs';

/**
 * Structured inbox browser regression.
 *
 * Covers nested brackets and collapse, literal code, default selection,
 * markdown edit+preview, remove, AI/explain quick chat, questions
 * option/free text, answer revisions, and optimistic-concurrency conflicts.
 *
 * Requires an isolated database configured via INBOX_SMOKE_DATABASE_URL.
 * Each test seeds uniquely-named rows and cleans them up in a finally block.
 *
 * Run with the isolated stack:
 *   INBOX_SMOKE_DATABASE_URL=postgresql://inbox_smoke@127.0.0.1:15843/inbox_smoke \
 *   FRONTEND_BASE_URL=http://127.0.0.1:5583 \
 *     npx playwright test e2e/inbox-structured.spec.js
 */

test.describe.configure({ mode: 'parallel' });

test.describe('structured inbox', () => {
  test.skip(!inboxSmokeDatabaseUrl, 'INBOX_SMOKE_DATABASE_URL must be set');

  const gotoInbox = async (page, title) => {
    await page.goto('/inbox');
    const card = page.locator('.chat-session-card').filter({ hasText: title });
    await expect(card).toBeVisible({ timeout: 15_000 });
    await card.click();
    await expect(page.locator('.inbox-detail')).toBeVisible();
  };

  test('renders nested brackets and literal code', async ({ page }) => {
    const title = 'Nested & Code';
    const id = uniqueInboxId('nested');
    const body = [
      '# Nested & Code',
      '',
      '<notes>',
      '<inner>',
      'Inner section with `inline code`.',
      '<deep>',
      '```python',
      'def deep():',
      '    return 42',
      '```',
      '</deep>',
      '</inner>',
      '</notes>',
      '<summary>',
      'Independent section.',
      '</summary>',
    ].join('\n');

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);

      // The first top-level tag is selected on open; nested tags remain independently selectable.
      await expect(page.getByRole('button', { name: 'Select <notes> section' })).toHaveAttribute('aria-pressed', 'true');
      await expect(page.getByRole('toolbar', { name: 'notes section actions' })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Select <notes> section' })).toHaveText('notes');
      await expect(page.getByRole('button', { name: 'Select <inner> section' })).toHaveText('inner');
      await expect(page.getByRole('button', { name: 'Select <deep> section' })).toBeVisible();
      await page.locator('.inbox-section__content').getByText('Inner section with inline code.').click();
      await expect(page.getByRole('button', { name: 'Select <inner> section' })).toHaveAttribute('aria-pressed', 'true');
      await expect(page.getByRole('button', { name: 'Select <notes> section' })).toHaveAttribute('aria-pressed', 'false');
      await expect(page.locator('.inbox-section__bracket')).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Collapse <deep> section' })).toHaveCount(0);
      await page.getByRole('button', { name: 'Select <summary> section' }).click();
      await expect(page.getByRole('button', { name: 'Select <summary> section' })).toHaveAttribute('aria-pressed', 'true');
      await expect(page.locator('.inbox-section__content pre code')).toContainText('def deep():');
      await expect(page.getByRole('button', { name: 'Select <inner> section' })).toBeVisible();
      const nested = page.getByRole('button', { name: 'Select <deep> section' }).locator('..').locator('..');
      const spacing = await nested.evaluate((section) => ({
        left: parseFloat(getComputedStyle(section).paddingLeft),
        top: parseFloat(getComputedStyle(section).marginTop),
      }));
      expect(spacing.left).toBeLessThanOrEqual(12);
      expect(spacing.top).toBeLessThanOrEqual(4);
      const inner = page.getByRole('button', { name: 'Select <inner> section' }).locator('..').locator('..');
      const beforeHover = await nested.evaluate((section) => getComputedStyle(section).borderLeftColor);
      const parentBeforeHover = await inner.evaluate((section) => getComputedStyle(section).borderLeftColor);
      await nested.locator('pre code').hover();
      await expect.poll(() => nested.evaluate((section) => getComputedStyle(section).borderLeftColor)).not.toBe(beforeHover);
      await expect.poll(() => inner.evaluate((section) => getComputedStyle(section).borderLeftColor)).toBe(parentBeforeHover);
      const summary = page.getByRole('button', { name: 'Select <summary> section' }).locator('..').locator('..');
      const bottomGap = await summary.evaluate((section) => section.getBoundingClientRect().bottom
        - section.querySelector(':scope > .inbox-section__content').getBoundingClientRect().bottom);
      expect(bottomGap).toBeLessThan(6);
    } finally {
      removeInboxMessage(id);
    }
  });

  test('markdown edit, formatting and preview', async ({ page }) => {
    const title = 'Edit Preview';
    const id = uniqueInboxId('edit');
    const body = [
      '# Edit Preview',
      '',
      '<plan>',
      'Build the feature.',
      '</plan>',
    ].join('\n');

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);

      // The first section is selected on open.
      await expect(page.getByRole('button', { name: 'Select <plan> section' })).toHaveAttribute('aria-pressed', 'true');
      await expect(page.locator('.inbox-section__actions')).toBeVisible();

      // Edit toggles below the persistent action bar and opens a full-width editor.
      const edit = page.getByRole('button', { name: '✎ Edit' });
      await expect(edit).toHaveAttribute('aria-pressed', 'false');
      await edit.click();
      await expect(edit).toHaveAttribute('aria-pressed', 'true');
      await expect(page.locator('.inbox-section__actions')).toBeVisible();
      await expect(page.locator('.inbox-section__editor')).toBeVisible();
      const textarea = page.getByRole('textbox', { name: 'Section Markdown' });
      await expect(textarea).toHaveValue(/\nBuild the feature\.\n/);
      const editorSize = await textarea.evaluate((el) => ({ height: el.getBoundingClientRect().height, width: el.getBoundingClientRect().width }));
      const sectionWidth = await page.locator('.inbox-section').first().evaluate((el) => el.getBoundingClientRect().width);
      expect(editorSize.height).toBeGreaterThanOrEqual(360);
      expect(editorSize.width).toBeGreaterThan(sectionWidth * 0.8);
      await edit.click();
      await expect(edit).toHaveAttribute('aria-pressed', 'false');
      await expect(page.locator('.inbox-section__editor')).toHaveCount(0);
      await edit.click();
      await expect(page.locator('.inbox-section__editor')).toBeVisible();

      // Append text, select it, and bold-format it.
      await textarea.fill('Build the feature. Then test it.');
      await textarea.evaluate((el) => {
        const start = el.value.indexOf('Then test it.');
        el.setSelectionRange(start, start + 'Then test it.'.length);
      });
      await page.getByRole('button', { name: 'Bold' }).click();

      // Preview should reflect the bold formatting.
      const preview = page.locator('.inbox-section__preview .markdown-content');
      await expect(preview.locator('strong')).toHaveText('Then test it.');

      // Save and verify the updated body.
      await page.getByRole('button', { name: 'Save' }).click();
      await expect(page.locator('.inbox-section__editor')).toHaveCount(0);
      await expect(page.locator('.inbox-section__content strong')).toHaveText('Then test it.');
    } finally {
      removeInboxMessage(id);
    }
  });

  test('remove section with confirm', async ({ page }) => {
    const title = 'Remove Section';
    const id = uniqueInboxId('remove');
    const body = [
      '# Remove Section',
      '',
      '<plan>',
      'Temporary plan.',
      '</plan>',
      '',
      '<notes>',
      'Keep me.',
      '</notes>',
    ].join('\n');

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);

      await expect(page.getByRole('button', { name: 'Select <plan> section' })).toHaveAttribute('aria-pressed', 'true');
      await expect(page.locator('.inbox-section__actions')).toBeVisible();
      await expect(page.getByRole('button', { name: 'Remove', exact: true }).locator('svg')).toBeVisible();

      page.on('dialog', (dialog) => dialog.accept());
      await page.getByRole('button', { name: 'Remove', exact: true }).click();

      // After removal the <plan> button should disappear; <notes> remains.
      await expect(page.getByRole('button', { name: 'Select <plan> section' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Select <notes> section' })).toBeVisible();
    } finally {
      removeInboxMessage(id);
    }
  });

  test('explain opens quick chat with pre-filled instruction', async ({ page }) => {
    const title = 'Explain Quick Chat';
    const id = uniqueInboxId('explain');
    const body = [
      '# Explain Quick Chat',
      '',
      '<plan>',
      'Do important work.',
      '</plan>',
    ].join('\n');

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);

      await expect(page.getByRole('button', { name: 'Select <plan> section' })).toHaveAttribute('aria-pressed', 'true');
      await page.getByRole('button', { name: 'Explain', exact: true }).click();

      // Quick chat panel opens and auto-submits the explain prompt.
      const panel = page.locator('.topnav-pane--quick.is-open');
      await expect(panel).toBeVisible({ timeout: 10_000 });
      await expect(panel.locator('.qchat-user')).toContainText('Explain this section');
    } finally {
      removeInboxMessage(id);
    }
  });

  test('edit with AI opens quick chat after typing instruction', async ({ page }) => {
    const title = 'Edit With AI';
    const id = uniqueInboxId('aiedit');
    const body = [
      '# Edit With AI',
      '',
      '<plan>',
      'Old plan.',
      '</plan>',
    ].join('\n');

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);

      await expect(page.getByRole('button', { name: 'Select <plan> section' })).toHaveAttribute('aria-pressed', 'true');
      const actions = page.getByRole('toolbar', { name: 'plan section actions' });
      await expect(actions.locator('button').nth(1)).toHaveText('Explain');
      await expect(actions.locator('button').nth(1).locator('svg')).toBeVisible();
      await expect(actions.locator('button').first()).toHaveText('Edit with AI');
      await expect(actions.locator('button').first().locator('svg')).toBeVisible();
      await page.getByRole('button', { name: 'Edit with AI', exact: true }).click();

      // In-place AI instruction form appears.
      const aiInput = page.getByRole('textbox', { name: 'AI edit instruction' });
      await expect(aiInput).toBeVisible();
      await aiInput.fill('Make it shorter');
      await page.getByRole('button', { name: 'Send' }).click();

      // Quick chat panel opens with the instruction.
      const panel = page.locator('.topnav-pane--quick.is-open');
      await expect(panel).toBeVisible({ timeout: 10_000 });
      await expect(panel.locator('.qchat-user')).toContainText('Make it shorter');
    } finally {
      removeInboxMessage(id);
    }
  });

  test('questions option click triggers quick chat', async ({ page }) => {
    const title = 'Questions Option';
    const id = uniqueInboxId('qopt');
    const body = [
      '# Questions Option',
      '',
      '<questions id="q1">',
      'What is your verdict?',
      '<options>',
      '<option>Approve</option>',
      '<option>Reject</option>',
      '</options>',
      '</questions>',
    ].join('\n');

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);

      await expect(page.getByRole('button', { name: 'Select <questions> section' })).toHaveAttribute('aria-pressed', 'true');
      await expect(page.getByRole('button', { name: 'Show options' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Reject' })).toBeVisible();

      await page.getByRole('button', { name: 'Reject' }).click();

      const panel = page.locator('.topnav-pane--quick.is-open');
      await expect(panel).toBeVisible({ timeout: 10_000 });
      await expect(panel.locator('.qchat-user')).toContainText('Reject');
    } finally {
      removeInboxMessage(id);
    }
  });

  test('questions free text answer triggers quick chat', async ({ page }) => {
    const title = 'Questions Free Text';
    const id = uniqueInboxId('qfree');
    const body = [
      '# Questions Free Text',
      '',
      '<questions>',
      'Any comments?',
      '<options>',
      '<option>None</option>',
      '</options>',
      '</questions>',
    ].join('\n');

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);

      await expect(page.getByRole('button', { name: 'Select <questions> section' })).toHaveAttribute('aria-pressed', 'true');
      const ownAnswer = page.getByRole('button', { name: 'Enter your own' });
      await expect(page.getByRole('button', { name: 'None' })).toBeVisible();
      await expect(page.getByRole('textbox', { name: 'Your own answer' })).toHaveCount(0);
      await ownAnswer.click();
      await expect(ownAnswer).toHaveAttribute('aria-expanded', 'true');
      await ownAnswer.click();
      await expect(ownAnswer).toHaveAttribute('aria-expanded', 'false');
      await expect(page.getByRole('textbox', { name: 'Your own answer' })).toHaveCount(0);
      await ownAnswer.click();

      const freeInput = page.getByRole('textbox', { name: 'Your own answer' });
      await freeInput.fill('Looks good to me');
      await page.getByRole('button', { name: 'Send answer' }).click();

      const panel = page.locator('.topnav-pane--quick.is-open');
      await expect(panel).toBeVisible({ timeout: 10_000 });
      await expect(panel.locator('.qchat-user')).toContainText('Looks good to me');
      await expect(ownAnswer).toHaveAttribute('aria-expanded', 'false');
      await expect(freeInput).toHaveCount(0);
    } finally {
      removeInboxMessage(id);
    }
  });

  test('rewritten question stays visible and can be answered again', async ({ page }) => {
    const title = 'Answer Revision';
    const id = uniqueInboxId('qrev');
    const body = [
      '# Answer Revision',
      '',
      '<questions id="rev1">',
      'The status is green: proceed with the current approach.',
      '<options>',
      '<option>Green</option>',
      '<option>Red</option>',
      '</options>',
      '</questions>',
    ].join('\n');

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);
      await expect(page.locator('.inbox-section__content')).toContainText('The status is green');
      await expect(page.getByRole('button', { name: 'Green' })).toBeVisible();
      await page.getByRole('button', { name: 'Red' }).click();

      const panel = page.locator('.topnav-pane--quick.is-open');
      await expect(panel).toBeVisible({ timeout: 10_000 });
      await expect(panel.locator('.qchat-user')).toContainText('Red');
      await expect(panel.locator('.qchat-user')).toContainText('rev1');

      // The agent's file write arrives through the ordinary inbox snapshot.
      updateInboxBody(id, body.replace('The status is green: proceed with the current approach.', 'The status is red: change course before proceeding.'));
      await expect(page.locator('.inbox-section__content')).toContainText('The status is red');
      await expect(page.getByRole('button', { name: 'Green' })).toBeVisible();
    } finally {
      removeInboxMessage(id);
    }
  });

  test('conflict error when body changes during edit', async ({ page }) => {
    const title = 'Conflict';
    const id = uniqueInboxId('conflict');
    const body = [
      '# Conflict',
      '',
      '<plan>',
      'Original plan.',
      '</plan>',
    ].join('\n');

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);

      await expect(page.getByRole('button', { name: 'Select <plan> section' })).toHaveAttribute('aria-pressed', 'true');
      await page.getByRole('button', { name: '✎ Edit' }).click();
      await expect(page.locator('.inbox-section__editor')).toBeVisible();

      // Simulate a concurrent modification in the database.
      const newBody = body.replace('Original plan.', 'Concurrently changed plan.');
      updateInboxBody(id, newBody);

      // Try to save; the PATCH should return 409 because expected_body no longer matches.
      await page.getByRole('button', { name: 'Save' }).click();

      await expect(page.locator('.inbox-section__error')).toContainText('changed');
    } finally {
      removeInboxMessage(id);
    }
  });
});
