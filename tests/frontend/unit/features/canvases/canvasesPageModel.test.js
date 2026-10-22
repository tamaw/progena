import { describe, expect, it } from 'vitest';
import {
  filterCanvases, canvasProblems, canvasState,
} from '@frontend/features/canvases/canvasesPageModel';

const base = { entry: 'index.mjs', files: ['index.mjs', 'manifest.json'], revision: 'r2', manifest_error: null };
const canvases = [
  { ...base, name: 'loaded', title: 'Task Graph', status: { state: 'loaded', revision: 'r2' } },
  { ...base, name: 'fresh', title: 'Fresh 3D', status: null },
  { ...base, name: 'crashed', title: 'Crashed', status: { state: 'error', revision: 'r2', errors: [{ message: 'boom' }] } },
  { ...base, name: 'fixed', title: 'Fixed', status: { state: 'error', revision: 'r1', errors: [{ message: 'old' }] } },
  { ...base, name: 'bad-manifest', title: 'Bad', manifest_error: 'manifest.json is missing.', status: null },
  { ...base, name: 'no-entry', title: 'No entry', files: ['manifest.json'], status: { state: 'loaded', revision: 'r2' } },
];

describe('canvasesPageModel', () => {
  it('is quietly available unless something is wrong, including when never opened', () => {
    expect(canvases.map(canvasState)).toEqual(['available', 'available', 'error', 'updated', 'error', 'error']);
  });

  it('explains errors from the manifest, the entry file and the current revision only', () => {
    expect(canvasProblems(canvases[2])).toEqual(['boom']);
    expect(canvasProblems(canvases[3])).toEqual([]);
    expect(canvasProblems(canvases[4])).toEqual(['manifest.json is missing.']);
    expect(canvasProblems(canvases[5])).toEqual(['Entry file index.mjs is missing.']);
  });

  it('groups Updated with errors and searches name, title and description', () => {
    expect(filterCanvases(canvases, { filter: 'Available' }).map((m) => m.name)).toEqual(['loaded', 'fresh']);
    expect(filterCanvases(canvases, { filter: 'Errors' }).map((m) => m.name))
      .toEqual(['crashed', 'fixed', 'bad-manifest', 'no-entry']);
    expect(filterCanvases(canvases, { query: '3d' }).map((m) => m.name)).toEqual(['fresh']);
    expect(filterCanvases(canvases, { filter: 'Errors', query: '3d' })).toEqual([]);
  });
});
