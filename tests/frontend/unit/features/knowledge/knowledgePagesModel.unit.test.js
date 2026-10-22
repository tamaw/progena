import { describe, expect, it } from 'vitest';
import { buildPageTree, resolvePageLink } from '@frontend/features/knowledge/knowledgePagesModel';

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

describe('resolvePageLink', () => {
  it('resolves relative page links against the current page', () => {
    expect(resolvePageLink('index.md', 'reviewed/leader-election.md')).toBe('reviewed/leader-election.md');
    expect(resolvePageLink('reviewed/leader-election.md', '../what-is-raft.md#terms')).toBe('what-is-raft.md');
    expect(resolvePageLink('reviewed/index.md', './')).toBe('reviewed/index.md');
    expect(resolvePageLink('index.md', 'reviewed/')).toBe('reviewed/index.md');
  });

  it('leaves external, absolute, anchor, non-page, and escaping links alone', () => {
    expect(resolvePageLink('index.md', 'https://raft.github.io/')).toBeNull();
    expect(resolvePageLink('index.md', '/knowledge/other/index.md')).toBeNull();
    expect(resolvePageLink('index.md', '#top')).toBeNull();
    expect(resolvePageLink('index.md', 'diagram.png')).toBeNull();
    expect(resolvePageLink('index.md', '../other-base/index.md')).toBeNull();
  });
});
