export function createLiveAssistantTurnStore() {
  let liveTurn = null;
  const listeners = new Set();

  return {
    getSnapshot: () => liveTurn,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    set(nextTurn) {
      if (Object.is(liveTurn, nextTurn)) {
        return;
      }
      liveTurn = nextTurn;
      for (const listener of [...listeners]) {
        listener();
      }
    },
  };
}
