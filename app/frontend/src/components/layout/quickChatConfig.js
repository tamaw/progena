// Quick-chat panel configuration: per-mode title/subtitle/placeholder and
// example prompts shown to the user. The panel is a thin front door onto the
// same chat the agent handles everywhere else -- it sends exactly what the
// user typed and relies on the agent's own skill routing (see each
// managing-* skill's own description) to recognize intent, the same as if
// the request came from the main Chat page, Telegram, or a scheduled task.

const quickPrompts = [
  'Summarize the latest workspace activity',
  'What should I work on next?',
  'Draft a short status update',
];
const scheduleQuickPrompts = [
  'One-off: remind me tomorrow at 9am to send the weekly update.',
  'Recurring: every weekday at 8:30am, prepare my morning brief.',
  'Scheduled: on the first day of each month at 10am, review budget drift.',
];
export const defaultQuickChatConfig = {
  mode: 'default',
  title: 'Quick chat',
  subtitle: '',
  placeholder: 'Ask anything...',
  prompts: quickPrompts,
};
const scheduleQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: 'schedule-create',
  prompts: scheduleQuickPrompts,
};
const mcpQuickPrompts = [
  'Connect the GitHub MCP server at https://api.githubcopilot.com/mcp/.',
  'Connect our Linear MCP server at https://mcp.linear.app/mcp.',
  'Disable the GitHub MCP server.',
];
const mcpQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: 'mcp-create',
  prompts: mcpQuickPrompts,
};
const taskQuickPrompts = [
  'Add a task to fix the slow /health check.',
  'Create a backlog item to write onboarding docs.',
  'New task: investigate the flaky test.',
];
const taskQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: 'task-create',
  prompts: taskQuickPrompts,
};
const knowledgeQuickPrompts = [
  'Create a knowledge base called Release Notes for changelog snippets, tagged release.',
  'Add a knowledge base named Runbooks with the tags ops, oncall.',
  'Create a knowledge base "Research Notes" and add the folder /data/research as a source.',
];
// Looks like the default quick chat (icon + header, no descriptive text block) —
// just seeded with knowledge-create example prompts.
const knowledgeQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: 'knowledge-create',
  prompts: knowledgeQuickPrompts,
};

const profileQuickPrompts = [
  'Add an Azure OpenAI model profile named fast-chat using deployment gpt-5-mini.',
  'Create a model profile named deep-reasoning with high reasoning effort.',
  'Add a profile for deployment gpt-5.3-chat and enable reasoning summaries.',
];
const profileQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: 'profile-create',
  title: 'Add a model profile',
  placeholder: 'Describe the model profile you want...',
  prompts: profileQuickPrompts,
};

const inboxQuickPrompts = [
  "Summarize this week's repo activity and send it to my inbox.",
  'Write me an inbox message with your current priorities and blockers.',
  'Research LangGraph checkpointing best practices and send me a digest.',
];
const inboxQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: 'inbox-compose',
  title: 'Compose inbox message',
  placeholder: 'What should the message cover?',
  prompts: inboxQuickPrompts,
};
const inboxEditQuickPrompts = [
  'Tighten this message into a few bullet points.',
  'Expand it with more detail and supporting links.',
  'Rewrite the title so it says what changed at a glance.',
];
const inboxEditQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: 'inbox-edit',
  title: 'Edit inbox message',
  placeholder: 'How should this message change?',
  prompts: inboxEditQuickPrompts,
};

const workflowQuickPrompts = [
  'Build a workflow that posts a "say to user" message whenever a kanban card moves to Done.',
  'When a kanban card is created, run a prompt node that drafts a summary and messages me.',
  'Notify me with a warning-level notification titled "Card status changed" whenever a card moves from Backlog to Plan.',
];
const workflowQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: 'workflow-create',
  title: 'Build a workflow',
  placeholder: 'Describe the workflow you want...',
  prompts: workflowQuickPrompts,
};

const routineQuickPrompts = [
  'Create a new routine that researches a question, evaluates the findings, and writes a concise answer.',
  'Create a new routine that processes each item in a list, summarizes them, and combines the summaries into a final report.',
  'Modify this routine to add a quality-check step after the final report and revise the report when it finds missing details.',
  'Modify this routine to add a human approval step before saving or sending the final report.',
];
const routineQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: 'routine-create',
  title: 'Build a routine',
  placeholder: 'Describe the routine you want...',
  prompts: routineQuickPrompts,
};


const QUICK_CHAT_CONFIGS_BY_MODE = {
  'schedule-create': scheduleQuickChatConfig,
  'mcp-create': mcpQuickChatConfig,
  'task-create': taskQuickChatConfig,
  'knowledge-create': knowledgeQuickChatConfig,
  'profile-create': profileQuickChatConfig,
  'inbox-compose': inboxQuickChatConfig,
  'inbox-edit': inboxEditQuickChatConfig,
  'workflow-create': workflowQuickChatConfig,
  'routine-create': routineQuickChatConfig,
};

export function normalizeQuickChatConfig(config) {
  const candidate = config && typeof config === 'object' ? config : {};
  const baseConfig = QUICK_CHAT_CONFIGS_BY_MODE[candidate.mode] || defaultQuickChatConfig;
  const prompts = Array.isArray(candidate.prompts)
    ? candidate.prompts.filter((prompt) => typeof prompt === 'string' && prompt.trim())
    : [];
  return {
    ...baseConfig,
    ...candidate,
    prompts: prompts.length ? prompts : baseConfig.prompts,
  };
}

export function normalizeAiPrompt(value) {
  return typeof value === 'string' ? value.trim() : '';
}
