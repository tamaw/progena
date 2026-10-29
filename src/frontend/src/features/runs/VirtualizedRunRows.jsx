import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

const DEFAULT_ROW_HEIGHT = 36;
const EXPANDED_ROW_HEIGHT = 280;

function firstIndexAtOrAfter(offsets, target) {
  let low = 0;
  let high = offsets.length - 1;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (offsets[middle + 1] <= target) low = middle + 1;
    else high = middle;
  }
  return low;
}

/**
 * Keep the run chart's DOM bounded while allowing an expanded trace to report
 * its actual height. The scroll owner remains the chart so sticky headers and
 * the live-update affordance retain their existing coordinate system.
 */
export default function VirtualizedRunRows({ rows, scrollElement, renderRow }) {
  const elementsRef = useRef(new Map());
  const observerRef = useRef(null);
  const viewportFrameRef = useRef(null);
  const [heights, setHeights] = useState({});
  const [viewport, setViewport] = useState({ top: 0, height: 0 });

  const keys = useMemo(() => rows.map((row) => row.key), [rows]);
  const offsets = useMemo(() => {
    const next = [0];
    for (const row of rows) {
      next.push(next[next.length - 1] + (heights[row.key] || (row.expanded ? EXPANDED_ROW_HEIGHT : DEFAULT_ROW_HEIGHT)));
    }
    return next;
  }, [heights, rows]);

  const updateViewport = useCallback(() => {
    if (!scrollElement || viewportFrameRef.current !== null) return;
    viewportFrameRef.current = requestAnimationFrame(() => {
      viewportFrameRef.current = null;
      const next = { top: scrollElement.scrollTop, height: scrollElement.clientHeight };
      setViewport((current) => (
        current.top === next.top && current.height === next.height ? current : next
      ));
    });
  }, [scrollElement]);

  useEffect(() => {
    if (!scrollElement) return undefined;
    updateViewport();
    scrollElement.addEventListener('scroll', updateViewport, { passive: true });
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(updateViewport);
    observer?.observe(scrollElement);
    return () => {
      scrollElement.removeEventListener('scroll', updateViewport);
      observer?.disconnect();
      if (viewportFrameRef.current !== null) {
        cancelAnimationFrame(viewportFrameRef.current);
        viewportFrameRef.current = null;
      }
    };
  }, [scrollElement, updateViewport]);

  const setRowElement = useCallback((key, element) => {
    const previous = elementsRef.current.get(key);
    if (element) {
      elementsRef.current.set(key, element);
      observerRef.current?.observe(element);
    } else {
      if (previous) observerRef.current?.unobserve(previous);
      elementsRef.current.delete(key);
    }
  }, []);

  useLayoutEffect(() => {
    const measure = () => {
      setHeights((current) => {
        let changed = false;
        const next = { ...current };
        for (const key of keys) {
          const height = Math.ceil(elementsRef.current.get(key)?.getBoundingClientRect().height || 0);
          if (height && next[key] !== height) {
            next[key] = height;
            changed = true;
          }
        }
        return changed ? next : current;
      });
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    for (const element of elementsRef.current.values()) observer.observe(element);
    observerRef.current = observer;
    return () => {
      observer.disconnect();
      if (observerRef.current === observer) observerRef.current = null;
    };
  }, [keys]);

  const start = rows.length ? Math.max(0, firstIndexAtOrAfter(offsets, Math.max(0, viewport.top - 33) - 420)) : 0;
  const end = rows.length
    ? Math.min(rows.length, firstIndexAtOrAfter(offsets, Math.max(0, viewport.top - 33) + viewport.height + 420) + 2)
    : 0;

  return <div className="runs-virtual-body" style={{ height: offsets.at(-1) || 0 }}>
    {rows.slice(start, end).map((row, index) => {
      const rowIndex = start + index;
      return <div key={row.key} ref={(element) => setRowElement(row.key, element)} className="runs-virtual-row" style={{ transform: `translateY(${offsets[rowIndex]}px)` }}>
        {renderRow(row)}
      </div>;
    })}
  </div>;
}
