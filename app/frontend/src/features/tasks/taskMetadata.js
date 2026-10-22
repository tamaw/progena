

function normalizeChecklistItems(items) {
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .filter((item) => item && typeof item === 'object')
    .map((item, index) => ({
      id: String(item.id || `item_${index}`),
      title: String(item.title || '').trim(),
      done: Boolean(item.done),
    }))
    .filter((item) => item.title);
}

// Checklists are fully dynamic: the agent (via `/tasks/`) or the user can
// add any number of named checklists. A legacy single `subtasks` list is
// surfaced as one default checklist for backward compatibility.
export function normalizeChecklists(metadata) {
  const lists = metadata?.checklists;
  if (Array.isArray(lists) && lists.length) {
    return lists
      .filter((list) => list && typeof list === 'object')
      .map((list, index) => ({
        id: String(list.id || `checklist_${index}`),
        title: String(list.title || 'Checklist').trim() || 'Checklist',
        items: normalizeChecklistItems(list.items),
      }));
  }
  const legacy = normalizeChecklistItems(metadata?.subtasks);
  return legacy.length ? [{ id: 'default', title: 'Checklist', items: legacy }] : [];
}

export function serializeChecklists(checklists) {
  return checklists.map((list) => ({
    id: list.id,
    title: list.title,
    items: list.items.map((item) => ({ id: item.id, title: item.title, done: item.done })),
  }));
}

// Dependencies are blocking-task references rendered as cards (title + a short
// #ref + an Open action). Legacy plain-string dependencies are upgraded to the
// {id,title,ref} shape on read so old cards keep working.
export function normalizeDependencies(metadata) {
  const dependencies = metadata?.dependencies;
  if (!Array.isArray(dependencies)) {
    return [];
  }
  return dependencies
    .map((item, index) => {
      if (typeof item === 'string') {
        const title = item.trim();
        return title ? { id: `dep_${index}`, title, ref: '' } : null;
      }
      if (item && typeof item === 'object') {
        const title = String(item.title || item.text || '').trim();
        return title
          ? { id: String(item.id || `dep_${index}`), title, ref: String(item.ref || '').trim() }
          : null;
      }
      return null;
    })
    .filter(Boolean);
}

export function serializeDependencies(dependencies) {
  return dependencies.map((dep) => ({ id: dep.id, title: dep.title, ref: dep.ref }));
}

export function normalizeCollaborators(metadata) {
  const collaborators = metadata?.collaborators;
  if (!Array.isArray(collaborators)) {
    return [];
  }
  return collaborators.map((item) => String(item || '').trim()).filter(Boolean);
}

export function normalizeAttachments(metadata) {
  const attachments = metadata?.attachments;
  if (!Array.isArray(attachments)) {
    return [];
  }
  return attachments
    .filter((item) => item && typeof item === 'object')
    .map((item, index) => ({
      id: String(item.id || `attachment_${index}`),
      label: String(item.label || item.url || '').trim(),
      url: String(item.url || '').trim(),
      kind: String(item.kind || 'link').trim() || 'link',
    }))
    .filter((item) => item.label && item.url);
}

export function buildDependencyDraft(value, board) {
  const trimmed = String(value || '').trim();
  if (!trimmed) {
    return null;
  }
  const normalized = trimmed.toLowerCase();
  const match = board?.cards.find((card) => (
    card.id === trimmed || String(card.title || '').trim().toLowerCase() === normalized
  ));
  if (match) {
    return {
      title: String(match.title || trimmed).trim(),
      ref: match.id,
    };
  }
  return {
    title: trimmed,
    ref: '',
  };
}
