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
 * Covers nested decorative margins, hover-only expanding icon actions,
 * themed Markdown tables and answer choices, literal code, markdown
 * edit+preview, removal, AI/explain chat, revisions, and edit conflicts.
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

      // No section is preselected; its compact action rail appears on hover.
      await expect(page.getByRole('button', { name: 'Select <notes> section' })).toHaveAttribute('aria-pressed', 'false');
      await expect(page.getByRole('toolbar', { name: 'notes section actions' })).toBeHidden();
      await page.getByRole('button', { name: 'Select <notes> section' }).hover();
      await expect(page.getByRole('toolbar', { name: 'notes section actions' })).toBeVisible();
      const aiAction = page.getByRole('toolbar', { name: 'notes section actions' }).getByRole('button', { name: 'Edit with AI' });
      const compactWidth = await aiAction.evaluate((button) => button.getBoundingClientRect().width);
      expect(compactWidth).toBeLessThanOrEqual(30);
      await aiAction.hover();
      await expect.poll(() => aiAction.evaluate((button) => button.getBoundingClientRect().width)).toBeGreaterThan(compactWidth);
      await page.getByRole('button', { name: 'Select <inner> section' }).hover();
      await expect(page.getByRole('toolbar', { name: 'inner section actions' })).toBeVisible();
      await expect(page.getByRole('toolbar', { name: 'notes section actions' })).toBeHidden();
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

  test('renders mostly Markdown with a single editable section', async ({ page }) => {
    const title = 'Mixed Markdown';
    const id = uniqueInboxId('mixed');
    const body = [
      '# Mixed Markdown',
      '',
      'A **plain Markdown** introduction.',
      '',
      '- First check',
      '- Second check',
      '',
      '<decision>',
      'Release after **review**.',
      '</decision>',
      '',
      '## Follow-up',
      '',
      '| Item | Status |',
      '| --- | --- |',
      '| Review | Pending |',
      '',
      '`<plan>literal example</plan>` remains code.',
    ].join('\n');
    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);
      const content = page.locator('.inbox-detail__body');
      await expect(content.locator('h1')).toHaveText('Mixed Markdown');
      await expect(content.locator('strong').first()).toHaveText('plain Markdown');
      await expect(content.locator('li')).toHaveCount(2);
      await expect(content.getByRole('button', { name: 'Select <decision> section' })).toHaveAttribute('aria-pressed', 'false');
      await expect(content.locator('.inbox-section__content strong')).toHaveText('review');
      await expect(content.locator('h2')).toHaveText('Follow-up');
      await expect(content.locator('table td')).toContainText(['Review', 'Pending']);
      const tableStyle = await content.locator('table th').first().evaluate((cell) => ({
        border: getComputedStyle(cell).borderTopStyle,
        background: getComputedStyle(cell).backgroundColor,
      }));
      expect(tableStyle.border).toBe('solid');
      expect(tableStyle.background).not.toBe('rgba(0, 0, 0, 0)');
      await expect(content.locator('code')).toContainText('<plan>literal example</plan>');
      await expect(content.locator('.inbox-section')).toHaveCount(1);
    } finally {
      removeInboxMessage(id);
    }
  });

  test('section text stays in place as the action bar moves', async ({ page }) => {
    const title = 'Stable Selection';
    const id = uniqueInboxId('selection');
    seedInboxMessage({ id, title, body: '# Stable Selection\n\n<plan>\nFirst section.\n</plan>\n\n<notes>\nSecond section.\n</notes>' });
    try {
      await gotoInbox(page, title);
      await page.setViewportSize({ width: 480, height: 900 });
      await expect(page.locator('.inbox-section__content')).toHaveCount(2);
      const positions = () => page.locator('.inbox-section__content').evaluateAll((contents) =>
        contents.map((content) => content.getBoundingClientRect().top));
      const before = await positions();
      await page.getByRole('button', { name: 'Select <notes> section' }).click();
      expect(await positions()).toEqual(before);
      await page.getByRole('button', { name: 'Select <plan> section' }).click();
      expect(await positions()).toEqual(before);
    } finally {
      removeInboxMessage(id);
    }
  });

  test('section colors follow light and dark theme tokens', async ({ page }) => {
    const title = 'Theme Colors';
    const id = uniqueInboxId('theme');
    seedInboxMessage({ id, title, body: '# Theme Colors\n\n<plan>\nTheme-aware section.\n</plan>' });
    try {
      await gotoInbox(page, title);
      await page.getByRole('button', { name: 'Select <plan> section' }).click();
      const colors = async (theme) => page.evaluate((nextTheme) => {
        document.documentElement.dataset.theme = nextTheme;
        const probe = document.createElement('span');
        probe.style.color = 'var(--color-accent)';
        probe.style.backgroundColor = 'var(--color-bg-secondary)';
        document.body.append(probe);
        const accent = getComputedStyle(probe).color;
        const surface = getComputedStyle(probe).backgroundColor;
        probe.style.color = 'var(--color-text-primary)';
        const foreground = getComputedStyle(probe).color;
        probe.remove();
        const section = getComputedStyle(document.querySelector('.inbox-section.is-selected'));
        const ai = getComputedStyle(document.querySelector('.inbox-section__actions .msg-action-btn'));
        const remove = getComputedStyle(document.querySelector('.inbox-section__actions button[aria-label="Remove"]'));
        return {
          accent,
          sectionBorder: section.borderLeftColor,
          buttonBackground: remove.backgroundColor,
          surface,
          buttonText: remove.color,
          foreground,
          aiIcon: getComputedStyle(document.querySelector('.inbox-section__actions .msg-action-btn svg')).color,
          aiBackground: ai.backgroundColor,
        };
      }, theme);
      const dark = await colors('dark');
      const light = await colors('light');
      for (const palette of [dark, light]) {
        expect(palette.sectionBorder).toBe(palette.accent);
        expect(palette.aiIcon).toBe(palette.buttonText);
        expect(palette.buttonBackground).toBe('rgba(0, 0, 0, 0)');
        expect(palette.buttonText).not.toBe(palette.surface);
        expect(palette.aiBackground).toBe('rgba(0, 0, 0, 0)');
      }
      expect(light.sectionBorder).not.toBe(dark.sectionBorder);
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

      await expect(page.getByRole('button', { name: 'Select <plan> section' })).toHaveAttribute('aria-pressed', 'false');
      await page.getByRole('button', { name: 'Select <plan> section' }).hover();
      await expect(page.locator('.inbox-section__actions')).toBeVisible();

      // Edit toggles below the action rail and opens a full-width editor.
      const edit = page.getByRole('button', { name: 'Edit', exact: true });
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

      await expect(page.getByRole('button', { name: 'Select <plan> section' })).toHaveAttribute('aria-pressed', 'false');
      await page.getByRole('button', { name: 'Select <plan> section' }).hover();
      await expect(page.getByRole('toolbar', { name: 'plan section actions' })).toBeVisible();
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

      await page.getByRole('button', { name: 'Select <plan> section' }).hover();
      await page.getByRole('button', { name: 'Explain', exact: true }).click();

      // Quick chat panel opens and auto-submits the explain prompt.
      const panel = page.locator('.topnav-pane--quick.is-open');
      await expect(panel).toBeVisible({ timeout: 10_000 });
      await expect(panel.locator('.qchat-user')).toContainText('Explain this section');
    } finally {
      removeInboxMessage(id);
    }
  });

  test('inline AI edit sends to quick chat without opening the pane and waits for the inbox update', async ({ page }) => {
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
    await page.addInitScript(() => {
      const NativeEventSource = window.EventSource;
      window.EventSource = function captureUiStream(url, options) {
        const source = new NativeEventSource(url, options);
        if (String(url).includes('/api/ui/stream')) window.__inboxUiStream = source;
        return source;
      };
    });
    await page.route('**/api/ui/responses', async (route) => {
      const requestId = route.request().headers()['x-request-id'];
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ request_id: requestId, event_types: ['chat.response.started', 'chat.response.completed'] }),
      });
    });
    try {
      await gotoInbox(page, title);

      await page.getByRole('button', { name: 'Select <plan> section' }).hover();
      const actions = page.getByRole('toolbar', { name: 'plan section actions' });
      await expect(actions.locator('button').nth(1)).toHaveText('Explain');
      await expect(actions.locator('button').nth(1).locator('svg')).toBeVisible();
      await expect(actions.locator('button').first()).toHaveText('Edit with AI');
      await expect(actions.locator('button').first().locator('svg')).toBeVisible();
      await page.getByRole('button', { name: 'Edit with AI', exact: true }).click();

      const dialog = page.getByRole('dialog', { name: 'Edit section with AI' });
      const aiButton = page.getByRole('button', { name: 'Edit with AI', exact: true });
      await expect(dialog).toBeVisible();
      await expect(aiButton).toHaveAttribute('aria-expanded', 'true');
      const position = await page.evaluate(() => ({
        iconBottom: document.querySelector('.inbox-section__actions .is-active').getBoundingClientRect().bottom,
        popupTop: document.querySelector('.inbox-ai-popover').getBoundingClientRect().top,
      }));
      expect(position.popupTop).toBeGreaterThan(position.iconBottom);
      const aiInput = dialog.getByRole('textbox', { name: 'AI edit instruction' });
      await expect(dialog.getByRole('button', { name: 'Send AI edit' })).toBeDisabled();
      await aiInput.fill('Make it shorter');
      const responseRequest = page.waitForRequest((request) => request.url().includes('/api/ui/responses') && request.method() === 'POST', { timeout: 15_000 });
      await dialog.getByRole('button', { name: 'Send AI edit' }).click();
      await expect(dialog.getByRole('textbox', { name: 'AI edit status' })).toHaveValue('Working…');
      await expect(dialog.getByRole('button', { name: 'Send AI edit' })).toBeDisabled();
      await expect(page.locator('.topnav-pane--quick.is-open')).toHaveCount(0);
      const request = await responseRequest;
      const outgoing = JSON.parse(request.postData());
      expect(outgoing.input[0].content).toContain('Make it shorter');
      expect(outgoing.input[0].content).toContain(body.slice(body.indexOf('<plan>')));
      expect(outgoing.metadata.thread_id).toBeTruthy();

      // Complete through the UI event stream consumed by quick chat. A
      // response alone cannot dismiss the popover; the document must change.
      await page.evaluate((requestId) => {
        window.__inboxUiStream.dispatchEvent(new MessageEvent('ui.event', {
          data: JSON.stringify({
            id: crypto.randomUUID(),
            type: 'chat.response.completed',
            payload: { request_id: requestId },
          }),
        }));
      }, request.headers()['x-request-id']);
      await expect(dialog.getByRole('textbox', { name: 'AI edit status' })).toHaveValue('Updating…');
      updateInboxBody(id, `${body}\n\nUnrelated note.`);
      await expect(page.locator('.inbox-detail__body')).toContainText('Unrelated note.');
      await expect(dialog.getByRole('textbox', { name: 'AI edit status' })).toHaveValue('Updating…');
      updateInboxBody(id, body.replace('Old plan.', 'Short plan.'));
      await expect(page.locator('.inbox-section__content')).toContainText('Short plan.');
      await expect(dialog).toHaveCount(0);
      await expect(page.locator('.topnav-pane--quick.is-open')).toHaveCount(0);
    } finally {
      removeInboxMessage(id);
    }
  });

  test('Enter submits inline AI edit and a failed send can retry in the same chat session', async ({ page }) => {
    const title = 'Retry AI Edit';
    const id = uniqueInboxId('airetry');
    seedInboxMessage({ id, title, body: '# Retry AI Edit\n\n<plan>\nKeep this intact.\n</plan>' });
    const requests = [];
    await page.route('**/api/ui/responses', async (route) => {
      requests.push(JSON.parse(route.request().postData()));
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Agent temporarily unavailable"}' });
    });
    try {
      await gotoInbox(page, title);
      await page.getByRole('button', { name: 'Select <plan> section' }).hover();
      await page.getByRole('button', { name: 'Edit with AI', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: 'Edit section with AI' });
      await dialog.getByRole('textbox', { name: 'AI edit instruction' }).fill('Make this clearer');
      await dialog.getByRole('textbox', { name: 'AI edit instruction' }).press('Enter');
      await expect(dialog.getByRole('alert')).toContainText('Agent temporarily unavailable');
      await expect(page.locator('.topnav-pane--quick.is-open')).toHaveCount(0);
      await expect(dialog.getByRole('textbox', { name: 'AI edit instruction' })).toHaveValue('Make this clearer');
      await dialog.getByRole('button', { name: 'Send AI edit' }).click();
      await expect.poll(() => requests.length).toBe(2);
      await expect(dialog.getByRole('alert')).toContainText('Agent temporarily unavailable');
      expect(requests[0].input[0].content).toContain('Make this clearer');
      expect(requests[1].metadata.thread_id).toBe(requests[0].metadata.thread_id);
      await expect(page.locator('.inbox-section__content')).toContainText('Keep this intact.');
    } finally {
      removeInboxMessage(id);
    }
  });

  test('AI popover closes on outside click or Escape and can hand off an unsent draft', async ({ page }) => {
    const title = 'AI Popover Draft';
    const id = uniqueInboxId('aipop');
    seedInboxMessage({ id, title, body: '# AI Popover Draft\n\n<plan>\n<step>Review the draft.</step>\n</plan>' });
    try {
      await gotoInbox(page, title);
      await page.getByRole('button', { name: 'Select <step> section' }).hover();
      const aiButton = page.getByRole('toolbar', { name: 'step section actions' }).getByRole('button', { name: 'Edit with AI' });
      const dialog = page.getByRole('dialog', { name: 'Edit section with AI' });
      await aiButton.click();
      await expect(aiButton).toHaveClass(/is-active/);
      await expect(dialog).toBeVisible();
      await page.getByRole('heading', { name: title }).last().click();
      await expect(dialog).toHaveCount(0);
      await expect(page.locator('.inbox-section__actions button[aria-label="Edit with AI"]').last()).toHaveAttribute('aria-expanded', 'false');
      await page.getByRole('button', { name: 'Select <step> section' }).hover();
      await aiButton.click();
      await aiButton.click();
      await expect(dialog).toHaveCount(0);
      await expect(page.locator('.inbox-section__actions button[aria-label="Edit with AI"]').last()).not.toHaveClass(/is-active/);
      await page.getByRole('button', { name: 'Select <step> section' }).hover();
      await aiButton.click();
      await dialog.getByRole('textbox', { name: 'AI edit instruction' }).fill('Clarify this step');
      await page.keyboard.press('Escape');
      await expect(dialog).toHaveCount(0);
      await expect(page.locator('.inbox-section__actions button[aria-label="Edit with AI"]').last()).toHaveAttribute('aria-expanded', 'false');
      await page.getByRole('button', { name: 'Select <step> section' }).hover();
      await aiButton.click();
      await dialog.getByRole('textbox', { name: 'AI edit instruction' }).fill('Shorten this step');
      await dialog.getByRole('button', { name: 'Open in quick chat' }).click();
      await expect(dialog).toHaveCount(0);
      const panel = page.locator('.topnav-pane--quick.is-open');
      await expect(panel).toBeVisible({ timeout: 10_000 });
      const composer = panel.getByRole('textbox', { name: 'How should this message change?' });
      await expect(composer).toContainText('Shorten this step');
      await expect(composer).toContainText('<step>');
      await expect(panel.locator('.qchat-user')).toHaveCount(0);
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

      await expect(page.getByRole('button', { name: 'Select <questions> section' })).toHaveAttribute('aria-pressed', 'false');
      await expect(page.getByRole('button', { name: 'Show options' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Reject' })).toBeVisible();
      const answer = page.getByRole('button', { name: 'Reject' });
      const normalBackground = await answer.evaluate((button) => getComputedStyle(button).backgroundColor);
      await answer.hover();
      await expect.poll(() => answer.evaluate((button) => getComputedStyle(button).backgroundColor)).not.toBe(normalBackground);

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

      await expect(page.getByRole('button', { name: 'Select <questions> section' })).toHaveAttribute('aria-pressed', 'false');
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

      await page.getByRole('button', { name: 'Select <plan> section' }).hover();
      await page.getByRole('button', { name: 'Edit', exact: true }).click();
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
