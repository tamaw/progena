// Pure helpers for the Profiles list, kept free of React for unit tests.

// Every profile names its provider and deployment (both required since
// provider connections moved to the database). "Customized" means it overrides
// the model's own behaviour: reasoning settings (whose editor offers
// "Inherit") or pricing.
const OVERRIDE_FIELDS = ['reasoning_enabled', 'reasoning_effort', 'reasoning_summary'];

export function isCustomized(profile) {
  return Boolean(profile.pricing) || OVERRIDE_FIELDS.some((field) => profile[field] != null);
}

/** Compact context window for the list's right-hand column, e.g. 200K, 1M. */
export function formatContextWindow(tokens) {
  if (!Number.isInteger(tokens) || tokens <= 0) return '';
  if (tokens >= 1_000_000) return `${+(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`;
  return String(tokens);
}

/** The list row's second line: deployment, reasoning effort, customized. */
export function profileRowDetail(profile) {
  return [
    profile.deployment,
    profile.reasoning_effort ? `reasoning ${profile.reasoning_effort}` : null,
    isCustomized(profile) ? 'customized' : null,
  ].filter(Boolean).join(' · ');
}
