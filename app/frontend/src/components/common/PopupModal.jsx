import { createPortal } from 'react-dom';
import { useEffect, useRef } from 'react';

export default function PopupModal({
  eyebrow = 'Confirm action',
  title,
  message,
  confirmLabel = 'Confirm',
  busyLabel = 'Working…',
  busy = false,
  tone = 'danger',
  onClose,
  onConfirm,
}) {
  const confirmRef = useRef(null);
  const openerRef = useRef(document.activeElement);

  useEffect(() => {
    const opener = openerRef.current;
    confirmRef.current?.focus();
    const handleKeyDown = (event) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      if (opener?.isConnected) opener.focus();
    };
  }, [busy, onClose]);

  const modal = (
    <div className="sidebar-modal-backdrop popup-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="sidebar-modal popup-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="popup-modal-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="sidebar-modal__head">
          <div>
            <span className="sidebar-modal__eyebrow">{eyebrow}</span>
            <h3 id="popup-modal-title">{title}</h3>
          </div>
          <button type="button" className="sidebar-modal__close" aria-label="Close" onClick={onClose} disabled={busy}>×</button>
        </header>
        <p className="sidebar-modal__field">{message}</p>
        <footer className="sidebar-modal__foot">
          <button type="button" className="task-btn task-btn--secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button ref={confirmRef} type="button" className={`popup-modal__confirm popup-modal__confirm--${tone}`} onClick={onConfirm} disabled={busy}>
            {busy ? busyLabel : confirmLabel}
          </button>
        </footer>
      </section>
    </div>
  );

  return createPortal(modal, document.body);
}
