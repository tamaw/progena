import { useCallback, useLayoutEffect, useRef } from 'react';

// A list that shows its first `visibleRows` children and scrolls the rest.
// Rows can differ in height (an optional detail line, wrapped text), so the cap
// is measured from the rendered rows instead of a fixed CSS height. Lists at or
// under the limit render unchanged. Style rows with the list's own classes
// (e.g. `agents-mini-list`); this only adds the cap and scrolling.
//
// The cap is written straight to the element (inline max-height plus
// `data-capped`), which React does not manage here, so measuring never
// triggers another render.
export default function CappedScrollList({ visibleRows = 5, className = '', children, ...rest }) {
  const ref = useRef(null);

  const measure = useCallback(() => {
    const list = ref.current;
    if (!list) return;
    const rows = list.children;
    if (rows.length <= visibleRows) {
      list.style.maxHeight = '';
      delete list.dataset.capped;
      return;
    }
    const last = rows[visibleRows - 1];
    const height = last.getBoundingClientRect().bottom - list.getBoundingClientRect().top + list.scrollTop;
    list.style.maxHeight = `${height}px`;
    list.dataset.capped = 'true';
  }, [visibleRows]);

  // Rows can be added or removed without the (capped) list changing size, so
  // re-measure after every render.
  useLayoutEffect(() => {
    measure();
  });

  // Width changes re-flow rows and change their heights.
  useLayoutEffect(() => {
    const list = ref.current;
    if (!list) return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(list);
    return () => observer.disconnect();
  }, [measure]);

  return (
    <div ref={ref} className={`capped-scroll-list ${className}`.trim()} {...rest}>
      {children}
    </div>
  );
}
