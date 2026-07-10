import { useCallback, useEffect, useState } from 'react';

function ImageModal({ src, alt, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="img-modal-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="img-modal-inner" onClick={(e) => e.stopPropagation()}>
        <button className="img-modal-close" onClick={onClose} aria-label="Close">
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
  const openLightbox = useCallback((image) => setLightboxImage(image), []);
  const closeLightbox = useCallback(() => setLightboxImage(null), []);

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
            onClick={() => openLightbox(image)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === 'Enter' && openLightbox(image)}
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
