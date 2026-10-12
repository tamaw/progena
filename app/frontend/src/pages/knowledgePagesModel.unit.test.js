import { describe, expect, it } from 'vitest';
import { buildPageTree } from './knowledgePagesModel';

describe('buildPageTree', () => {
  it('groups directories before pages and sorts titles', () => {
    const tree = buildPageTree([
      { path: 'z.md', title: 'Z', kind: 'concept' },
      { path: 'notes/b.md', title: 'B', kind: 'concept' },
      { path: 'a.md', title: 'A', kind: 'concept' },
    ]);
    expect(tree.directories.map((node) => node.name)).toEqual(['notes']);
    expect(tree.pages.map((page) => page.title)).toEqual(['Index', 'A', 'Z']);
    expect(tree.directories[0].pages[0].path).toBe('notes/index.md');
    expect(tree.directories[0].pages[1].path).toBe('notes/b.md');
  });

  it('places reserved files before concepts and adds synthetic index', () => {
    const tree = buildPageTree([
      { path: 'log.md', kind: 'log' },
      { path: 'concepts/x.md', title: 'X', kind: 'concept' },
    ]);
    expect(tree.pages.map((p) => p.name)).toEqual(['index.md', 'log.md']);
    expect(tree.directories[0].pages.map((p) => p.name)).toEqual(['index.md', 'x.md']);
  });
});
