import { createSelector } from '@reduxjs/toolkit';
import {
  selectAgentProfiles,
  selectInboxMessages,
  selectKanbanBoard,
  selectKnowledgeBases,
  selectMcpServers,
  selectPrompts,
  selectRepos,
} from '../uiDataSlice';

// Routines are graph agent profiles (kind "routine"); runs resolve the
// current persisted graph, so every listed routine is selectable.
const selectRoutineProfiles = createSelector(
  [selectAgentProfiles],
  (profiles) => (profiles || []).filter((profile) => profile.kind === 'routine'),
);
/**
 * Whitelisted Redux collections exposed to workflow prompt pickers.
 * Picker schemas name one of these keys; they never carry a URL, query, or
 * arbitrary state path.
 */
export const WORKFLOW_REFERENCE_SOURCES = {
  agent: {
    referenceKey: 'agent_id',
    select: selectAgentProfiles,
    toOption: (item) => ({ value: item.id, label: item.name || item.id, description: item.description || '' }),
  },
  prompt: {
    referenceKey: 'prompt_path',
    select: selectPrompts,
    toOption: (item) => ({ value: item.path, label: item.title || item.path, description: item.description || '' }),
  },
  repository: {
    referenceKey: 'repository_id',
    select: selectRepos,
    toOption: (item) => ({ value: item.id, label: item.name || item.id, description: item.path || '' }),
  },
  knowledge_base: {
    referenceKey: 'knowledge_base_id',
    select: selectKnowledgeBases,
    toOption: (item) => ({ value: item.id, label: item.name || item.id, description: item.description || '' }),
  },
  inbox_message: {
    referenceKey: 'inbox_message_id',
    select: selectInboxMessages,
    toOption: (item) => ({ value: item.id, label: item.subject || item.title || item.id, description: item.preview || '' }),
  },
  kanban_card: {
    referenceKey: 'kanban_card_id',
    select: selectKanbanBoard,
    toOptions: (board) => (board?.cards || []).map((card) => ({
      value: card.id,
      label: card.title || card.id,
      description: card.column_id || '',
    })),
  },
  mcp_server: {
    select: selectMcpServers,
    toOption: (item) => ({ value: item.id, label: item.name || item.id, description: item.description || '' }),
  },
  routine: {
    referenceKey: 'routine',
    select: selectRoutineProfiles,
    toOption: (item) => ({ value: item.id, label: item.name || item.id, description: item.description || '' }),
  },
};

/** Declarative picker forms for the schema-supported scalar references. */
export const WORKFLOW_REFERENCE_FIELDS = [
  { source: 'prompt', label: 'Saved prompt', key: 'prompt_path' },
  { source: 'agent', label: 'Agent profile', key: 'agent_id' },
  { source: 'repository', label: 'Repository', key: 'repository_id' },
  { source: 'knowledge_base', label: 'Knowledge base', key: 'knowledge_base_id' },
  { source: 'inbox_message', label: 'Inbox message', key: 'inbox_message_id' },
];

export function workflowReferenceOptions(state, source) {
  const definition = WORKFLOW_REFERENCE_SOURCES[source];
  if (!definition) return [];
  const items = definition.select(state);
  if (definition.toOptions) return definition.toOptions(items);
  return (items || []).map(definition.toOption);
}
