import { createPortal } from 'react-dom';
import { useEffect, useRef } from 'react';

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  '[href]',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(', ');

// Open dialogs in mount order; only the topmost one owns Tab and Escape.
const openDialogs = [];

export default function Modal({
  children,
  onClose,
  busy = false,
  className = '',
  backdropClassName = '',
  ariaLabel,
  ariaLabelledby,
  initialFocusRef,
  backdropDismissEvent = 'mouseDown',
}) {
  const dialogRef = useRef(null);
  const openerRef = useRef(typeof document === 'undefined' ? null : document.activeElement);
  const onCloseRef = useRef(onClose);
  const busyRef = useRef(busy);

  useEffect(() => {
    onCloseRef.current = onClose;
    busyRef.current = busy;
  }, [busy, onClose]);

  useEffect(() => {
    const dialog = dialogRef.current;
    const opener = openerRef.current;

    const focusables = () => Array.from(dialog?.querySelectorAll(FOCUSABLE_SELECTOR) || []);
    const focusInitial = () => {
      const initialFocus = initialFocusRef?.current;
      if (initialFocus && typeof initialFocus.focus === 'function') {
        initialFocus.focus();
        return;
      }
      const firstFocusable = focusables()[0];
      if (firstFocusable) firstFocusable.focus();
      else dialog?.focus();
    };
    focusInitial();

    const handleKeyDown = (event) => {
      if (openDialogs[openDialogs.length - 1] !== dialog) return;
      if (event.key === 'Escape') {
        if (!event.defaultPrevented && !busyRef.current) {
          onCloseRef.current();
        }
        return;
      }
      if (event.key !== 'Tab') return;

      const controls = focusables();
      if (controls.length === 0) {
        event.preventDefault();
        dialog?.focus();
        return;
      }
      const first = controls[0];
      const last = controls[controls.length - 1];
      const activeElement = document.activeElement;
      const outsideControls = activeElement === dialog || !dialog?.contains(activeElement);
      if (outsideControls || activeElement === (event.shiftKey ? first : last)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    };

    openDialogs.push(dialog);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      openDialogs.splice(openDialogs.indexOf(dialog), 1);
      document.removeEventListener('keydown', handleKeyDown);
      if (opener?.isConnected) opener.focus();
    };
  }, [initialFocusRef]);

  const dismissFromBackdrop = (event) => {
    if (!busy && event.target === event.currentTarget) {
      onClose();
    }
  };
  const backdropProps = backdropDismissEvent === 'click'
    ? { onClick: dismissFromBackdrop }
    : { onMouseDown: dismissFromBackdrop };

  return createPortal(
    <div className={backdropClassName} role="presentation" {...backdropProps}>
      <section
        ref={dialogRef}
        className={className}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledby}
        tabIndex={-1}
      >
        {children}
      </section>
    </div>,
    document.body,
  );
}
