import { useState } from 'react';
import JsonCodeEditor from '../common/JsonCodeEditor';

// Chevrons for the right-edge dock / collapse seam (point toward the action:
// left = expand the panel leftward, right = collapse it away).
function ChevronLeftSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m9.8 4.2-3.6 3.8 3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

function ChevronRightSmall() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="m6.2 4.2 3.6 3.8-3.6 3.8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

// Collapsed right-edge rail that opens the JSON editor. Mirrors the Routines
// canvas tools dock but overlays the workflow canvas's right edge.
export function WorkflowJsonDock({ onOpen }) {
  return (
    <aside className="canvas-json-dock" aria-label="Workflow tools">
      <button
        type="button"
        className="canvas-json-dock__item"
        title="Edit as JSON"
        aria-label="Edit workflow as JSON"
        onClick={onOpen}
      >
        <ChevronLeftSmall />
        <span className="canvas-json-dock__label">JSON</span>
      </button>
    </aside>
  );
}

// Edit the workflow document as JSON and hand the parsed object back to the
// caller. Keyed by `value` at mount so opening starts from the current state.
export function WorkflowJsonEditor({ value, onApply, onClose }) {
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  function handleApply() {
    let parsed;
    try {
      parsed = JSON.parse(draft);
    } catch (parseError) {
      setError(`Invalid JSON: ${parseError.message}`);
      return;
    }
    try {
      onApply(parsed);
      setError(null);
      setSaved(true);
    } catch (applyError) {
      setError(applyError.message || 'Failed to apply workflow');
    }
  }

  return (
    <aside className="canvas-json-editor">
      <button
        type="button"
        className="canvas-json-editor__collapse"
        onClick={onClose}
        title="Collapse"
        aria-label="Collapse JSON editor"
      >
        <ChevronRightSmall />
      </button>
      <header className="canvas-json-editor__head">
        <h2>Edit workflow as JSON</h2>
      </header>
      <JsonCodeEditor
        value={draft}
        onChange={(event) => { setDraft(event.target.value); setSaved(false); }}
      />
      {error ? <p className="canvas-json-editor__error">{error}</p> : null}
      {saved ? <p className="canvas-json-editor__saved">Workflow applied.</p> : null}
      <div className="canvas-json-editor__actions">
        <button
          type="button"
          className="node-action-btn"
          onClick={() => { setDraft(value); setError(null); setSaved(false); }}
        >
          Reset
        </button>
        <button type="button" className="node-action-btn primary" onClick={handleApply}>
          Apply changes
        </button>
      </div>
    </aside>
  );
}
