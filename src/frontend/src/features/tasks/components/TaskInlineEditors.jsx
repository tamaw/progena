import { useEffect, useRef, useState } from 'react';
import InlineFailure from '../../../components/InlineFailure';
import useEntityDraft from '../../../hooks/useEntityDraft';

// Minimal add affordance for the meta pane: a small "+ Add" link (or "+" pill for
// tags) that reveals a single inline input only when clicked. The input text is
// retained for its task and field while the popover itself remains ephemeral.
export function InlineAdd({
  draftKey,
  openLabel = '+ Add',
  operation = 'Add task item',
  placeholder,
  disabled = false,
  pill = false,
  pillLabel = '',
  onSubmit,
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [value, setValue, controls] = useEntityDraft(draftKey, '');
  const {
    clear,
    beginSave,
    trackSave,
    failSave,
    hasDraft,
    saving,
    error,
  } = controls;
  const inputRef = useRef(null);
  const isOpen = open && (!pending || hasDraft || saving);

  useEffect(() => {
    if (isOpen) {
      inputRef.current?.focus();
    }
  }, [isOpen]);

  const discard = () => {
    clear();
    setPending(false);
    setOpen(false);
  };

  const submit = async () => {
    const trimmed = value.trim();
    if (!trimmed) return;

    const ticket = beginSave();
    if (!ticket) return;
    setPending(true);
    try {
      const result = await onSubmit(trimmed, {
        ticket,
        trackSave,
        failSave,
      });
      if (result?.ok === false && !result.stale && !result.cancelled && !result.busy) {
        failSave(ticket, result.error || 'Add item failed. Please try again.');
      }
    } catch (submitError) {
      failSave(ticket, submitError);
    }
  };

  if (!isOpen) {
    return (
      <button
        type="button"
        className={pill ? 'task-lbl task-lbl--add' : 'task-add-sub'}
        data-tooltip={pill ? openLabel : undefined}
        aria-label={pill ? openLabel : undefined}
        onClick={() => {
          setPending(false);
          setOpen(true);
        }}
        disabled={disabled || saving}
      >
        {pill ? (pillLabel ? `+ ${pillLabel}` : '+') : openLabel}
      </button>
    );
  }

  // A click-away only closes this transient popover; it deliberately leaves
  // the retained raw field text available when the user returns to the task.
  const handleBlur = (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) {
      setOpen(false);
    }
  };

  return (
    <div className="task-comment-input" style={{ marginTop: 8 }} onBlur={handleBlur}>
      <input
        ref={inputRef}
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            void submit();
          } else if (event.key === 'Escape') {
            event.preventDefault();
            discard();
          }
        }}
        disabled={disabled || saving}
      />
      <button
        type="button"
        className="task-add-sub"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => { void submit(); }}
        disabled={disabled || saving}
      >
        Add
      </button>
      {error ? <InlineFailure failure={error} operation={operation} /> : null}
    </div>
  );
}

// Attachments need two fields, so they get their own toggled variant.
export function InlineAddAttachment({ draftKey, disabled = false, onSubmit }) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [value, setValue, controls] = useEntityDraft(draftKey, { label: '', url: '' });
  const label = typeof value?.label === 'string' ? value.label : '';
  const url = typeof value?.url === 'string' ? value.url : '';
  const {
    clear,
    beginSave,
    trackSave,
    failSave,
    hasDraft,
    saving,
    error,
  } = controls;
  const labelRef = useRef(null);
  const isOpen = open && (!pending || hasDraft || saving);

  useEffect(() => {
    if (isOpen) {
      labelRef.current?.focus();
    }
  }, [isOpen]);

  const discard = () => {
    clear();
    setPending(false);
    setOpen(false);
  };

  const updateField = (field, nextValue) => {
    setValue({ label, url, [field]: nextValue });
  };

  const submit = async () => {
    if (!label.trim() || !url.trim()) return;

    const ticket = beginSave();
    if (!ticket) return;
    setPending(true);
    try {
      const result = await onSubmit(label.trim(), url.trim(), {
        ticket,
        trackSave,
        failSave,
      });
      if (result?.ok === false && !result.stale && !result.cancelled && !result.busy) {
        failSave(ticket, result.error || 'Add attachment failed. Please try again.');
      }
    } catch (submitError) {
      failSave(ticket, submitError);
    }
  };

  if (!isOpen) {
    return (
      <div style={{ marginTop: 8 }}>
        <button
          type="button"
          className="task-lbl task-lbl--add"
          data-tooltip="Add attachment"
          aria-label="Add attachment"
          onClick={() => {
            setPending(false);
            setOpen(true);
          }}
          disabled={disabled || saving}
        >
          +
        </button>
      </div>
    );
  }

  // Moving elsewhere closes the popover without discarding either field.
  const handleBlur = (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) {
      setOpen(false);
    }
  };

  return (
    <div
      className="task-comment-input"
      style={{ marginTop: 8, flexDirection: 'column', alignItems: 'stretch' }}
      onBlur={handleBlur}
    >
      <input
        ref={labelRef}
        type="text"
        value={label}
        placeholder="Attachment label..."
        onChange={(event) => updateField('label', event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault();
            discard();
          }
        }}
        disabled={disabled || saving}
      />
      <input
        type="text"
        value={url}
        placeholder="Attachment URL..."
        onChange={(event) => updateField('url', event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault();
            discard();
          }
        }}
        disabled={disabled || saving}
      />
      <button
        type="button"
        className="task-add-sub"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => { void submit(); }}
        disabled={disabled || saving || !label.trim() || !url.trim()}
      >
        Add attachment
      </button>
      {error ? <InlineFailure failure={error} operation="Add task attachment" /> : null}
    </div>
  );
}
