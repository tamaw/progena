import { chromium } from '@playwright/test';

const baseUrl = process.env.FRONTEND_BASE_URL || 'http://127.0.0.1:5173';
const prompt = process.env.MEASURE_PROMPT || 'Say exactly READY and nothing else.';
const threadId = `measure-frontend-${crypto.randomUUID()}`;
const timeoutMs = Number(process.env.MEASURE_TIMEOUT_MS || 90000);

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(`${baseUrl.replace(/\/$/, '')}/chat/${threadId}/new-chat`, {
    waitUntil: 'load',
  });
  await page.locator('.chat-input-container').waitFor({ state: 'visible', timeout: 30000 });
  const input = page.locator('.chat-input');
  await input.waitFor({ state: 'visible', timeout: 30000 });
  await input.waitFor({ state: 'attached', timeout: 30000 });

  await page.evaluate(() => {
    window.__firstTokenMeasurement = { startedAt: performance.now() };
  });
  await input.fill(prompt);
  await input.press('Enter');

  await page.waitForFunction(
    () => [...document.querySelectorAll(
      '.chat-messages .message-wrapper.assistant .message-content',
    )].some((element) => Boolean(element.textContent?.trim())),
    undefined,
    { timeout: timeoutMs },
  );

  const measurement = await page.evaluate(() => {
    const startedAt = window.__firstTokenMeasurement?.startedAt ?? null;
    const assistantContent = [...document.querySelectorAll(
      '.chat-messages .message-wrapper.assistant .message-content',
    )].find((element) => Boolean(element.textContent?.trim()));
    const firstTextAt = performance.now();
    return {
      path: 'frontend_playwright_dom',
      thread_id: window.location.pathname.split('/')[2] || null,
      first_token_ms: startedAt === null ? null : Number((firstTextAt - startedAt).toFixed(3)),
      text_preview: assistantContent?.textContent?.trim().slice(0, 120) || '',
      url: window.location.href,
    };
  });
  console.log(JSON.stringify(measurement));
} finally {
  await browser.close();
}
