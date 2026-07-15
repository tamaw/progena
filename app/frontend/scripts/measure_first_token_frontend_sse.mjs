import { chromium } from '@playwright/test';

const baseUrl = process.env.FRONTEND_BASE_URL || 'http://127.0.0.1:5173';
const prompt = process.env.MEASURE_PROMPT || 'Say exactly READY and nothing else.';
const timeoutMs = Number(process.env.MEASURE_TIMEOUT_MS || 90000);

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(`${baseUrl.replace(/\/$/, '')}/chat`, { waitUntil: 'domcontentloaded' });
  const measurement = await page.evaluate(async ({ prompt, timeoutMs }) => {
    const streamUrl = '/api/ui/stream';
    const responseUrl = '/api/ui/responses';
    const requestId = crypto.randomUUID();
    const threadId = `measure-frontend-sse-${crypto.randomUUID()}`;
    const stream = new EventSource(streamUrl);
    const startedAt = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('SSE connection.ready timeout')), timeoutMs);
      stream.addEventListener('ui.event', (event) => {
        let data;
        try {
          data = JSON.parse(event.data);
        } catch {
          return;
        }
        if (data?.type === 'connection.ready') {
          clearTimeout(timeout);
          resolve(performance.now());
        }
      });
      stream.onerror = () => {
        clearTimeout(timeout);
        reject(new Error('SSE connection failed before connection.ready'));
      };
    });

    const firstDelta = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('first delta timeout')), timeoutMs);
      stream.addEventListener('ui.event', (event) => {
        let data;
        try {
          data = JSON.parse(event.data);
        } catch {
          return;
        }
        if (data?.type !== 'chat.response.delta') return;
        const payload = data.payload;
        if (payload?.request_id !== requestId || typeof payload.delta !== 'string' || !payload.delta) {
          return;
        }
        clearTimeout(timeout);
        resolve({ at: performance.now(), delta: payload.delta });
      });
    });

    const requestStartedAt = performance.now();
    const acknowledgement = await fetch(responseUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Request-ID': requestId,
      },
      body: JSON.stringify({
        model: 'progenitor-v1',
        input: [{ role: 'user', content: prompt }],
        stream: true,
        metadata: { thread_id: threadId },
      }),
    });
    if (!acknowledgement.ok) {
      stream.close();
      throw new Error(`backend acknowledgement failed: HTTP ${acknowledgement.status}`);
    }
    const delta = await firstDelta;
    stream.close();
    return {
      path: 'frontend_playwright_sse',
      request_id: requestId,
      thread_id: threadId,
      ready_to_request_ms: Number((requestStartedAt - startedAt).toFixed(3)),
      first_token_ms: Number((delta.at - requestStartedAt).toFixed(3)),
      delta_preview: delta.delta.slice(0, 120),
    };
  }, { prompt, timeoutMs });
  console.log(JSON.stringify(measurement));
} finally {
  await browser.close();
}
