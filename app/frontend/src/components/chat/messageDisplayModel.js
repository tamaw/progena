export function formatDisplayToolCalls(displayToolCalls) {
  if (!Array.isArray(displayToolCalls) || displayToolCalls.length === 0) {
    return '';
  }
  return displayToolCalls
    .map((call) => {
      if (typeof call === 'string') return call;
      if (call && typeof call === 'object' && typeof call.name === 'string') return call.name;
      return '';
    })
    .filter(Boolean)
    .join(', ');
}
