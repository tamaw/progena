export function getAgentMentionContext(value, cursor) {
  const text = typeof value === 'string' ? value : '';
  const position = Number.isFinite(cursor) ? cursor : text.length;
  const uptoCursor = text.slice(0, position);
  const match = uptoCursor.match(/(^|\s)@([A-Za-z0-9_.-]*)$/);
  if (!match) {
    return null;
  }
  const tokenStart = uptoCursor.length - match[0].length + match[1].length;
  return {
    query: match[2].toLowerCase(),
    tokenStart,
    tokenEnd: position,
  };
}
