/**
 * Pure helpers for the Plugins page — extracted for testability.
 */

export function buildSkillTree(skills) {
  const groups = {};

  skills.forEach((skill) => {
    const agentKey = skill.agent || 'progenitor';
    if (!groups[agentKey]) {
      groups[agentKey] = [];
    }
    groups[agentKey].push(skill);
  });

  return Object.entries(groups)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, items]) => ({
      name,
      skills: [...items].sort((left, right) => {
        const leftLabel = left.title || left.name;
        const rightLabel = right.title || right.name;
        return leftLabel.localeCompare(rightLabel);
      }),
    }));
}

export function normalizeSkillSegment(value) {
  return value.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
}

/**
 * Turn a skill agent segment into a readable filter-tab or meta label.
 * Segments with `__` (marketplace__plugin) are split; bare segments stay as-is.
 */
export function formatSourceLabel(agent) {
  if (!agent || typeof agent !== 'string') {
    return 'progenitor';
  }
  if (!agent.includes('__')) {
    return agent;
  }
  const [marketplace, plugin] = agent.split('__', 2);
  return `${marketplace} / ${plugin}`;
}
