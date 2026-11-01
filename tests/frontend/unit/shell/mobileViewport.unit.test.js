import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

const read = (path) => readFileSync(new URL(`../../../../src/frontend/${path}`, import.meta.url), 'utf8');

it('lets the on-screen keyboard resize the layout viewport', () => {
  const meta = read('index.html').match(/<meta name="viewport" content="([^"]*)"/);
  const content = meta?.[1].split(',').map((part) => part.trim());
  expect(content).toContain('width=device-width');
  expect(content).toContain('interactive-widget=resizes-content');
});

it('sizes the app shell to the dynamic viewport with a vh fallback', () => {
  const rule = read('src/styles/chrome-frame.css').match(/^\.app-shell \{([^}]*)\}/m)?.[1] ?? '';
  const heights = [...rule.matchAll(/^\s*height:\s*([^;]+);/gm)].map((match) => match[1]);
  expect(heights).toEqual(['100vh', '100dvh']);
});
