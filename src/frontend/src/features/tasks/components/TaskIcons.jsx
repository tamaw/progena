

// agentColors, formatAgentName, normalizePriority, priorityLabel,
// isArchivedCard, cardTags, getCardService, getCardAssignee, buildCardBadges,
// sortCards, and groupVisibleCards were extracted to tasksPageModel.js in the
// frontend-refactor branch (imported above); groupVisibleCards there also
// gained the projectId filter this branch added.

export function ChevronRightIcon() {
  return (
    <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
      <path d="M4 2l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

// "Jump to the blocking task" — a small launch arrow leaping out of its corner.
export function DependencyOpenIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M5.5 10.5 11 5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" />
      <path d="M6.6 5H11v4.4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" />
    </svg>
  );
}
