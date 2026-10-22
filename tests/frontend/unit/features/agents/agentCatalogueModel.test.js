import { describe, expect, it } from 'vitest';
import { catalogueAgents, catalogueRoutines } from '@frontend/features/agents/agentCatalogueModel';

describe('catalogueAgents', () => {
  const agents = [
    { id: 'remote', kind: 'a2a', name: 'Zeta', status: 'running', group: 'Remote', skills: [{ name: 'review' }] },
    { id: 'manifest', kind: 'object', name: 'Alpha', status: 'running', group: 'Local' },
    { id: 'stopped', kind: 'acp', name: 'Beta', status: 'stopped', group: 'Remote' },
  ];

  it('does not fabricate a runtime status for object manifests', () => {
    const { visible } = catalogueAgents(agents, { filter: 'Running', search: '', sortMode: 'Name' });
    expect(visible.map((agent) => agent.id)).toEqual(['remote']);
  });

  it('keeps deterministic name order inside type groups and searches advertised skills', () => {
    const { visible, sections } = catalogueAgents(agents, { filter: 'All', search: '', sortMode: 'Type' });
    expect(visible.map((agent) => agent.id)).toEqual(['manifest', 'stopped', 'remote']);
    expect(sections.map((section) => [section.key, section.items.map((agent) => agent.id)])).toEqual([
      ['Local', ['manifest']],
      ['Remote', ['stopped', 'remote']],
    ]);
    expect(catalogueAgents(agents, { filter: 'All', search: 'review', sortMode: 'Type' }).visible.map((agent) => agent.id))
      .toEqual(['remote']);
  });
});

describe('catalogueRoutines', () => {
  const routines = [
    { id: 'zeta', name: 'Zeta', role: 'weekly review' },
    { id: 'alpha', name: 'Alpha', role: 'daily summary' },
    { id: 'failed', name: 'Failed run', role: 'weekly review' },
  ];
  const runStatusById = { zeta: 'Running', alpha: 'Paused', failed: 'Failed' };

  it('uses the latest correlated run status for filters and name ordering', () => {
    const { visible, sections } = catalogueRoutines(routines, {
      filter: 'Failed', search: 'weekly', sortMode: 'Name', runStatusById,
    });
    expect(visible.map((routine) => routine.id)).toEqual(['failed']);
    expect(sections).toEqual([{ key: 'all', title: null, items: [routines[2]] }]);
  });
});
