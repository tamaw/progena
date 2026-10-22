import { describe, expect, it } from 'vitest';
import {
  buildModuleImportMap,
  buildModuleSrcdoc,
  javascriptDataUrl,
  normalizeModuleTheme,
  rewriteModuleFiles,
  unicodeBase64,
} from './moduleHostModel';

const libraries = { d3: async () => 'export const π = "π";' };

describe('moduleHostModel', () => {
  it('rewrites static and dynamic relative imports to virtual module URLs', async () => {
    const result = await rewriteModuleFiles('demo', {
      'index.mjs': "import './lib/a.mjs';",
      'lib/a.mjs': "export async function load(){ return import('../b.mjs'); }",
      'b.mjs': 'export default 2;',
    }, libraries);
    expect(result.errors).toEqual([]);
    expect(result.files['index.mjs']).toContain('https://module.invalid/demo/lib/a.mjs');
    expect(result.files['lib/a.mjs']).toContain('import("https://module.invalid/demo/b.mjs")');
  });

  it('rejects imports escaping the root and unknown bare specifiers', async () => {
    const result = await rewriteModuleFiles('demo', {
      'index.mjs': "import '../../escape.mjs'; import 'not-in-catalogue';",
    }, libraries);
    expect(result.errors.map((error) => error.message)).toEqual(expect.arrayContaining([
      'Import escapes module root: ../../escape.mjs',
      'Unknown bare module specifier: not-in-catalogue',
    ]));
  });

  it('creates unicode-safe JavaScript data URLs and maps library sources', async () => {
    expect(atob(unicodeBase64('héllo π'))).not.toBe('héllo π');
    expect(new TextDecoder().decode(Uint8Array.from(atob(unicodeBase64('héllo π')), (char) => char.charCodeAt(0)))).toBe('héllo π');
    expect(javascriptDataUrl('export default "π"')).toMatch(/^data:text\/javascript;base64,/);
    const map = await buildModuleImportMap('demo', { 'index.mjs': "import 'd3';" }, libraries);
    expect(map.imports.d3).toMatch(/^data:text\/javascript;base64,/);
    expect(map.imports['https://module.invalid/demo/index.mjs']).toMatch(/^data:text\/javascript;base64,/);
  });

  it('includes the required CSP and never relaxes the sandbox origin', async () => {
    const { srcdoc } = await buildModuleSrcdoc({
      name: 'demo', entry: 'index.mjs', files: { 'index.mjs': 'export default () => {}' }, librarySources: {}, theme: { mode: 'light', tokens: { '--accent': '#fff' } },
    });
    expect(srcdoc).toContain("default-src 'none'; script-src 'unsafe-inline' data:");
    expect(srcdoc).toContain('connect-src \'none\'');
    expect(srcdoc).toContain('--accent:#fff;');
    expect(srcdoc).toContain('<html data-theme="light" style="color-scheme:light">');
  });

  it('normalizes the host theme to a known mode and custom-property tokens only', () => {
    expect(normalizeModuleTheme({ mode: 'light', tokens: { '--bg': '#fff', color: 'red', '--x;}body{': '1' } }))
      .toEqual({ mode: 'light', tokens: { '--bg': '#fff' } });
    expect(normalizeModuleTheme({ mode: '"><script>', tokens: null })).toEqual({ mode: 'dark', tokens: {} });
    expect(normalizeModuleTheme(undefined)).toEqual({ mode: 'dark', tokens: {} });
  });
});
