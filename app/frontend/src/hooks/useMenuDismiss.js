import { useCallback, useEffect, useRef } from 'react';

/**
 * Dismiss a non-portal menu when a pointer lands outside its root or Escape is
 * pressed. Portal menus manage their own containment and timing.
 */
export function useMenuDismiss(open, rootRef, onDismiss) {
  const onDismissRef = useRef(onDismiss);

  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  const dismiss = useCallback(() => {
    onDismissRef.current?.();
  }, []);

  useEffect(() => {
    if (!open) return undefined;

    const handleMouseDown = (event) => {
      if (!rootRef.current?.contains(event.target)) {
        dismiss();
      }
    };
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        dismiss();
      }
    };

    document.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleMouseDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [dismiss, open, rootRef]);
}
