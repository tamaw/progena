

// Persist the quick chat conversation across panel open/close (the panel unmounts
// when closed), so reopening the *same* mode resumes the same conversation.
// Cleared by "New chat" (resetQuickChat) and whenever the mode changes between
// opens (e.g. closing a "connect an MCP server" chat on Tools and opening
// "create a knowledge base" from Knowledge must not resume the old one, or its
// suggestion chips never show since the history is no longer empty). Resets
// naturally on full page reload.
export const quickChatMemory = { history: [], threadId: null, mode: null };
