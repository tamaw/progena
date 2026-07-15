import { useCallback, useEffect, useRef, useState } from 'react';

function ImageModal({ src, alt, onClose }) {
  const closeButtonRef = useRef(null);
  const modalRef = useRef(null);

  useEffect(() => {
    closeButtonRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== 'Tab') return;

      const modal = modalRef.current;
      if (!modal) return;
      const focusable = Array.from(modal.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ));
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const focusIsOutsideModal = !modal.contains(document.activeElement);
      if (e.shiftKey) {
        if (focusIsOutsideModal || document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else if (focusIsOutsideModal || document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="img-modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="Image preview">
      <div className="img-modal-inner" ref={modalRef} onClick={(e) => e.stopPropagation()}>
        <button ref={closeButtonRef} className="img-modal-close" onClick={onClose} aria-label="Close">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M1 1l12 12M13 1L1 13" />
          </svg>
        </button>
        <img className="img-modal-img" src={src} alt={alt || 'Full size'} />
      </div>
    </div>
  );
}

// Renders both tool-result images and markdown images from the message text
// (see collectMessageImages() in conversationModel.js) as one strip of
// fixed-size, click-to-expand thumbnails -- callers pass already-normalized
// { id, src, alt } entries.
export default function MessageImageStrip({ images }) {
  const [lightboxImage, setLightboxImage] = useState(null);
  const openerRef = useRef(null);
  const openLightbox = useCallback((image, opener) => {
    const activeElement = opener || document.activeElement;
    openerRef.current = activeElement && typeof activeElement.focus === 'function' ? activeElement : null;
    setLightboxImage(image);
  }, []);
  const closeLightbox = useCallback(() => {
    setLightboxImage(null);
    const opener = openerRef.current;
    openerRef.current = null;
    if (opener && typeof opener.focus === 'function' && opener.isConnected !== false) {
      opener.focus();
    }
  }, []);

  return (
    <>
      {lightboxImage && (
        <ImageModal src={lightboxImage.src} alt={lightboxImage.alt} onClose={closeLightbox} />
      )}
      <div className="img-strip msg-img-strip">
        {images.map((image, index) => (
          <div
            key={image.id || index}
            className="img-thumb img-thumb--photo"
            data-label={`image_${index + 1}`}
            onClick={(e) => openLightbox(image, e.currentTarget)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key !== 'Enter' && e.key !== ' ') return;
              e.preventDefault();
              openLightbox(image, e.currentTarget);
            }}
            aria-label={`View image ${index + 1} full size`}
          >
            <div className="img-thumb__inner img-thumb__inner--photo">
              <img src={image.src} alt={image.alt} />
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
