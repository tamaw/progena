const CANCELLED_SENTINEL = '[Cancelled]';
const CANCELLED_SUFFIX = ` ${CANCELLED_SENTINEL}`;

/** Remove only the retired app-owned cancellation sentinel from cancelled turns. */
export function normalizeCancelledContent(content, responseStatus) {
  if (responseStatus !== 'cancelled' || typeof content !== 'string') {
    return content;
  }
  if (content === CANCELLED_SENTINEL) {
    return '';
  }
  return content.endsWith(CANCELLED_SUFFIX)
    ? content.slice(0, -CANCELLED_SUFFIX.length)
    : content;
}
