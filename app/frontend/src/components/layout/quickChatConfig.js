// Quick-chat panel configuration and the outbound-message wrapping that turns a
// quick-chat submission into a skill-specific instruction. Pure data + helpers,
// extracted from AppRuntimeShell so the panel component stays focused on UI.

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
// just seeded with example prompts and the knowledge-create outbound wrapping.
const knowledgeQuickChatConfig = {
  ...defaultQuickChatConfig,
  mode: 'knowledge-create',
  prompts: knowledgeQuickPrompts,
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


const QUICK_CHAT_CONFIGS_BY_MODE = {
  'schedule-create': scheduleQuickChatConfig,
  'mcp-create': mcpQuickChatConfig,
  'task-create': taskQuickChatConfig,
  'knowledge-create': knowledgeQuickChatConfig,
  'inbox-compose': inboxQuickChatConfig,
  'inbox-edit': inboxEditQuickChatConfig,
  'workflow-create': workflowQuickChatConfig,
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

export function buildQuickChatOutboundMessage(mode, text, config = {}) {
  if (mode === 'mcp-create') {
    return [
      'Connect a new MCP server from the following user request.',
      'Follow the managing-mcp skill: read /config/mcp.json, then add an entry under',
      'mcpServers with the server name, url (HTTP only), any auth headers, and',
      'enabled: true. Saving the file persists it and discovers its tools immediately.',
      'Only ask a follow-up question if the endpoint URL is missing; otherwise add it.',
      'Reply with one short sentence confirming the connected server name and endpoint.',
      '',
      `User request: ${text}`,
    ].join('\n');
  }
  if (mode === 'task-create') {
    const projectLine = config.projectId
      ? `This task belongs to the "${config.projectName || config.projectId}" project, so its directory IS the project: write it to /config/tasks/${config.projectId}/<id>.md (not the top-level /config/tasks/<id>.md).`
      : null;
    return [
      'Create a new task on the kanban board from the following user request.',
      'Follow the managing-tasks skill: write /config/tasks/<id>.md (a unique',
      'card_<short-hex> slug) with a concise title, a real one-sentence summary,',
      'and any column_id, status, owner, or labels the request implies. Use the',
      'exact frontmatter field names from the skill (column_id, not column).',
      'Saving the file persists the task and the Tasks board updates live.',
      ...(projectLine ? [projectLine] : []),
      'Only ask a follow-up question if the task title is unclear; otherwise create it.',
      'Reply with one short sentence confirming the created task title and column.',
      '',
      `User request: ${text}`,
    ].join('\n');
  }
  if (mode === 'knowledge-create') {
    return [
      'Create a new knowledge base from the following user request.',
      'Follow the managing-knowledge skill: read /config/knowledge.json, then append',
      'an object to the bases array with at least a name (omit id — it is generated),',
      'plus any description, tags, or folder source paths the request implies.',
      'Saving the file persists it and the Knowledge page updates live; folder',
      'sources still need indexing from the Knowledge page UI.',
      'Only ask a follow-up question if the base name is unclear; otherwise create it.',
      'Reply with one short sentence confirming the created knowledge base name.',
      '',
      `User request: ${text}`,
    ].join('\n');
  }
  if (mode === 'inbox-compose') {
    return [
      'Compose a new inbox message for the user from the following request.',
      'Follow the managing-inbox skill: generate an id (prefix=msg), then write',
      '/inbox/<id>.md with a specific title in the YAML frontmatter and the full',
      'content as a markdown body (headings, lists, and links all render).',
      'Leave read/read_at/created_at alone — they are stamped automatically.',
      'Writing the file delivers it; the Inbox page updates live.',
      'Only ask a follow-up question if the requested content is unclear; otherwise send it.',
      'Reply with one short sentence confirming the message title you sent.',
      '',
      `User request: ${text}`,
    ].join('\n');
  }
  if (mode === 'inbox-edit') {
    const messagePath = config.messageId ? `/inbox/${config.messageId}.md` : '/inbox/<id>.md';
    const titleLine = config.messageTitle
      ? `The message is currently titled "${config.messageTitle}".`
      : null;
    return [
      `Edit the existing inbox message ${messagePath} per the following request.`,
      ...(titleLine ? [titleLine] : []),
      'Follow the managing-inbox skill: read the file first, then use edit_file to',
      'change only the title/body text the request calls for, preserving the',
      'read, read_at, and created_at frontmatter fields exactly as they are.',
      'Saving the file updates it; the Inbox page reflects the change live.',
      'Only ask a follow-up question if the requested change is unclear; otherwise make it.',
      'Reply with one short sentence confirming what changed.',
      '',
      `User request: ${text}`,
    ].join('\n');
  }
  if (mode === 'workflow-create') {
    return [
      'Build a new backend-native workflow from the following user request.',
      'Follow the managing-workflows skill: read /schemas/definition.workflow.schema.v1.json',
      'and /schemas/node_prompt.workflow.schema.v1.json, generate ids for the workflow',
      'and its nodes, then use structured_output to produce a valid definition — one',
      'event node, one trigger.event_type from the kanban.card.* set, and at least',
      'one prompt, user, or code node wired to it with an edge. If the request names',
      'specific kanban columns for a card move (e.g. "from Backlog to Plan"), use',
      'trigger.event_type "kanban.card.moved" plus trigger.from_column/to_column',
      '(backlog | plan | build | validate | done) to filter to that transition.',
      'Write it to /workflows/<id>.json. Saving the file persists it and the',
      'Workflows page updates live.',
      'Only ask a follow-up question if the trigger event or the desired action is',
      'unclear; otherwise build it.',
      'Reply with one short sentence confirming the created workflow title and trigger.',
      '',
      `User request: ${text}`,
    ].join('\n');
  }
  if (mode !== 'schedule-create') {
    return text;
  }
  return [
    'Create a new schedule from the following user request.',
    'Follow the managing-schedules skill: generate an id (prefix=cron for a',
    'one-off, prefix=sched for recurring), use current_time to resolve the',
    'timing, then add an entry to /config/schedule.json keyed by that id with a',
    'message and exactly one of cron_expression (recurring) or scheduled_at',
    '(one-off). Saving the file persists it and the Schedules page updates live.',
    'Only ask a follow-up question if critical schedule details are missing; otherwise create it.',
    'Reply with one short sentence confirming the created schedule name and timing.',
    '',
    `User request: ${text}`,
  ].join('\n');
}
