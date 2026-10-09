import { createContext } from 'react';


// Edit callbacks shared with the card/branch renderers (avoids threading
// mutable refs through node data, which React forbids reading during render).
export const GraphEditContext = createContext(null);

// The required fields the inspected node is still missing. A context rather
// than a prop so the marker reaches every ConfigField without rethreading each
// of NodeConfigBody's per-kind call sites.
export const EMPTY_MISSING = new Set();
export const MissingFieldsContext = createContext(EMPTY_MISSING);
