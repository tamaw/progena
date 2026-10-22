

export function normalizeSearchText(value) {
  return String(value || '')
    .replace(/[_:/-]+/g, ' ')
    .trim()
    .toLowerCase();
}
