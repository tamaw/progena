// List shaping for the Instructions page. Instructions are grouped by the agent
// that reads them (the first path segment); Progenitor's own come first since
// they are the only editable ones.

export const INSTRUCTION_FILTERS = ['All', 'Editable', 'Read-only'];

const RUNTIME_GROUP = 'progenitor';

export function groupLabel(group) {
  const words = String(group || '').split('_').filter(Boolean);
  if (!words.length) return 'Ungrouped';
  const label = words.join(' ');
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function matches(instruction, filter, query) {
  if (filter === 'Editable' && !instruction.editable) return false;
  if (filter === 'Read-only' && instruction.editable) return false;
  if (!query) return true;
  return [instruction.title, instruction.description, instruction.path]
    .some((value) => String(value || '').toLowerCase().includes(query));
}

export function groupInstructions(instructions, filter, rawQuery) {
  const query = String(rawQuery || '').trim().toLowerCase();
  const byGroup = new Map();
  instructions
    .filter((instruction) => matches(instruction, filter, query))
    .forEach((instruction) => {
      if (!byGroup.has(instruction.group)) byGroup.set(instruction.group, []);
      byGroup.get(instruction.group).push(instruction);
    });
  return [...byGroup.entries()]
    .sort(([left], [right]) => {
      if (left === right) return 0;
      if (left === RUNTIME_GROUP) return -1;
      if (right === RUNTIME_GROUP) return 1;
      return left.localeCompare(right);
    })
    .map(([group, items]) => ({
      key: group,
      title: groupLabel(group),
      items: [...items].sort((left, right) => left.title.localeCompare(right.title)),
    }));
}

// /instructions/<group>/<file>.md, one encoded URL segment per path segment.
export function instructionHref(path) {
  return `/instructions/${String(path).split('/').map(encodeURIComponent).join('/')}`;
}
