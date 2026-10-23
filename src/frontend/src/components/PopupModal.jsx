import { useId, useRef } from 'react';
import Modal from './Modal';
import InlineFailure from './InlineFailure';

export default function PopupModal({
  eyebrow = 'Confirm action',
  title,
  message,
  error,
  confirmLabel = 'Confirm',
  busyLabel = 'Working…',
  busy = false,
  tone = 'danger',
  onClose,
  onConfirm,
}) {
  const confirmRef = useRef(null);
  const titleId = useId();

  return (
    <Modal
      busy={busy}
      className="sidebar-modal popup-modal"
      backdropClassName="sidebar-modal-backdrop popup-modal-backdrop"
      ariaLabelledby={titleId}
      initialFocusRef={confirmRef}
      onClose={onClose}
    >
      <header className="sidebar-modal__head">
        <div>
          <span className="sidebar-modal__eyebrow">{eyebrow}</span>
          <h3 id={titleId}>{title}</h3>
        </div>
        <button type="button" className="sidebar-modal__close" aria-label="Close" onClick={onClose} disabled={busy}>×</button>
      </header>
      <p className="sidebar-modal__field">{message}</p>
      <InlineFailure failure={error} operation={confirmLabel} />
      <footer className="sidebar-modal__foot">
        <button type="button" className="task-btn task-btn--secondary" onClick={onClose} disabled={busy}>Cancel</button>
        <button ref={confirmRef} type="button" className={`popup-modal__confirm popup-modal__confirm--${tone}`} onClick={onConfirm} disabled={busy}>
          {busy ? busyLabel : confirmLabel}
        </button>
      </footer>
    </Modal>
  );
}
