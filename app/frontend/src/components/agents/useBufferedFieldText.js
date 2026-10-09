import { useState } from 'react';


// Shared by every "text <-> structured value" field (Inputs, response_schema
// field lists, routine input/output maps) whose parser drops an in-progress
// line -- that's how each one recognizes "not a real entry yet" (no colon,
// no name). Deriving the textarea's value fresh from `toText(fromText(raw))`
// on every render, as these fields did before this hook existed, erases
// whatever the author is mid-typing (a name with no colon after it yet, a
// colon with nothing after it yet) the instant it re-renders -- keystrokes
// before the FIRST fully-formed line just vanish, which is indistinguishable
// from "I can't type in this field" (confirmed live: typing into a fresh
// response_schema field did nothing at all, every character wiped before
// the next keystroke could build on it).
//
// The buffer resets from `value` only when `id` changes (a different node
// is now selected) or `value` changed for a reason other than this field's
// own last edit (e.g. the "Edit as JSON" panel) -- tracked by comparing
// object/array identity against what this field itself last emitted.
// `toText` must be pure/stable (called during render).
export function useBufferedFieldText(id, value, toText) {
  const [trackedId, setTrackedId] = useState(id);
  const [trackedValue, setTrackedValue] = useState(value);
  const [text, setText] = useState(() => toText(value));
  if (trackedId !== id || value !== trackedValue) {
    setTrackedId(id);
    setTrackedValue(value);
    const nextText = toText(value);
    if (nextText !== text) setText(nextText);
  }
  return [text, setText, setTrackedValue];
}
