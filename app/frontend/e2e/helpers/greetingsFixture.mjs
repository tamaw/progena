import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { resolve } from 'node:path';

async function listen(server) {
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return server.address().port;
}

// Run the extracted greetings agent in its deterministic mode, isolated from
// the user's live :8109 instance. The transparent proxy counts actual retries;
// it never fabricates A2A responses or delays the runtime itself.
export async function startGreetingsFixture() {
  const agentsRoot = process.env.PROGENITOR_TEST_AGENTS_ROOT;
  if (!agentsRoot) {
    throw new Error('Set PROGENITOR_TEST_AGENTS_ROOT to an installed progentior.agents checkout');
  }
  const reservation = createServer();
  const port = await listen(reservation);
  await new Promise((resolve) => reservation.close(resolve));
  let logs = '';
  const child = spawn('bash', ['-c', 'source .venv/bin/activate && exec python -m uvicorn app.agents.greetings_agent.agent:app --host 127.0.0.1 --port "$PORT"'], {
    cwd: resolve(agentsRoot), env: { ...process.env, PORT: String(port), A2A_PUBLIC_HOST: '127.0.0.1', PROGENITOR_MOCK_LLM: 'true' }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.on('data', (data) => { logs += data; });
  child.stderr.on('data', (data) => { logs += data; });
  const requests = [];
  let proxyUrl;
  const proxy = createServer(async (request, response) => {
    try {
      const chunks = []; for await (const chunk of request) chunks.push(chunk);
      const body = Buffer.concat(chunks).toString();
      if (body) requests.push({ at: Date.now(), ...JSON.parse(body) });
      const upstream = await fetch(`http://127.0.0.1:${port}${request.url}`, {
        method: request.method, headers: { 'content-type': 'application/json' }, ...(body ? { body } : {}),
      });
      let text = await upstream.text();
      if (request.url.includes('agent-card.json')) { const card = JSON.parse(text); card.url = proxyUrl; text = JSON.stringify(card); }
      response.writeHead(upstream.status, { 'content-type': upstream.headers.get('content-type') || 'application/json' });
      response.end(text);
    } catch (error) { response.writeHead(502); response.end(JSON.stringify({ error: String(error) })); }
  });
  proxyUrl = `http://127.0.0.1:${await listen(proxy)}`;
  async function stop() {
    child.kill('SIGTERM');
    proxy.closeAllConnections();
    await new Promise((resolve) => proxy.close(resolve));
    await Promise.race([new Promise((resolve) => child.once('exit', resolve)), new Promise((resolve) => setTimeout(resolve, 3000))]);
    if (child.exitCode === null) child.kill('SIGKILL');
  }
  try {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      if (child.exitCode !== null) throw new Error(`Greetings agent exited: ${logs}`);
      try { if ((await fetch(`http://127.0.0.1:${port}/health`)).ok) return { url: proxyUrl, requests, stop, logs: () => logs }; } catch { /* starting */ }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    throw new Error(`Greetings agent did not become ready: ${logs}`);
  } catch (error) { await stop(); throw error; }
}
