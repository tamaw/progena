function positiveInteger(value) {
  return Number.isInteger(value) && value > 0 ? value : null;
}

function nonNegativeInteger(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function optionalString(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

/** Normalize the backend's latest parent-call footprint into UI naming. */
export function normalizeContextUsage(usage) {
  if (!usage || typeof usage !== 'object' || Array.isArray(usage)) {
    return null;
  }

  const inputTokens = nonNegativeInteger(usage.input_tokens ?? usage.inputTokens);
  const outputTokens = nonNegativeInteger(usage.output_tokens ?? usage.outputTokens);
  const totalTokens = nonNegativeInteger(usage.total_tokens ?? usage.totalTokens);
  if (inputTokens === null || outputTokens === null || totalTokens === null) {
    return null;
  }

  const profileName = optionalString(usage.profile_name ?? usage.profileName);
  const deployment = optionalString(usage.deployment);
  const providerId = optionalString(usage.provider_id ?? usage.providerId);
  return {
    inputTokens,
    outputTokens,
    totalTokens,
    ...(profileName ? { profileName } : {}),
    ...(deployment ? { deployment } : {}),
    ...(providerId ? { providerId } : {}),
  };
}

function profileMatchesUsage(profile, usage) {
  if (!profile || typeof profile !== 'object' || Array.isArray(profile)) {
    return false;
  }
  if (usage.profileName && profile.name !== usage.profileName) {
    return false;
  }
  if (usage.deployment && profile.deployment !== usage.deployment) {
    return false;
  }
  if (usage.providerId && profile.provider_id !== usage.providerId) {
    return false;
  }
  return Boolean(usage.profileName || usage.deployment || usage.providerId);
}

function matchingProfile(profiles, usage) {
  const matches = profiles.filter((profile) => profileMatchesUsage(profile, usage));
  return matches.length === 1 ? matches[0] : null;
}

function defaultProfile(profiles) {
  const candidates = profiles.filter((profile) => (
    profile?.name === 'general' || profile?.name === 'default'
  ));
  return candidates.length === 1 ? candidates[0] : null;
}

/**
 * Resolve one composer's capacity only when the latest parent model call can
 * be identified unambiguously. Context limits are configured per profile, not
 * inferred from a deployment/model name.
 */
export function resolveComposerContextUsage({ contextUsage, profiles, isFreshConversation = false }) {
  const catalogue = Array.isArray(profiles) ? profiles : [];
  const normalizedUsage = normalizeContextUsage(contextUsage);
  if (normalizedUsage) {
    const profile = matchingProfile(catalogue, normalizedUsage);
    const maxTokens = positiveInteger(profile?.context_window);
    return maxTokens === null ? null : { usedTokens: normalizedUsage.totalTokens, maxTokens };
  }

  if (!isFreshConversation) {
    return null;
  }

  const profile = defaultProfile(catalogue);
  const maxTokens = positiveInteger(profile?.context_window);
  return maxTokens === null ? null : { usedTokens: 0, maxTokens };
}

/** Configured retention estimate, not an observation of provider cache state. */
export function resolveComposerCacheExpiry({ messages = [], profiles = [] } = {}) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== 'assistant' || message.isStreaming || message.error) continue;
    const usage = normalizeContextUsage(message.contextUsage);
    const respondedAt = message.responseTimestamp;
    if (!usage || !Number.isFinite(respondedAt) || respondedAt <= 0) return null;
    const ttl = matchingProfile(profiles, usage)?.cache_ttl_seconds;
    if (!Number.isSafeInteger(ttl) || ttl <= 0) return null;
    const expiresAt = respondedAt + ttl * 1000;
    return Number.isSafeInteger(expiresAt) ? { expiresAt, ttlSeconds: ttl } : null;
  }
  return null;
}

export function cacheCountdownLabel(expiresAt, now) {
  const minutes = Math.max(0, Math.ceil((expiresAt - now) / 60000));
  return minutes >= 100 ? `${Math.ceil(minutes / 60)}h` : `${minutes}m`;
}

/** Return the newest persisted latest-call usage, never an accumulated total. */
export function latestContextUsage(messages) {
  if (!Array.isArray(messages)) {
    return null;
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.role !== 'assistant') continue;
    const usage = normalizeContextUsage(messages[index].contextUsage);
    if (usage) return usage;
  }
  return null;
}
