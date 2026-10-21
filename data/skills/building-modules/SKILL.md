---
name: building-modules
description: Build a sandboxed interactive dashboard, diagram, visualisation, or small game for the Modules page.
allowed-tools: read_file write_file edit_file ls glob grep
---

Use this skill when the user asks for a dashboard, interactive diagram, game, or visualisation module.

## Create and update modules

1. Read `/modules/_catalogue.json` first. It is the live source of available libraries, document types, host API, and limits.
2. Choose a directory name matching `^[a-z][a-z0-9-]{1,47}$`; `_`-prefixed names are reserved.
3. Write `/modules/<name>/manifest.json` **before every other file**. The manifest `name` must equal `<name>`, `entry` must name an `.mjs` file, and `libraries` may only name catalogue library ids.
4. Write the entry and its assets. Module paths are relative POSIX paths with no `..`; valid file types are `.mjs`, `.css`, `.json`, `.svg`, `.md`, and `.txt`. Only `.mjs` files are importable.
5. Tell the user to open **Modules → <manifest title>**. Afterwards read `/modules/<name>/status.json`; fix reported load errors until it is loaded. Users delete modules in the UI; do not rely on agent deletion.

A module entry exports this exact shape:

```js
export default async function mount(root, host) {
  // Fill root completely and return cleanup when listeners/timers exist.
  return () => {};
}
```

Use `root` as the whole page. Do not hard-code app chrome sizing. Always remove event subscriptions, theme listeners, timers, animation frames, and WebGL resources in the cleanup function.

## Light and dark theme

Every module must look right in both themes and follow the user's switch live, without a reload:

- The app's design tokens are CSS custom properties on `:root`: `--color-bg-primary`, `--color-bg-secondary`, `--color-bg-tertiary`, `--color-bg-hover`, `--color-text-primary`, `--color-text-secondary`, `--color-text-muted`, `--color-border`, `--color-accent`, `--color-accent-soft`, `--color-success`, `--color-warning`, `--color-error`, `--color-info`, `--font-family`, `--font-family-mono`, `--border-radius-md`, `--spacing-md` (all of them are in `host.theme.tokens`). Use `var(--…)` for every DOM, CSS and SVG colour; these restyle automatically. Never hard-code background or text colours.
- `<html>` has `data-theme="light"` or `"dark"` and a matching `color-scheme`, so module CSS can add mode-specific rules with `[data-theme="dark"] .thing { … }` (e.g. stronger glow, different palette).
- Colours computed in JavaScript (canvas 2D, three.js materials/scene background/fog, d3 scales or interpolators) do not update by themselves. Read them with `host.theme.get('--color-accent')` (`host.theme.mode` is `'light'` or `'dark'`), and re-apply them in `host.theme.onChange(() => …)`, which returns an unsubscribe function. With three.js, update `scene.background`, material colours and lights, then render again.
- Prefer palettes derived from the tokens. When a design needs extra colours, pick separate light and dark values keyed on `host.theme.mode` with enough contrast in both.
- Lighting in 3D: keep lights white/neutral (`0xffffff`) and change only their intensity per mode. Never derive light colours from text or accent tokens: that tints the whole scene. Surfaces such as the ground take `--color-bg-secondary`/`--color-bg-tertiary`, objects take `--color-accent` (or a token palette), and grid lines or edges take `--color-border`.

```js
function applyTheme() {
  const c = (name) => new THREE.Color(host.theme.get(name));
  const dark = host.theme.mode === 'dark';
  scene.background = c('--color-bg-primary');
  ground.material.color = c('--color-bg-secondary');
  for (const bar of bars) bar.material.color = c('--color-accent');
  ambient.intensity = dark ? 0.6 : 1.1;
  sun.intensity = dark ? 1.2 : 1.6;
  renderer.render(scene, camera);
}
applyTheme();
const offTheme = host.theme.onChange(applyTheme); // call offTheme() in cleanup
```

## Sandbox and imports

The iframe has no network, cookies, localStorage, or parent-window access. Do not use `fetch`, WebSockets, `localStorage`, `document.cookie`, or `parent`. WebGL is allowed.

Import only bare specifiers listed in the catalogue and relative `./x.mjs` paths. Read every non-module file with `host.asset(path)` instead of importing it.

`host.documents.list(type, {limit, cursor})` resolves `{items: [{key, id, document}], cursor}` (pass `cursor` back for the next page; `null` when done) and `host.documents.get(type, id)` resolves one `{key, id, document}` or `null`. `host.documentTypes()` lists types (ids, labels, key prefixes, schemas; also in the catalogue). Subscribe with `host.events.subscribe(['kanban_card.*'], callback)` (patterns `<type|*>.<created|updated|deleted|*>`); it returns an unsubscribe function, and callbacks receive `{event_id, document_type, key, id, op, timestamp, changed_fields, document}` (`document` is `null` on delete). Store module-owned JSON values with `host.data.get(key)`, `host.data.set(key, value)`, `host.data.delete(key)` and `host.data.list()`; this is the module's only writable storage. `host.asset(path)` returns a non-`.mjs` file's text.

Respect the catalogue limits: at most 32 files, 256 KiB per file, and 1 MiB per module.

## Complete compact example

`/modules/card-status/manifest.json`:
```json
{"name":"card-status","title":"Cards by status","description":"Live kanban card counts.","entry":"index.mjs","libraries":["d3"]}
```

`/modules/card-status/index.mjs`:
```js
import * as d3 from 'd3';

export default async function mount(root, host) {
  root.style.cssText = 'width:100%;height:100%;margin:0;background:var(--color-bg-primary);color:var(--color-text-primary);font-family:var(--font-family)';
  const svg = d3.select(root).append('svg').attr('width', '100%').attr('height', '100%');
  async function render() {
    const {items} = await host.documents.list('kanban_card', {limit: 500});
    const counts = d3.rollups(items, (v) => v.length, (x) => x.document.column_id || 'unassigned');
    const {width, height} = root.getBoundingClientRect();
    const x = d3.scaleBand(counts.map(([c]) => c), [40, width - 20]).padding(0.2);
    const y = d3.scaleLinear([0, d3.max(counts, ([, n]) => n) || 1], [height - 30, 20]);
    svg.selectAll('*').remove();
    svg.append('g').attr('transform', `translate(0,${height - 30})`).call(d3.axisBottom(x));
    svg.append('g').attr('transform', 'translate(40,0)').call(d3.axisLeft(y).ticks(5));
    svg.selectAll('rect').data(counts).join('rect')
      .attr('x', ([c]) => x(c)).attr('width', x.bandwidth())
      .attr('y', ([, n]) => y(n)).attr('height', ([, n]) => y(0) - y(n))
      .attr('fill', 'var(--color-accent)');
  }
  await render();
  const off = host.events.subscribe(['kanban_card.*'], render);
  return () => off();
}
```
