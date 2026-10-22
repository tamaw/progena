/**
 * Consume only events newer than the caller's sequence cursor, in queue order.
 * Returning false from consume stops after that event while retaining its cursor.
 */
export function consumeUiEventQueue(queue, cursor, consume) {
  let nextCursor = Number.isFinite(cursor) ? cursor : 0;
  for (const entry of Array.isArray(queue) ? queue : []) {
    const sequence = entry?.sequence;
    if (!Number.isFinite(sequence) || sequence <= nextCursor) {
      continue;
    }
    nextCursor = sequence;
    if (consume(entry) === false) {
      break;
    }
  }
  return nextCursor;
}
