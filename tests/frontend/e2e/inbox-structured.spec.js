import { test, expect } from '@playwright/test';
import {
  inboxSmokeDatabaseUrl,
  seedInboxMessage,
  removeInboxMessage,
  updateInboxBody,
  uniqueInboxId,
} from '../helpers/inboxFixture.mjs';

/**
 * Structured inbox browser regression.
 *
 * Covers Markdown heading sections (##–######), nested heading margins,
 * hover-only expanding icon actions, themed Markdown tables and answer
 * choices, literal code, AI-only editing and removal, AI/explain chat,
 * mixed Markdown with <questions> tags, heading selection, exact section
 * sends, revisions, and live refresh.
 *
 * Requires an isolated database configured via INBOX_SMOKE_DATABASE_URL.
 * Each test seeds uniquely-named rows and cleans them up in a finally block.
 *
 * Run with the isolated stack:
 *   INBOX_SMOKE_DATABASE_URL=postgresql://inbox_smoke@127.0.0.1:15843/inbox_smoke \
 *   FRONTEND_BASE_URL=http://127.0.0.1:5583 \
 *     npm run test:e2e -- inbox-structured.spec.js
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

  test('renders nested headings and literal code', async ({ page }) => {
    const title = 'Nested & Code';
    const id = uniqueInboxId('nested');
    const body = [
      '# Nested & Code',
      '',
      '## Notes',
      '',
      '### Inner',
      '',
      'Inner section with `inline code`.',
      '',
      '#### Deep',
      '',
      '```python',
      'def deep():',
      '    return 42',
      '```',
      '',
      '## Summary',
      '',
      'Independent section.',
    ].join('\n');

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);

      // No section is preselected; its compact action rail appears on hover.
      await expect(page.getByRole('button', { name: 'Select ## Notes section' })).toHaveAttribute('aria-pressed', 'false');
      await expect(page.getByRole('toolbar', { name: 'Notes section actions' })).toBeHidden();
      await page.getByRole('button', { name: 'Select ## Notes section' }).hover();
      await expect(page.getByRole('toolbar', { name: 'Notes section actions' })).toBeVisible();
      const aiAction = page.getByRole('toolbar', { name: 'Notes section actions' }).getByRole('button', { name: 'Edit with AI' });
      const compactWidth = await aiAction.evaluate((button) => button.getBoundingClientRect().width);
      expect(compactWidth).toBeLessThanOrEqual(30);
      await aiAction.hover();
      await expect.poll(() => aiAction.evaluate((button) => button.getBoundingClientRect().width)).toBeGreaterThan(compactWidth);
      await page.getByRole('button', { name: 'Select ### Inner section' }).hover();
      await expect(page.getByRole('toolbar', { name: 'Inner section actions' })).toBeVisible();
      await expect(page.getByRole('toolbar', { name: 'Notes section actions' })).toBeHidden();
      await expect(page.getByRole('heading', { name: 'Notes', level: 2 })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Inner', level: 3 })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Deep', level: 4 })).toBeVisible();
      const titleSize = await page.getByRole('heading', { name: 'Notes', level: 2 }).evaluate((heading) => parseFloat(getComputedStyle(heading).fontSize));
      const nestedSize = await page.getByRole('heading', { name: 'Inner', level: 3 }).evaluate((heading) => parseFloat(getComputedStyle(heading).fontSize));
      expect(titleSize).toBeGreaterThan(nestedSize);
      await expect(page.getByRole('button', { name: 'Select #### Deep section' })).toBeVisible();
      await page.locator('.inbox-section__content').getByText('Inner section with inline code.').click();
      await expect(page.getByRole('button', { name: 'Select ### Inner section' })).toHaveAttribute('aria-pressed', 'true');
      await expect(page.getByRole('button', { name: 'Select ## Notes section' })).toHaveAttribute('aria-pressed', 'false');
      await expect(page.locator('.inbox-section__bracket')).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Collapse #### Deep section' })).toHaveCount(0);
      await page.getByRole('button', { name: 'Select ## Summary section' }).click();
      await expect(page.getByRole('button', { name: 'Select ## Summary section' })).toHaveAttribute('aria-pressed', 'true');
      await expect(page.locator('.inbox-section__content pre code')).toContainText('def deep():');
      await expect(page.getByRole('button', { name: 'Select ### Inner section' })).toBeVisible();
      const nested = page.getByRole('button', { name: 'Select #### Deep section' }).locator('xpath=ancestor::section[contains(@class, "inbox-section")][1]');
      const nestedContent = nested.locator(':scope > .inbox-section__content');
      const spacing = await nested.evaluate((section) => ({
        left: parseFloat(getComputedStyle(section.querySelector(':scope > .inbox-section__content')).paddingLeft),
        top: parseFloat(getComputedStyle(section).marginTop),
        headingBottom: section.querySelector(':scope > .inbox-section__head').getBoundingClientRect().bottom,
        bracketTop: section.querySelector(':scope > .inbox-section__content').getBoundingClientRect().top,
      }));
      expect(spacing.left).toBeLessThanOrEqual(12);
      expect(spacing.top).toBeLessThanOrEqual(4);
      expect(spacing.bracketTop).toBeGreaterThanOrEqual(spacing.headingBottom);
      const inner = page.getByRole('button', { name: 'Select ### Inner section' }).locator('xpath=ancestor::section[contains(@class, "inbox-section")][1]');
      const innerContent = inner.locator(':scope > .inbox-section__content');
      const beforeHover = await nestedContent.evaluate((content) => getComputedStyle(content).borderLeftColor);
      const parentBeforeHover = await innerContent.evaluate((content) => getComputedStyle(content).borderLeftColor);
      await nested.locator('pre code').hover();
      await expect.poll(() => nestedContent.evaluate((content) => getComputedStyle(content).borderLeftColor)).not.toBe(beforeHover);
      await expect.poll(() => innerContent.evaluate((content) => getComputedStyle(content).borderLeftColor)).toBe(parentBeforeHover);
      const summary = page.getByRole('button', { name: 'Select ## Summary section' }).locator('xpath=ancestor::section[contains(@class, "inbox-section")][1]');
      const bottomGap = await summary.evaluate((section) => section.getBoundingClientRect().bottom
        - section.querySelector(':scope > .inbox-section__content').getBoundingClientRect().bottom);
      expect(bottomGap).toBeLessThan(6);
    } finally {
      removeInboxMessage(id);
    }
  });

  test('renders mostly Markdown with a single selectable section', async ({ page }) => {
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
      '## Decision',
      '',
      'Release after **review**.',
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
      await expect(content.getByRole('button', { name: 'Select ## Decision section' })).toHaveAttribute('aria-pressed', 'false');
      await expect(content.locator('.inbox-section__content strong')).toHaveText('review');
      await expect(content.getByRole('button', { name: 'Select ## Follow-up section' })).toBeVisible();
      await expect(content.locator('table td')).toContainText(['Review', 'Pending']);
      await expect.poll(() => content.locator('table th').first().evaluate((cell) => getComputedStyle(cell).borderTopStyle)).toBe('solid');
      const tableBackground = await content.locator('table th').first().evaluate((cell) => getComputedStyle(cell).backgroundColor);
      expect(tableBackground).not.toBe('rgba(0, 0, 0, 0)');
      await expect(content.locator('code')).toContainText('<plan>literal example</plan>');
      await expect(content.locator('.inbox-section')).toHaveCount(2);
    } finally {
      removeInboxMessage(id);
    }
  });

  test('section text stays in place as the action bar moves', async ({ page }) => {
    const title = 'Stable Selection';
    const id = uniqueInboxId('selection');
    seedInboxMessage({ id, title, body: '# Stable Selection\n\n## Plan\n\nFirst section.\n\n## Notes\n\nSecond section.' });
    try {
      await gotoInbox(page, title);
      await page.setViewportSize({ width: 480, height: 900 });
      await expect(page.locator('.inbox-section__content')).toHaveCount(2);
      const positions = () => page.locator('.inbox-section__content').evaluateAll((contents) =>
        contents.map((content) => content.getBoundingClientRect().top));
      const before = await positions();
      await page.getByRole('button', { name: 'Select ## Notes section' }).click();
      expect(await positions()).toEqual(before);
      await page.getByRole('button', { name: 'Select ## Plan section' }).click();
      expect(await positions()).toEqual(before);
    } finally {
      removeInboxMessage(id);
    }
  });

  test('section colors follow light and dark theme tokens', async ({ page }) => {
    const title = 'Theme Colors';
    const id = uniqueInboxId('theme');
    seedInboxMessage({ id, title, body: '# Theme Colors\n\n## Plan\n\nTheme-aware section.' });
    try {
      await gotoInbox(page, title);
      await page.getByRole('button', { name: 'Select ## Plan section' }).click();
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
        const section = getComputedStyle(document.querySelector('.inbox-section.is-selected > .inbox-section__content'));
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

  test('Remove instructs the agent with the selected section rather than deleting it directly', async ({ page }) => {
    const title = 'AI Remove Section';
    const id = uniqueInboxId('airemove');
    const body = [
      '# AI Remove Section',
      '',
      '## Plan',
      '',
      'Temporary plan.',
      '',
      '## Notes',
      '',
      'Keep me.',
    ].join('\n');

    await page.route('**/api/ui/responses', async (route) => {
      const requestId = route.request().headers()['x-request-id'];
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ request_id: requestId, event_types: ['chat.response.started'] }),
      });
    });
    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);

      await expect(page.getByRole('button', { name: 'Select ## Plan section' })).toHaveAttribute('aria-pressed', 'false');
      await page.getByRole('button', { name: 'Select ## Plan section' }).hover();
      await expect(page.getByRole('toolbar', { name: 'Plan section actions' })).toBeVisible();

      const removeButton = page.getByRole('button', { name: 'Remove', exact: true });
      await expect(removeButton.locator('svg')).toBeVisible();
      page.once('dialog', (dialog) => dialog.accept());
      const requestPromise = page.waitForRequest((request) => request.url().includes('/api/ui/responses') && request.method() === 'POST');
      await removeButton.click();
      const request = await requestPromise;
      const outgoing = JSON.parse(request.postData()).input[0].content;
      expect(outgoing).toContain(`/inbox/${id}.md`);
      expect(outgoing).toContain('## Plan\n\nTemporary plan.');
      expect(outgoing).toContain('Remove this entire section');
      await expect(page.getByRole('button', { name: 'Select ## Plan section' })).toBeVisible();
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
      '## Plan',
      '',
      'Do important work.',
    ].join('\n');

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);

      await page.getByRole('button', { name: 'Select ## Plan section' }).hover();
      await page.getByRole('button', { name: 'Explain', exact: true }).click();

      // Quick chat panel opens and auto-submits the explain prompt.
      const panel = page.locator('.topnav-pane--quick.is-open');
      await expect(panel).toBeVisible({ timeout: 10_000 });
      await expect(panel.locator('.qchat-user')).toContainText('Explain this section');
    } finally {
      removeInboxMessage(id);
    }
  });

  test('inline AI instruction completes independently of inbox document refresh', async ({ page }) => {
    const title = 'Edit With AI';
    const id = uniqueInboxId('aiedit');
    const body = [
      '# Edit With AI',
      '',
      '## Plan',
      '',
      'Old plan.',
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

      await page.getByRole('button', { name: 'Select ## Plan section' }).hover();
      const actions = page.getByRole('toolbar', { name: 'Plan section actions' });
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
      expect(outgoing.input[0].content).toContain('## Plan\n\nOld plan.');
      expect(outgoing.metadata.thread_id).toBeTruthy();

      // Complete through the UI event stream consumed by quick chat.
      // The popover closes on terminal completion; the document update refreshes independently.
      await page.evaluate((requestId) => {
        window.__inboxUiStream.dispatchEvent(new MessageEvent('ui.event', {
          data: JSON.stringify({
            id: crypto.randomUUID(),
            type: 'chat.response.completed',
            payload: { request_id: requestId },
          }),
        }));
      }, request.headers()['x-request-id']);
      await expect(dialog).toHaveCount(0);
      updateInboxBody(id, body.replace('Old plan.', 'Short plan.'));
      await expect(page.locator('.inbox-section__content')).toContainText('Short plan.');
      await expect(page.locator('.topnav-pane--quick.is-open')).toHaveCount(0);
    } finally {
      removeInboxMessage(id);
    }
  });

  test('AI instruction keeps showing current document when an external write arrives first', async ({ page }) => {
    const title = 'AI Edit Early Update';
    const id = uniqueInboxId('aiearly');
    const body = '# AI Edit Early Update\n\n## Plan\n\nOriginal plan.';
    seedInboxMessage({ id, title, body });
    await page.route('**/api/ui/responses', async (route) => {
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ request_id: route.request().headers()['x-request-id'], event_types: ['chat.response.completed'] }),
      });
    });
    try {
      await gotoInbox(page, title);
      await page.getByRole('button', { name: 'Select ## Plan section' }).hover();
      await page.getByRole('button', { name: 'Edit with AI', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: 'Edit section with AI' });
      await dialog.getByRole('textbox', { name: 'AI edit instruction' }).fill('Rewrite the plan');
      await page.evaluate(() => {
        window.addEventListener('progena:open-quick-chat', (event) => {
          if (event.detail?.inline) window.__inlineClientId = event.detail.clientId;
        }, { once: true });
      });
      const requestPromise = page.waitForRequest((request) => request.url().includes('/api/ui/responses') && request.method() === 'POST');
      await dialog.getByRole('button', { name: 'Send AI edit' }).click();
      await requestPromise;
      updateInboxBody(id, body.replace('Original plan.', 'Updated by the agent.'));
      await expect(page.locator('.inbox-section__content')).toContainText('Updated by the agent.');
      await expect(dialog.getByRole('textbox', { name: 'AI edit status' })).toHaveValue('Working…');
      await expect(page.getByText('This message changed while you were editing. Select the latest section to review it.')).toHaveCount(0);
      await page.evaluate(() => {
        window.dispatchEvent(new CustomEvent('progena:quick-chat-inline-status', {
          detail: { clientId: window.__inlineClientId, status: 'completed' },
        }));
      });
      await expect(dialog).toHaveCount(0);
    } finally {
      removeInboxMessage(id);
    }
  });

  test('Enter submits inline AI edit and a failed send can retry in the same chat session', async ({ page }) => {
    const title = 'Retry AI Edit';
    const id = uniqueInboxId('airetry');
    seedInboxMessage({ id, title, body: '# Retry AI Edit\n\n## Plan\n\nKeep this intact.' });
    const requests = [];
    await page.route('**/api/ui/responses', async (route) => {
      requests.push(JSON.parse(route.request().postData()));
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Agent temporarily unavailable"}' });
    });
    try {
      await gotoInbox(page, title);
      await page.getByRole('button', { name: 'Select ## Plan section' }).hover();
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
    seedInboxMessage({ id, title, body: '# AI Popover Draft\n\n## Plan\n\n### Step\n\nReview the draft.' });
    try {
      await gotoInbox(page, title);
      await page.getByRole('button', { name: 'Select ### Step section' }).hover();
      const aiButton = page.getByRole('toolbar', { name: 'Step section actions' }).getByRole('button', { name: 'Edit with AI' });
      const dialog = page.getByRole('dialog', { name: 'Edit section with AI' });
      await aiButton.click();
      await expect(aiButton).toHaveClass(/is-active/);
      await expect(dialog).toBeVisible();
      await page.getByRole('heading', { name: title }).last().click();
      await expect(dialog).toHaveCount(0);
      await expect(page.locator('.inbox-section__actions button[aria-label="Edit with AI"]').last()).toHaveAttribute('aria-expanded', 'false');
      await page.getByRole('button', { name: 'Select ### Step section' }).hover();
      await aiButton.click();
      await aiButton.click();
      await expect(dialog).toHaveCount(0);
      await expect(page.locator('.inbox-section__actions button[aria-label="Edit with AI"]').last()).not.toHaveClass(/is-active/);
      await page.getByRole('button', { name: 'Select ### Step section' }).hover();
      await aiButton.click();
      await dialog.getByRole('textbox', { name: 'AI edit instruction' }).fill('Clarify this step');
      await page.keyboard.press('Escape');
      await expect(dialog).toHaveCount(0);
      await expect(page.locator('.inbox-section__actions button[aria-label="Edit with AI"]').last()).toHaveAttribute('aria-expanded', 'false');
      await page.getByRole('button', { name: 'Select ### Step section' }).hover();
      await aiButton.click();
      await dialog.getByRole('textbox', { name: 'AI edit instruction' }).fill('Shorten this step');
      await dialog.getByRole('button', { name: 'Open in quick chat' }).click();
      await expect(dialog).toHaveCount(0);
      const panel = page.locator('.topnav-pane--quick.is-open');
      await expect(panel).toBeVisible({ timeout: 10_000 });
      const composer = panel.getByRole('textbox', { name: 'How should this message change?' });
      await expect(composer).toContainText('Shorten this step');
      await expect(composer).toContainText('### Step');
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
      const question = page.getByRole('button', { name: 'Select <questions> section' }).locator('xpath=ancestor::section[contains(@class, "inbox-section")][1]');
      const bracket = question.locator(':scope > .inbox-section__content');
      await expect(bracket.getByRole('button', { name: 'None' })).toBeVisible();
      await expect(bracket.getByRole('button', { name: 'Enter your own' })).toBeVisible();
      const grouping = await question.evaluate((section) => {
        const content = section.querySelector(':scope > .inbox-section__content');
        const questionText = content.querySelector('.markdown-content');
        const choices = content.querySelector('.inbox-section__options');
        return {
          beginsAboveQuestion: content.getBoundingClientRect().top <= questionText.getBoundingClientRect().top,
          endsBelowChoices: content.getBoundingClientRect().bottom >= choices.getBoundingClientRect().bottom,
          hasBracket: getComputedStyle(content).borderLeftStyle === 'solid',
        };
      });
      expect(grouping).toEqual({ beginsAboveQuestion: true, endsBelowChoices: true, hasBracket: true });
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
      await expect(page.locator('.inbox-section__content')).toContainText('The status is green');
      await expect(page.getByRole('button', { name: 'Green' })).toBeVisible();

      // Independent prompt submission: option click opens quick chat with the choice and question id.
      await page.getByRole('button', { name: 'Red' }).click();
      const panel = page.locator('.topnav-pane--quick.is-open');
      await expect(panel).toBeVisible({ timeout: 10_000 });
      await expect(panel.locator('.qchat-user')).toContainText('Red');
      await expect(panel.locator('.qchat-user')).toContainText('rev1');

      // External document update observation: a DB write refreshes the viewer live.
      updateInboxBody(id, body.replace('The status is green: proceed with the current approach.', 'The status is red: change course before proceeding.'));
      await expect(page.locator('.inbox-section__content')).toContainText('The status is red');
      await expect(page.getByRole('button', { name: 'Green' })).toBeVisible();
    } finally {
      removeInboxMessage(id);
    }
  });

  test('renders mixed Markdown with question tags among headings', async ({ page }) => {
    const title = 'Mixed Questions';
    const id = uniqueInboxId('mixedq');
    const body = [
      '# Mixed Questions',
      '',
      'Intro paragraph.',
      '',
      '## Approach',
      '',
      'Pick an approach.',
      '',
      '<questions id="approach">',
      'Which approach should we use?',
      '<options>',
      '<option>Conservative</option>',
      '<option>Aggressive</option>',
      '</options>',
      '</questions>',
      '',
      '## Details',
      '',
      'More info here.',
      '',
      '<questions id="details">',
      'Any concerns?',
      '<options>',
      '<option>None</option>',
      '</options>',
      '</questions>',
      '',
      '## Closing',
      '',
      'End of message.',
    ].join('\n');

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);
      const content = page.locator('.inbox-detail__body');

      // Headings are selectable sections.
      await expect(content.getByRole('button', { name: 'Select ## Approach section' })).toHaveAttribute('aria-pressed', 'false');
      await expect(content.getByRole('button', { name: 'Select ## Details section' })).toHaveAttribute('aria-pressed', 'false');
      await expect(content.getByRole('button', { name: 'Select ## Closing section' })).toHaveAttribute('aria-pressed', 'false');

      // Questions are also selectable sections.
      await expect(content.getByRole('button', { name: 'Select <questions> section' })).toHaveCount(2);

      // Options are visible.
      await expect(content.getByRole('button', { name: 'Conservative' })).toBeVisible();
      await expect(content.getByRole('button', { name: 'Aggressive' })).toBeVisible();
      await expect(content.getByRole('button', { name: 'None' })).toBeVisible();

      // Clicking a heading selects it, not the question inside.
      await content.getByRole('button', { name: 'Select ## Approach section' }).click();
      await expect(content.getByRole('button', { name: 'Select ## Approach section' })).toHaveAttribute('aria-pressed', 'true');

      // Clicking a question option opens quick chat.
      await content.getByRole('button', { name: 'Conservative' }).click();
      const panel = page.locator('.topnav-pane--quick.is-open');
      await expect(panel).toBeVisible({ timeout: 10_000 });
      await expect(panel.locator('.qchat-user')).toContainText('Conservative');
    } finally {
      removeInboxMessage(id);
    }
  });

  test('heading selection toggles and sends exact heading section', async ({ page }) => {
    const title = 'Heading Selection';
    const id = uniqueInboxId('hsel');
    const body = [
      '# Heading Selection',
      '',
      '## First',
      '',
      'First content.',
      '',
      '### Nested',
      '',
      'Nested content.',
      '',
      '## Second',
      '',
      'Second content.',
    ].join('\n');

    await page.route('**/api/ui/responses', async (route) => {
      const requestId = route.request().headers()['x-request-id'];
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ request_id: requestId, event_types: ['chat.response.started'] }),
      });
    });

    seedInboxMessage({ id, title, body });
    try {
      await gotoInbox(page, title);

      // No section preselected.
      await expect(page.getByRole('button', { name: 'Select ## First section' })).toHaveAttribute('aria-pressed', 'false');
      await expect(page.getByRole('button', { name: 'Select ### Nested section' })).toHaveAttribute('aria-pressed', 'false');
      await expect(page.getByRole('button', { name: 'Select ## Second section' })).toHaveAttribute('aria-pressed', 'false');

      // Clicking a heading selects it.
      await page.getByRole('button', { name: 'Select ## First section' }).click();
      await expect(page.getByRole('button', { name: 'Select ## First section' })).toHaveAttribute('aria-pressed', 'true');
      await expect(page.getByRole('button', { name: 'Select ### Nested section' })).toHaveAttribute('aria-pressed', 'false');

      // Clicking the same heading deselects it.
      await page.getByRole('button', { name: 'Select ## First section' }).click();
      await expect(page.getByRole('button', { name: 'Select ## First section' })).toHaveAttribute('aria-pressed', 'false');

      // Clicking nested heading selects only the nested section.
      await page.getByRole('button', { name: 'Select ### Nested section' }).click();
      await expect(page.getByRole('button', { name: 'Select ### Nested section' })).toHaveAttribute('aria-pressed', 'true');
      await expect(page.getByRole('button', { name: 'Select ## First section' })).toHaveAttribute('aria-pressed', 'false');

      // AI Edit sends the exact heading section.
      await page.getByRole('button', { name: 'Select ## Second section' }).hover();
      const requestPromise = page.waitForRequest((request) => request.url().includes('/api/ui/responses') && request.method() === 'POST');
      await page.getByRole('toolbar', { name: 'Second section actions' }).getByRole('button', { name: 'Explain' }).click();
      const request = await requestPromise;
      const outgoing = JSON.parse(request.postData()).input[0].content;
      expect(outgoing).toContain('## Second\n\nSecond content.');
      expect(outgoing).toContain('Explain this section');
    } finally {
      removeInboxMessage(id);
    }
  });

  test('busy quick chat reports an inline instruction failure instead of silently losing it', async ({ page }) => {
    const title = 'Lost Inline Race';
    const id = uniqueInboxId('race');
    const body = '# Lost Inline Race\n\n## Plan\n\nFirst plan.';
    seedInboxMessage({ id, title, body });
    await page.addInitScript(() => {
      window.__inlineStatuses = [];
      window.addEventListener('progena:quick-chat-inline-status', (event) => {
        window.__inlineStatuses.push(event.detail);
      });
    });

    let firstResolve;
    const firstDeferred = new Promise((resolve) => { firstResolve = resolve; });
    const requests = [];


    await page.route('**/api/ui/responses', async (route) => {
      const requestId = route.request().headers()['x-request-id'];
      requests.push(JSON.parse(route.request().postData()));
      if (requests.length === 1) {
        await firstDeferred;
      }
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ request_id: requestId, event_types: ['chat.response.completed'] }),
      });
    });

    try {
      await gotoInbox(page, title);

      // Explain starts a regular quick-chat send; hold its request open.
      await page.getByRole('button', { name: 'Select ## Plan section' }).hover();
      await page.getByRole('button', { name: 'Explain', exact: true }).click();
      await expect.poll(() => requests.length).toBe(1);

      // The modal pane covers the inbox, so dispatch the same event as AI Edit.
      // Another regular instruction must not overwrite or silence its failure.
      await page.evaluate(() => {
        window.dispatchEvent(new CustomEvent('progena:open-quick-chat', {
          detail: { prompt: 'Inline instruction', inline: true, clientId: 'race-inline' },
        }));
        window.dispatchEvent(new CustomEvent('progena:open-quick-chat', {
          detail: { prompt: 'Other regular instruction' },
        }));
      });
      await expect.poll(() => page.evaluate(() => window.__inlineStatuses)).toContainEqual(
        expect.objectContaining({ clientId: 'race-inline', status: 'failed' }),
      );
      expect(requests[0].input[0].content).toContain('Explain this section');
      await expect(page.getByRole('button', { name: 'Select ## Plan section' })).toBeVisible();
    } finally {
      firstResolve();
      removeInboxMessage(id);
    }
  });
});
