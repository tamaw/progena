import { describe, expect, it } from 'vitest';
import {
  artifactFromDocument,
  buildCanvasImportMap,
  buildCanvasSrcdoc,
  javascriptDataUrl,
  normalizeCanvasTheme,
  rewriteCanvasFiles,
  unicodeBase64,
} from '@frontend/features/canvases/canvasHostModel';

const libraries = { d3: async () => 'export const π = "π";' };

describe('canvasHostModel', () => {
  it('rewrites static and dynamic relative imports to virtual canvas URLs', async () => {
    const result = await rewriteCanvasFiles('demo', {
      'index.mjs': "import './lib/a.mjs';",
      'lib/a.mjs': "export async function load(){ return import('../b.mjs'); }",
      'b.mjs': 'export default 2;',
    }, libraries);
    expect(result.errors).toEqual([]);
    expect(result.files['index.mjs']).toContain('https://canvas.invalid/demo/lib/a.mjs');
    expect(result.files['lib/a.mjs']).toContain('import("https://canvas.invalid/demo/b.mjs")');
  });

  it('rejects imports escaping the root and unknown bare specifiers', async () => {
    const result = await rewriteCanvasFiles('demo', {
      'index.mjs': "import '../../escape.mjs'; import 'not-in-catalogue';",
    }, libraries);
    expect(result.errors.map((error) => error.message)).toEqual(expect.arrayContaining([
      'Import escapes canvas root: ../../escape.mjs',
      'Unknown bare module specifier: not-in-catalogue',
    ]));
  });

  it('creates unicode-safe JavaScript data URLs and maps library sources', async () => {
    expect(atob(unicodeBase64('héllo π'))).not.toBe('héllo π');
    expect(new TextDecoder().decode(Uint8Array.from(atob(unicodeBase64('héllo π')), (char) => char.charCodeAt(0)))).toBe('héllo π');
    expect(javascriptDataUrl('export default "π"')).toMatch(/^data:text\/javascript;base64,/);
    const map = await buildCanvasImportMap('demo', { 'index.mjs': "import 'd3';" }, libraries);
    expect(map.imports.d3).toMatch(/^data:text\/javascript;base64,/);
    expect(map.imports['https://canvas.invalid/demo/index.mjs']).toMatch(/^data:text\/javascript;base64,/);
  });

  it('includes the required CSP and never relaxes the sandbox origin', async () => {
    const { srcdoc } = await buildCanvasSrcdoc({
      name: 'demo', entry: 'index.mjs', files: { 'index.mjs': 'export default () => {}' }, librarySources: {}, theme: { mode: 'light', tokens: { '--accent': '#fff' } },
    });
    expect(srcdoc).toContain("default-src 'none'; script-src 'unsafe-inline' data:");
    expect(srcdoc).toContain('connect-src \'none\'');
    expect(srcdoc).toContain('--accent:#fff;');
    expect(srcdoc).toContain('<html data-theme="light" style="color-scheme:light">');
  });

  it('normalizes the host theme to a known mode and custom-property tokens only', () => {
    expect(normalizeCanvasTheme({ mode: 'light', tokens: { '--bg': '#fff', color: 'red', '--x;}body{': '1' } }))
      .toEqual({ mode: 'light', tokens: { '--bg': '#fff' } });
    expect(normalizeCanvasTheme({ mode: '"><script>', tokens: null })).toEqual({ mode: 'dark', tokens: {} });
    expect(normalizeCanvasTheme(undefined)).toEqual({ mode: 'dark', tokens: {} });
  });

  it('parses CSV artifacts with RFC 4180 quoting, CRLF, BOM and ragged rows', () => {
    const content = '\uFEFFname,note,qty\r\n"Smith, J","said ""hi""\nthen left",3\r\n\r\nLee,,\r\nShort\r\n';
    const artifact = artifactFromDocument({ id: 'people.csv', document: { content, updated_at: 't1' } });
    expect(artifact.data).toEqual([
      { name: 'Smith, J', note: 'said "hi"\nthen left', qty: '3' },
      { name: 'Lee', note: '', qty: '' },
      { name: 'Short', note: '', qty: '' },
    ]);
    expect(artifact).toMatchObject({ path: 'people.csv', updated_at: 't1' });
  });

  it('parses TSV, JSON and JSONL by extension and passes other text through', () => {
    const parse = (id, content) => artifactFromDocument({ id, document: { content } }).data;
    expect(parse('a.tsv', 'a\tb\n1\t2')).toEqual([{ a: '1', b: '2' }]);
    expect(parse('a.json', '{"n": [1, 2]}')).toEqual({ n: [1, 2] });
    expect(parse('a.jsonl', '{"n":1}\n\n{"n":2}\n')).toEqual([{ n: 1 }, { n: 2 }]);
    expect(parse('notes.md', '# Hi')).toBe('# Hi');
    expect(parse('empty.csv', '')).toEqual([]);
  });

  it('rejects artifacts whose content does not parse, naming the path', () => {
    expect(() => artifactFromDocument({ id: 'bad.json', document: { content: '{' } })).toThrow(/^Artifact bad\.json is not valid json/);
    expect(() => artifactFromDocument({ id: 'bad.csv', document: { content: 'a\n"open' } })).toThrow(/unterminated quoted field/);
    expect(() => artifactFromDocument({ id: 'bad.csv', document: { content: 'a\nfoo"bar' } })).toThrow(/unexpected quote inside an unquoted field/);
    expect(() => artifactFromDocument({ id: 'bad.csv', document: { content: 'a\n"foo"bar' } })).toThrow(/unexpected character after a closing quote/);
  });

  it('keeps a quoted empty record that a blank line would otherwise drop', () => {
    const parse = (content) => artifactFromDocument({ id: 'x.csv', document: { content } }).data;
    expect(parse('name\n""\n')).toEqual([{ name: '' }]);
    expect(parse('name\n""')).toEqual([{ name: '' }]);
    expect(parse('name\n\nAda\n\n')).toEqual([{ name: 'Ada' }]);
  });

  it('keeps the artifact parser self-contained so its source runs inside the iframe', () => {
    const embedded = new Function(`return (${artifactFromDocument.toString()});`)();
    expect(embedded({ id: 'x.csv', document: { content: 'a\n1' } }).data).toEqual([{ a: '1' }]);
  });
});
