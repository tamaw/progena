import { describe, expect, it } from 'vitest';
import {
  filterModules, groupModuleRevisions, moduleFileMarkdown, moduleProblems, moduleState,
} from '@frontend/features/modules/modulesPageModel';

const base = { entry: 'index.mjs', files: ['index.mjs', 'manifest.json'], revision: 'r2', manifest_error: null };
const modules = [
  { ...base, name: 'loaded', title: 'Task Graph', status: { state: 'loaded', revision: 'r2' } },
  { ...base, name: 'fresh', title: 'Fresh 3D', status: null },
  { ...base, name: 'crashed', title: 'Crashed', status: { state: 'error', revision: 'r2', errors: [{ message: 'boom' }] } },
  { ...base, name: 'fixed', title: 'Fixed', status: { state: 'error', revision: 'r1', errors: [{ message: 'old' }] } },
  { ...base, name: 'bad-manifest', title: 'Bad', manifest_error: 'manifest.json is missing.', status: null },
  { ...base, name: 'no-entry', title: 'No entry', files: ['manifest.json'], status: { state: 'loaded', revision: 'r2' } },
];

describe('modulesPageModel', () => {
  it('is quietly available unless something is wrong, including when never opened', () => {
    expect(modules.map(moduleState)).toEqual(['available', 'available', 'error', 'updated', 'error', 'error']);
  });

  it('explains errors from the manifest, the entry file and the current revision only', () => {
    expect(moduleProblems(modules[2])).toEqual(['boom']);
    expect(moduleProblems(modules[3])).toEqual([]);
    expect(moduleProblems(modules[4])).toEqual(['manifest.json is missing.']);
    expect(moduleProblems(modules[5])).toEqual(['Entry file index.mjs is missing.']);
  });

  it('groups Updated with errors and searches name, title and description', () => {
    expect(filterModules(modules, { filter: 'Available' }).map((m) => m.name)).toEqual(['loaded', 'fresh']);
    expect(filterModules(modules, { filter: 'Errors' }).map((m) => m.name))
      .toEqual(['crashed', 'fixed', 'bad-manifest', 'no-entry']);
    expect(filterModules(modules, { query: '3d' }).map((m) => m.name)).toEqual(['fresh']);
    expect(filterModules(modules, { filter: 'Errors', query: '3d' })).toEqual([]);
  });

  it('fences code with a fence longer than any backtick run inside it', () => {
    const markdown = moduleFileMarkdown('index.mjs', 'const s = `a ``` b`;');
    expect(markdown.startsWith('````javascript\n')).toBe(true);
    expect(markdown.endsWith('\n````')).toBe(true);
    expect(moduleFileMarkdown('README.md', '# Hi')).toBe('# Hi');
  });

  it('groups bursts of edits into sessions that restore to their newest revision', () => {
    const at = (seconds) => new Date(Date.UTC(2026, 9, 20, 10, 0, seconds)).toISOString();
    const revisions = [ // newest first, as the backend sends them
      { id: 6, path: 'index.mjs', op: 'write', captured_at: at(400) },
      { id: 5, path: 'style.css', op: 'write', captured_at: at(390) },
      { id: 4, path: 'index.mjs', op: 'write', captured_at: at(380) },
      { id: 3, path: 'index.mjs', op: 'write', captured_at: at(200) },
      { id: 2, path: 'index.mjs', op: 'write', captured_at: at(10) },
      { id: 1, path: 'manifest.json', op: 'write', captured_at: at(0) },
    ];
    const sessions = groupModuleRevisions(revisions, 120_000);
    expect(sessions.map((s) => s.revisions.map((r) => r.id))).toEqual([[6, 5, 4], [3], [2, 1]]);
    expect(sessions[0]).toMatchObject({ capturedAt: at(400), startedAt: at(380), paths: ['index.mjs', 'style.css'] });
    expect(sessions[0].latestByPath['index.mjs'].id).toBe(6);
  });
});
