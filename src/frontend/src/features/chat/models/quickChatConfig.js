import { QUICK_CHAT_MODE } from '../../../lib/browserContracts';

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
  mode: QUICK_CHAT_MODE.SCHEDULE_CREATE,
  prompts: scheduleQuickPrompts,
};
const mcpQuickPrompts = [
  'Connect the GitHub MCP server at https://api.githubcopilot.com/mcp/.',
  'Connect our Linear MCP server at https://mcp.linear.src/mcp.',
  'Disable the GitHub MCP server.',
];
const mcpQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: QUICK_CHAT_MODE.MCP_CREATE,
  prompts: mcpQuickPrompts,
};
const taskQuickPrompts = [
  'Add a task to fix the slow /health check.',
  'Create a backlog item to write onboarding docs.',
  'New task: investigate the flaky test.',
];
const taskQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: QUICK_CHAT_MODE.TASK_CREATE,
  prompts: taskQuickPrompts,
};
const knowledgeQuickPrompts = [
  'Create a knowledge base called Release Notes for changelog snippets, tagged release.',
  'Add a knowledge base named Runbooks with the tags ops, oncall.',
  'Create a knowledge base "Research Notes" for durable findings, tagged research.',
];
// Looks like the default quick chat (icon + header, no descriptive text block) —
// just seeded with knowledge-create example prompts.
const knowledgeQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: QUICK_CHAT_MODE.KNOWLEDGE_CREATE,
  prompts: knowledgeQuickPrompts,
};

const profileQuickPrompts = [
  'Add an Azure OpenAI model profile named fast-chat using deployment gpt-5-mini.',
  'Create a model profile named deep-reasoning with high reasoning effort.',
  'Add a profile for deployment gpt-5.3-chat and enable reasoning summaries.',
];
const profileQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: QUICK_CHAT_MODE.PROFILE_CREATE,
  title: 'Add a model profile',
  placeholder: 'Describe the model profile you want...',
  prompts: profileQuickPrompts,
};

const repositoryQuickPrompts = [
  'Add a repository named payments-api that clones github.com/acme/payments-api with read/write access.',
  'Track the local checkout at /home/me/projects/frontend as a read-only repository called frontend.',
  'Add a repository for github.com/acme/infra, owned by Platform, cloned into infra.',
];
const repositoryQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: QUICK_CHAT_MODE.REPOSITORY_CREATE,
  title: 'Add a repository',
  placeholder: 'Describe the repository to add...',
  prompts: repositoryQuickPrompts,
};

const inboxQuickPrompts = [
  "Summarize this week's repo activity and send it to my inbox.",
  'Write me an inbox message with your current priorities and blockers.',
  'Research LangGraph checkpointing best practices and send me a digest.',
];
const inboxQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: QUICK_CHAT_MODE.INBOX_COMPOSE,
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
  mode: QUICK_CHAT_MODE.INBOX_EDIT,
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
  mode: QUICK_CHAT_MODE.WORKFLOW_CREATE,
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
  mode: QUICK_CHAT_MODE.ROUTINE_CREATE,
  title: 'Build a routine',
  placeholder: 'Describe the routine you want...',
  prompts: routineQuickPrompts,
};

const instructionQuickPrompts = [
  'Create a workflow instruction that summarises a kanban card for the team, with the card as a typed input.',
  'Create an instruction that turns an inbox message into a short reply draft.',
  'Create an instruction that extracts action items from a chat transcript as a JSON list.',
];
const instructionQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: QUICK_CHAT_MODE.INSTRUCTION_CREATE,
  title: 'Create an instruction',
  placeholder: 'Describe the instruction you want...',
  prompts: instructionQuickPrompts,
};

const QUICK_CHAT_CONFIGS_BY_MODE = {
  [QUICK_CHAT_MODE.SCHEDULE_CREATE]: scheduleQuickChatConfig,
  [QUICK_CHAT_MODE.MCP_CREATE]: mcpQuickChatConfig,
  [QUICK_CHAT_MODE.TASK_CREATE]: taskQuickChatConfig,
  [QUICK_CHAT_MODE.KNOWLEDGE_CREATE]: knowledgeQuickChatConfig,
  [QUICK_CHAT_MODE.PROFILE_CREATE]: profileQuickChatConfig,
  [QUICK_CHAT_MODE.REPOSITORY_CREATE]: repositoryQuickChatConfig,
  [QUICK_CHAT_MODE.INBOX_COMPOSE]: inboxQuickChatConfig,
  [QUICK_CHAT_MODE.INBOX_EDIT]: inboxEditQuickChatConfig,
  [QUICK_CHAT_MODE.WORKFLOW_CREATE]: workflowQuickChatConfig,
  [QUICK_CHAT_MODE.ROUTINE_CREATE]: routineQuickChatConfig,
  [QUICK_CHAT_MODE.INSTRUCTION_CREATE]: instructionQuickChatConfig,
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
