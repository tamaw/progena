const baseUrl = process.env.FRONTEND_BASE_URL || 'http://127.0.0.1:5173';
const routes = [
  '/',
  '/dashboard',
  '/agents',
  '/tools',
  '/repositories',
  '/knowledge',
  '/schedules',
  '/skills',
  '/sessions',
  '/tasks',
  '/projects',
  '/kanban',
  '/solutions/main/projects/backend-runtime',
];

const failures = [];

for (const route of routes) {
  const url = new URL(route, baseUrl);
  try {
    const response = await fetch(url);
    const text = await response.text();
    if (!response.ok || !text.includes('<div id="root">')) {
      failures.push(`${route} returned ${response.status}`);
    }
  } catch (error) {
    failures.push(`${route} failed: ${error.message}`);
  }
}

if (failures.length) {
  console.error(failures.join('\n'));
  process.exit(1);
}

console.log(`Smoke checked ${routes.length} routes at ${baseUrl}`);
