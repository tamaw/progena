/**
 * Small status glyphs shared by every "mini card/event" nested chain in the
 * chat UI -- A2ACallCard's per-update sub-entries (submitted/working/
 * completed/failed) and SubagentCard's per-nested-tool-call children
 * (running/success/error). One set of icons for "in progress / succeeded /
 * failed" instead of two near-identical copies living in each card file.
 */

// In progress: an open ring -- reads as "in progress" even without animation.
export function WorkingGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M12 3a9 9 0 1 0 9 9" />
    </svg>
  );
}

// Submitted: an upward arrow showing work handed off to another agent.
export function SubmittedGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="12" y1="21" x2="12" y2="3" />
      <polyline points="4 11 12 3 20 11" />
    </svg>
  );
}

// Succeeded: checkmark.
export function CompletedGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="4 12 9 17 20 6" />
    </svg>
  );
}

// Failed/rejected/canceled/error: X.
export function FailedGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="6" y1="6" x2="18" y2="18" />
      <line x1="6" y1="18" x2="18" y2="6" />
    </svg>
  );
}
