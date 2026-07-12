import { useEffect, useRef } from 'react';

// How close to the bottom (in px) still counts as "at the bottom" -- lets the
// user scroll up a few pixels without immediately losing stickiness.
const NEAR_BOTTOM_PX = 24;

// Keeps a fixed-height, scrollable card body tailing its latest content as
// it streams in (like `tail -f`), without yanking the view if the user has
// scrolled up to read earlier entries. `dep` is a cheap value that changes
// whenever new content is appended (e.g. an item count or content length) --
// pass a plain scalar/string, not an object, so the effect only re-fires on
// real growth. Purely local (ref + a mutable "am I stuck to the bottom" flag)
// so concurrent cards never share or interfere with each other's scroll state.
export default function useStickyScroll(dep) {
  const ref = useRef(null);
  const stickyRef = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (!el || !stickyRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [dep]);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    stickyRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
  };

  return { ref, onScroll };
}
