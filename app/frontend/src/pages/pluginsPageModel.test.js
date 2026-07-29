import { describe, expect, it } from 'vitest';
import { buildSkillTree, normalizeSkillSegment, formatSourceLabel } from './pluginsPageModel';

describe('buildSkillTree', () => {
  it('groups skills by agent and sorts groups alphabetically', () => {
    const skills = [
      { agent: 'acme-tools__formatter', name: 'lint-fix', title: 'Lint Fix', path: 'acme-tools__formatter/lint-fix/SKILL.md' },
      { agent: 'progenitor', name: 'checking-status', title: 'Checking Status', path: 'progenitor/checking-status/SKILL.md' },
      { agent: 'acme-tools__formatter', name: 'quality-review', title: 'Quality Review', path: 'acme-tools__formatter/quality-review/SKILL.md' },
    ];
    const tree = buildSkillTree(skills);
    expect(tree.map((g) => g.name)).toEqual(['acme-tools__formatter', 'progenitor']);
    expect(tree[0].skills.map((s) => s.name)).toEqual(['lint-fix', 'quality-review']);
    expect(tree[1].skills.map((s) => s.name)).toEqual(['checking-status']);
  });

  it('falls back to progenitor when agent is missing', () => {
    const skills = [{ name: 'foo', title: 'Foo', path: 'foo/SKILL.md' }];
    const tree = buildSkillTree(skills);
    expect(tree).toHaveLength(1);
    expect(tree[0].name).toBe('progenitor');
    expect(tree[0].skills[0].name).toBe('foo');
  });

  it('sorts skills within a group by title', () => {
    const skills = [
      { agent: 'progenitor', name: 'zebra', title: 'Zebra', path: 'progenitor/zebra/SKILL.md' },
      { agent: 'progenitor', name: 'alpha', title: 'Alpha', path: 'progenitor/alpha/SKILL.md' },
    ];
    const tree = buildSkillTree(skills);
    expect(tree[0].skills.map((s) => s.name)).toEqual(['alpha', 'zebra']);
  });
});

describe('normalizeSkillSegment', () => {
  it('lowercases, replaces invalid chars with hyphens, and trims', () => {
    expect(normalizeSkillSegment('  Hello World!!  ')).toBe('hello-world');
    expect(normalizeSkillSegment('foo_bar')).toBe('foo_bar');
    expect(normalizeSkillSegment('a-b_c')).toBe('a-b_c');
    expect(normalizeSkillSegment('---test---')).toBe('test');
  });
});

describe('formatSourceLabel', () => {
  it('splits marketplace__plugin into readable label', () => {
    expect(formatSourceLabel('acme-tools__formatter')).toBe('acme-tools / formatter');
  });

  it('leaves bare segments unchanged', () => {
    expect(formatSourceLabel('progenitor')).toBe('progenitor');
    expect(formatSourceLabel('builder_agent')).toBe('builder_agent');
    expect(formatSourceLabel('builder_progenitor')).toBe('builder_progenitor');
  });

  it('falls back to progenitor for falsy input', () => {
    expect(formatSourceLabel(null)).toBe('progenitor');
    expect(formatSourceLabel('')).toBe('progenitor');
  });
});
