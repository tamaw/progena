import { randomUUID } from 'node:crypto';

/**
 * A stream client for specs that call finite `/api/ui/*` routes directly.
 *
 * Those routes answer on the requester's own `/api/ui/stream`, so they need a
 * live `client_id`. This opens one stream in the test process (independent of
 * any page) and returns it with `url()` to add the id to a request path.
 * Replies are discarded; specs observe results through the page or the database.
 */
export async function openApiClient(baseURL = process.env.FRONTEND_BASE_URL || 'http://127.0.0.1:5173') {
  const clientId = randomUUID();
  const controller = new AbortController();
  const response = await fetch(`${baseURL}/api/ui/stream?client_id=${clientId}`, {
    headers: { accept: 'text/event-stream' },
    signal: controller.signal,
  });
  if (!response.ok || !response.body) throw new Error(`UI stream refused the spec client: ${response.status}`);
  const reader = response.body.getReader();
  // The first frame is `connection.ready`: the client is registered once it arrives.
  await reader.read();
  (async () => {
    try {
      while (!(await reader.read()).done);
    } catch {
      // Closed by close().
    }
  })();
  return {
    clientId,
    url: (path) => `${path}${path.includes('?') ? '&' : '?'}client_id=${clientId}`,
    close: () => controller.abort(),
  };
}
