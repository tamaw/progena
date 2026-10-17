import { useEffect, useRef } from 'react';
import { ApproveActionIcon, EditActionIcon } from '../layout/DetailHeader';

/** Renders a DetailHeader title with the shared inline rename interaction. */
export default function EditableDetailTitle({
  title,
  editor,
  maxLength,
  ariaLabel,
  entityLabel,
  disabled = false,
}) {
  const renameLabel = `Rename ${entityLabel}`;
  const saveLabel = `Save ${entityLabel} name`;
  const {
    isEditing,
    draft,
    saving,
    setDraft,
    start,
    cancel,
    save,
  } = editor;
  const inputRef = useRef(null);

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isEditing]);


  return (
    <>
      {isEditing ? (
        <input
          ref={inputRef}
          type="text"
          className="chat-page-header__title-input"
          value={draft}
          maxLength={maxLength}
          aria-label={ariaLabel}
          disabled={saving}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={saving ? undefined : cancel}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              void save();
            } else if (event.key === 'Escape') {
              event.preventDefault();
              cancel();
            }
          }}
        />
      ) : (
        <span className="chat-detail-header__title-text">{title}</span>
      )}
      <button
        type="button"
        className="chat-icon-button chat-detail-header__rename"
        data-tooltip={isEditing ? saveLabel : renameLabel}
        aria-label={isEditing ? saveLabel : renameLabel}
        disabled={disabled || saving}
        onMouseDown={isEditing ? (event) => event.preventDefault() : undefined}
        onClick={isEditing ? () => { void save(); } : start}
      >
        {isEditing ? <ApproveActionIcon /> : <EditActionIcon />}
      </button>
    </>
  );
}
