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
  'Add a task to wire the Tasks page to the live event stream, owner me, high priority.',
  'Create a backlog task: investigate the slow /health check, label backend.',
  'New task in build: ship the kanban tool, assign builder_progenitor.',
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

export function normalizeQuickChatConfig(config) {
  const candidate = config && typeof config === 'object' ? config : {};
  const baseConfig = candidate.mode === 'schedule-create'
    ? scheduleQuickChatConfig
    : candidate.mode === 'mcp-create'
      ? mcpQuickChatConfig
      : candidate.mode === 'task-create'
        ? taskQuickChatConfig
        : candidate.mode === 'knowledge-create'
          ? knowledgeQuickChatConfig
          : defaultQuickChatConfig;
  const prompts = Array.isArray(candidate.prompts)
    ? candidate.prompts.filter((prompt) => typeof prompt === 'string' && prompt.trim())
    : [];
  return {
    ...baseConfig,
    ...candidate,
    prompts: prompts.length ? prompts : baseConfig.prompts,
  };
}

export function buildQuickChatOutboundMessage(mode, text) {
  if (mode === 'mcp-create') {
    return [
      'Connect a new MCP server from the following user request.',
      'Follow the managing-mcp skill: read /state/mcp.json, then add an entry under',
      'mcpServers with the server name, url (HTTP only), any auth headers, and',
      'enabled: true. Saving the file persists it and discovers its tools immediately.',
      'Only ask a follow-up question if the endpoint URL is missing; otherwise add it.',
      'Reply with one short sentence confirming the connected server name and endpoint.',
      '',
      `User request: ${text}`,
    ].join('\n');
  }
  if (mode === 'task-create') {
    return [
      'Create a new task on the kanban board from the following user request.',
      'Write a new file /state/tasks/<id>.md where <id> is a unique slug',
      '(e.g. card_<short-hex>). The file is YAML frontmatter + a markdown body:',
      'frontmatter keys: title (concise), column_id (default "backlog"),',
      'status (default "draft"), owner (default "user"), and any agent_name plus',
      'free-form labels (tags) the request implies; the markdown body is the task',
      'description. Writing the file persists the task and the Tasks board updates',
      'live (board.json tracks the card order automatically).',
      'Only ask a follow-up question if the task title is unclear; otherwise create it.',
      'Reply with one short sentence confirming the created task title and column.',
      '',
      `User request: ${text}`,
    ].join('\n');
  }
  if (mode === 'knowledge-create') {
    return [
      'Create a new knowledge base from the following user request.',
      'Follow the managing-knowledge skill: read /state/knowledge.json, then append',
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
  if (mode !== 'schedule-create') {
    return text;
  }
  return [
    'Create a new schedule from the following user request.',
    'Use the schedule_task tool.',
    'If the request is one-off, provide scheduled_at.',
    'If the request is recurring, provide cron_expression.',
    'Choose a concise schedule name and preserve the requested intent as the instruction.',
    'Only ask a follow-up question if critical schedule details are missing; otherwise create it.',
    'Reply with one short sentence confirming the created schedule name and timing.',
    '',
    `User request: ${text}`,
  ].join('\n');
}
