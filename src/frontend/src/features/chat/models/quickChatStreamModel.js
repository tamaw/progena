export function createSpectatorReplayState(existingAssistant, requestId) {
  return {
    assistantId: existingAssistant?.id || `qchat_spectate_${requestId}`,
    // observeResponseStream replays the whole response when no cursor is
    // supplied, so a cached streaming bubble supplies identity only.
    assistantContent: '',
  };
}
