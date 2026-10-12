import { describe, it, expect } from 'vitest';
import reducer, {
  applyUiEvent,
  clearChatErrored,
  clearChatGenerating,
  clearChatUnread,
  clearActiveChatSession,
  deleteChatSession,
  ensureDraftChatSession,
  kanbanSnapshotReceived,
  markChatErrored,
  markChatGenerating,
  markChatUnread,
  requestAgentDetail,
  requestChatSessionDetail,
  requestChatSessionReconciliation,
  updateChatSessionMetadata,
  selectActiveAgentProfile,
  selectRoutineLatestRunStatusByAgentId,
  selectActiveChatSessionState,
  selectAgentProfiles,
  selectChatIndicators,
  selectChatReconciliationState,
  selectErroredChatIds,
  selectGeneratingChatIds,
  selectRequestIdByThreadId,
  selectRequestIdForThread,
  selectChatSessions,
  selectKanbanBoard,
  selectPromptContentEntry,
  selectTaskActivityEntry,
  selectTaskContentEntry,
  selectUnreadChatIds,
  fetchInbox,
  fetchSchedules,
  selectSchedulesState,
  selectWorkflowDetailByPath,
  selectWorkflowLatestRunById,
  selectWorkflowRuns,
  selectWorkflows,
  selectWorkflowsState,
  selectInboxMessages,
  selectInboxState,
  selectInboxUnreadCount,
  selectMcpServers,
  selectMcpServerDetail,
  selectMcpState,
  selectSchedules,
  selectUnseenNavRoutes,
  setActiveNavRoute,
  scheduleSnapshotReceived,
  setErroredChatIds,
  setGeneratingChatIds,
  setUnreadChatIds,
} from './uiDataSlice';
import { CHAT_STATUS } from '../lib/chatStatus';

const snapshot = (ids) => applyUiEvent({
  type: 'chat.sessions.snapshot',
  payload: { sessions: ids.map((id) => ({ thread_id: id })) },
});

describe('applyUiEvent reducer', () => {
  it('snapshot replaces the chat list and marks loaded', () => {
    const s = reducer(undefined, snapshot(['a', 'b']));
    expect(s.chats.order).toEqual(['a', 'b']);
    expect(s.chats.loaded).toBe(true);
  });

  it('created prepends a new chat', () => {
    let s = reducer(undefined, snapshot(['a']));
    s = reducer(s, applyUiEvent({ type: 'chat.session.created', payload: { session: { thread_id: 'b', title: 'B' } } }));
    expect(s.chats.order).toEqual(['b', 'a']);
    expect(s.chats.byId.b.title).toBe('B');
  });

  it('upserted adds only when missing and does not duplicate', () => {
    let s = reducer(undefined, snapshot(['a']));
    s = reducer(s, applyUiEvent({ type: 'chat.session.upserted', payload: { session: { thread_id: 'a', title: 'changed' } } }));
    expect(s.chats.order).toEqual(['a']);
    s = reducer(s, applyUiEvent({ type: 'chat.session.upserted', payload: { session: { thread_id: 'c' } } }));
    expect(s.chats.order).toEqual(['c', 'a']);
  });

  it('deleted removes the chat and clears the active detail if it was selected', () => {
    let s = reducer(undefined, snapshot(['a', 'b']));
    s = reducer(s, applyUiEvent({ type: 'chat.session.deleted', payload: { thread_id: 'a' } }));
    expect(s.chats.order).toEqual(['b']);
    expect(s.chats.byId.a).toBeUndefined();
  });

  it('deleteChatSession.fulfilled acknowledges without changing chats until chat.session.deleted arrives', () => {
    let s = reducer(undefined, snapshot(['a', 'b']));
    s = reducer(s, deleteChatSession.fulfilled({
      request_id: 'REQ1',
      event_types: ['chat.session.deleted', 'chat.sessions.snapshot'],
    }, 'REQ1', {
      chatsUrl: '/x',
      threadId: 'a',
    }));
    expect(s.chats.order).toEqual(['a', 'b']);
    expect(s.chats.byId.a).toBeDefined();

    s = reducer(s, applyUiEvent({
      type: 'chat.session.deleted',
      payload: { thread_id: 'a', request_id: 'REQ1' },
    }));
    expect(s.chats.order).toEqual(['b']);
    expect(s.chats.byId.a).toBeUndefined();
  });

  it('updateChatSessionMetadata.fulfilled acknowledges without changing archive state until chat.session.metadata.updated arrives', () => {
    let s = reducer(undefined, snapshot(['a', 'b']));
    s = reducer(s, updateChatSessionMetadata.fulfilled({
      request_id: 'REQ1',
      event_types: ['chat.session.metadata.updated', 'chat.sessions.snapshot'],
    }, 'REQ1', {
      chatsUrl: '/x',
      threadId: 'a',
      patch: { archived: true },
    }));
    expect(s.chats.byId.a.archived).toBeUndefined();

    s = reducer(s, applyUiEvent({
      type: 'chat.session.metadata.updated',
      payload: {
        request_id: 'REQ1',
        session: { thread_id: 'a', title: 'A', archived: true },
      },
    }));
    expect(s.chats.byId.a.archived).toBe(true);
    expect(s.chats.order).toEqual(['a', 'b']);
  });

  it('updateChatSessionMetadata.fulfilled waits for the canonical snapshot to reorder a touched chat', () => {
    let s = reducer(undefined, snapshot(['a', 'b', 'c']));
    s = reducer(s, updateChatSessionMetadata.fulfilled({
      request_id: 'REQ1',
      event_types: ['chat.session.metadata.updated', 'chat.sessions.snapshot'],
    }, 'REQ1', {
      chatsUrl: '/x',
      threadId: 'c',
      patch: { title: 'New chat', touch: true },
    }));
    expect(s.chats.order).toEqual(['a', 'b', 'c']);

    s = reducer(s, applyUiEvent({
      type: 'chat.sessions.snapshot',
      payload: {
        request_id: 'REQ1',
        sessions: [
          { thread_id: 'c', title: 'New chat' },
          { thread_id: 'a' },
          { thread_id: 'b' },
        ],
      },
    }));
    expect(s.chats.order).toEqual(['c', 'a', 'b']);
    expect(s.chats.byId.c.title).toBe('New chat');
  });

  it('ensureDraftChatSession.fulfilled waits for the canonical snapshot to reorder the draft', () => {
    let s = reducer(undefined, snapshot(['a', 'b', 'c']));
    s = reducer(s, ensureDraftChatSession.fulfilled({
      request_id: 'REQ1',
      event_types: ['chat.session.metadata.updated', 'chat.sessions.snapshot'],
    }, 'REQ1', {
      chatsUrl: '/x',
      threadId: 'b',
    }));
    expect(s.chats.order).toEqual(['a', 'b', 'c']);

    s = reducer(s, applyUiEvent({
      type: 'chat.sessions.snapshot',
      payload: {
        request_id: 'REQ1',
        sessions: [
          { thread_id: 'b', title: 'New chat', message_count: 0 },
          { thread_id: 'a' },
          { thread_id: 'c' },
        ],
      },
    }));
    expect(s.chats.order).toEqual(['b', 'a', 'c']);
    expect(s.chats.byId.b.title).toBe('New chat');
  });

  it('selected applies matching detail with messages and preserves request identity for hydration', () => {
    const existingMessages = [
      { role: 'user', content: 'Continue the existing chat' },
      { role: 'assistant', content: 'Here is the next step.' },
    ];
    let s = reducer(undefined, requestChatSessionDetail.pending('REQ1', {
      chatsUrl: '/x', threadId: 'a', requestId: 'REQ1',
    }));
    const pendingActive = s.chats.active;

    // A selected event from another request must not replace the pending detail.
    s = reducer(s, applyUiEvent({
      type: 'chat.session.selected',
      payload: {
        session: { thread_id: 'a', title: 'Stale selection' },
        request_id: 'REQ2',
      },
    }));
    expect(s.chats.active).toEqual(pendingActive);

    // The matching stream event supplies the existing messages. Its request
    // identity remains available for ConversationPage's hydration guard.
    s = reducer(s, applyUiEvent({
      type: 'chat.session.selected',
      payload: {
        session: { thread_id: 'a', title: 'Existing chat', messages: existingMessages },
        request_id: 'REQ1',
      },
    }));
    expect(s.chats.active.detail.messages).toEqual(existingMessages);
    expect(s.chats.active.requestId).toBe('REQ1');
    expect(s.chats.active.threadId).toBe('a');
    expect(s.chats.active.loading).toBe(false);
  });

  it('reconciles only the matching request without replacing or loading the active chat', () => {
    const activeMessages = [
      { role: 'user', content: 'Continue the existing chat.' },
      { role: 'assistant', content: 'The locally committed reply remains visible.' },
    ];
    let s = reducer(undefined, requestChatSessionDetail.pending('ACTIVE1', {
      chatsUrl: '/x', threadId: 'a', requestId: 'ACTIVE1',
    }));
    s = reducer(s, applyUiEvent({
      type: 'chat.session.selected',
      payload: {
        request_id: 'ACTIVE1',
        session: { thread_id: 'a', messages: activeMessages },
      },
    }));
    const activeBeforeReconciliation = s.chats.active;

    s = reducer(s, requestChatSessionReconciliation.pending('REC1', {
      chatsUrl: '/x', threadId: 'a', requestId: 'REC1',
    }));
    expect(selectChatReconciliationState({ uiData: s })).toMatchObject({
      threadId: 'a',
      detail: null,
      requestId: 'REC1',
    });
    expect(s.chats.active).toEqual(activeBeforeReconciliation);
    expect(s.chats.active.loading).toBe(false);

    s = reducer(s, applyUiEvent({
      type: 'chat.session.selected',
      payload: {
        request_id: 'STALE1',
        session: { thread_id: 'a', messages: [{ role: 'assistant', content: 'Stale reply.' }] },
      },
    }));
    expect(selectChatReconciliationState({ uiData: s }).detail).toBeNull();
    expect(s.chats.active).toEqual(activeBeforeReconciliation);
    expect(s.chats.active.loading).toBe(false);

    const reconciledMessages = [
      ...activeMessages,
      { role: 'assistant', content: 'Persisted metadata is now available.' },
    ];
    s = reducer(s, applyUiEvent({
      type: 'chat.session.selected',
      payload: {
        request_id: 'REC1',
        session: { thread_id: 'a', messages: reconciledMessages },
      },
    }));
    expect(selectChatReconciliationState({ uiData: s })).toMatchObject({
      threadId: 'a',
      detail: { thread_id: 'a', messages: reconciledMessages },
      requestId: 'REC1',
    });
    expect(s.chats.active).toEqual(activeBeforeReconciliation);
    expect(s.chats.active.loading).toBe(false);
  });

  it('uses the started-response registry to mark a background chat unread and errored when failure omits thread_id', () => {
    let s = reducer(undefined, applyUiEvent({
      type: 'chat.response.started',
      payload: {
        request_id: 'REQ_STREAM',
        thread_id: 'thread-background',
      },
    }));

    expect(s.chatRuntime.responseThreadsByRequestId).toEqual({ REQ_STREAM: 'thread-background' });
    expect(selectGeneratingChatIds({ uiData: s })).toEqual(['thread-background']);

    s = reducer(s, applyUiEvent({
      type: 'chat.response.failed',
      payload: {
        request_id: 'REQ_STREAM',
      },
    }));

    expect(s.chatRuntime.responseThreadsByRequestId).toEqual({});
    expect(selectGeneratingChatIds({ uiData: s })).toEqual([]);
    expect(selectUnreadChatIds({ uiData: s })).toEqual(['thread-background']);
    expect(selectErroredChatIds({ uiData: s })).toEqual(['thread-background']);
  });

  it('tracks generatingChatIds and requestIdByThreadId for the currently-viewed thread too', () => {
    // A generation another surface (quick chat, another tab) started into the
    // thread this tab already has selected as active must still be
    // discoverable, so a spectator can attach to it.
    let s = reducer(undefined, requestChatSessionDetail.pending('rid', {
      chatsUrl: '/x', threadId: 'thread-active', requestId: 'REQ1',
    }));
    s = reducer(s, applyUiEvent({
      type: 'chat.session.selected',
      payload: { session: { thread_id: 'thread-active' }, request_id: 'REQ1' },
    }));

    s = reducer(s, applyUiEvent({
      type: 'chat.response.started',
      payload: { request_id: 'REQ_OTHER', thread_id: 'thread-active' },
    }));
    expect(selectGeneratingChatIds({ uiData: s })).toEqual(['thread-active']);
    expect(selectRequestIdForThread({ uiData: s }, 'thread-active')).toBe('REQ_OTHER');
    expect(selectRequestIdByThreadId({ uiData: s })).toEqual({ 'thread-active': 'REQ_OTHER' });

    s = reducer(s, applyUiEvent({
      type: 'chat.response.completed',
      payload: { request_id: 'REQ_OTHER' },
    }));
    expect(selectGeneratingChatIds({ uiData: s })).toEqual([]);
    expect(selectRequestIdForThread({ uiData: s }, 'thread-active')).toBeNull();
  });

  it('seeds requestIdByThreadId from active_request_id on chat.session.selected (late-join discovery)', () => {
    let s = reducer(undefined, applyUiEvent({
      type: 'chat.session.selected',
      payload: {
        request_id: 'REQ1',
        session: { thread_id: 'thread-late', active_request_id: 'REQ_INFLIGHT' },
      },
    }));
    expect(selectRequestIdForThread({ uiData: s }, 'thread-late')).toBe('REQ_INFLIGHT');
    expect(selectGeneratingChatIds({ uiData: s })).toEqual(['thread-late']);

    // A later selection with no active_request_id (generation already
    // finished by the time this fetch resolved) clears the stale mapping.
    s = reducer(s, applyUiEvent({
      type: 'chat.session.selected',
      payload: {
        request_id: 'REQ2',
        session: { thread_id: 'thread-late', active_request_id: null },
      },
    }));
    expect(selectRequestIdForThread({ uiData: s }, 'thread-late')).toBeNull();
  });

  it('clearActiveChatSession resets the active backend selection', () => {
    let s = reducer(undefined, requestChatSessionDetail.pending('rid', {
      chatsUrl: '/x', threadId: 'a', requestId: 'REQ1',
    }));
    s = reducer(s, applyUiEvent({ type: 'chat.session.selected', payload: { session: { thread_id: 'a' }, request_id: 'REQ1' } }));
    s = reducer(s, clearActiveChatSession());
    expect(s.chats.active).toEqual({
      threadId: null,
      detail: null,
      loading: false,
      error: null,
      requestId: null,
      hasMoreMessages: false,
      pendingOlderPage: null,
    });
  });

  it('schedule.snapshot replaces the schedules list', () => {
    const items = [{ id: 'sched-a', name: 'A' }, { id: 'sched-b', name: 'B', paused: true }];
    const s = reducer(undefined, applyUiEvent({ type: 'schedule.snapshot', payload: { items } }));
    expect(selectSchedules({ uiData: s })).toEqual(items);
  });

  it('status.updated merges into existing status (partial metric pushes)', () => {
    let s = reducer(undefined, applyUiEvent({
      type: 'status.updated',
      payload: { progenitor_running: true, agents: [{ name: 'a' }], memory_mb: 100 },
    }));
    // A later partial push (e.g. the process-metrics tick) updates only its
    // fields and preserves the rest.
    s = reducer(s, applyUiEvent({ type: 'status.updated', payload: { memory_mb: 142 } }));
    expect(s.status.memory_mb).toBe(142);
    expect(s.status.progenitor_running).toBe(true);
    expect(s.status.agents).toEqual([{ name: 'a' }]);
  });

  it('mcp.snapshot replaces the MCP server list and marks it loaded', () => {
    const items = [
      { id: 'mcp:jina', type: 'mcp', name: 'jina' },
      { id: 'mcp:linear', type: 'mcp', name: 'linear' },
    ];
    const s = reducer(undefined, applyUiEvent({ type: 'mcp.snapshot', payload: { items } }));
    expect(selectMcpServers({ uiData: s })).toEqual(items);
    expect(selectMcpState({ uiData: s }).loaded).toBe(true);
    expect(selectMcpState({ uiData: s }).error).toBe(null);
  });

  it('mcp.server.detail.updated caches one server\'s full body (tools + logs) by id', () => {
    const detail = {
      id: 'mcp:jina',
      type: 'mcp',
      name: 'jina',
      data: { tools: [{ name: 'search', enabled: true }], logs: [{ message: 'connected' }] },
    };
    const s = reducer(undefined, applyUiEvent({ type: 'mcp.server.detail.updated', payload: detail }));
    expect(selectMcpServerDetail({ uiData: s }, 'mcp:jina')).toEqual(detail);
    expect(selectMcpServerDetail({ uiData: s }, 'mcp:other')).toBe(null);
  });

  it('kanban.snapshot replaces the live tasks board', () => {
    const board = {
      id: 'progenitor-kanban',
      title: 'Tasks',
      updated_at: '2026-06-23T00:00:00Z',
      columns: [{ id: 'backlog', label: 'Backlog' }],
      cards: [{ id: 'card_1', title: 'Ship Tasks', column_id: 'backlog', status: 'idle' }],
      card_schema: { statuses: [{ id: 'idle', label: 'Idle' }] },
    };
    const s = reducer(undefined, applyUiEvent({ type: 'kanban.snapshot', payload: board }));
    expect(selectKanbanBoard({ uiData: s })).toEqual(board);
  });

  it('task content and activity events update per-card detail state', () => {
    let s = reducer(undefined, applyUiEvent({
      type: 'task.content.updated',
      payload: { card_id: 'card_1', path: 'tasks/card_1.md', content: '# Ship Tasks' },
    }));
    s = reducer(s, applyUiEvent({
      type: 'task.activity.updated',
      payload: { card_id: 'card_1', items: [{ id: 'evt_1', message: 'Updated the task brief.' }], total: 1 },
    }));
    expect(selectTaskContentEntry({ uiData: s }, 'card_1')?.content).toBe('# Ship Tasks');
    expect(selectTaskActivityEntry({ uiData: s }, 'card_1')?.total).toBe(1);
  });

  it('prompt.content.updated caches the full body by path', () => {
    const s = reducer(undefined, applyUiEvent({
      type: 'prompt.content.updated',
      payload: { path: 'builder_agent/think.md', content: '# Think prompt' },
    }));
    expect(selectPromptContentEntry({ uiData: s }, 'builder_agent/think.md')?.content).toBe('# Think prompt');
    expect(selectPromptContentEntry({ uiData: s }, 'unknown.md')).toBeNull();
  });

  it('chat.session.selected caps hasMoreMessages and chat.messages.page.loaded prepends an older page', () => {
    let s = reducer(undefined, requestChatSessionDetail.pending('REQ1', {
      chatsUrl: '/x', threadId: 'a', requestId: 'REQ1',
    }));
    s = reducer(s, applyUiEvent({
      type: 'chat.session.selected',
      payload: {
        request_id: 'REQ1',
        session: { thread_id: 'a', messages: [{ id: 'm50', content: 'newest' }], has_more_messages: true },
      },
    }));
    expect(selectActiveChatSessionState({ uiData: s }).hasMoreMessages).toBe(true);
    expect(selectActiveChatSessionState({ uiData: s }).pendingOlderPage).toBeNull();

    s = reducer(s, applyUiEvent({
      type: 'chat.messages.page.loaded',
      payload: {
        thread_id: 'a',
        request_id: 'REQ2',
        messages: [{ id: 'm0', content: 'oldest' }],
        has_more: false,
      },
    }));
    expect(selectActiveChatSessionState({ uiData: s }).hasMoreMessages).toBe(false);
    expect(selectActiveChatSessionState({ uiData: s }).pendingOlderPage).toEqual({
      requestId: 'REQ2',
      messages: [{ id: 'm0', content: 'oldest' }],
    });
  });

  it('chat.messages.page.loaded is ignored for a thread that is not currently active', () => {
    let s = reducer(undefined, requestChatSessionDetail.pending('REQ1', {
      chatsUrl: '/x', threadId: 'a', requestId: 'REQ1',
    }));
    s = reducer(s, applyUiEvent({
      type: 'chat.session.selected',
      payload: { request_id: 'REQ1', session: { thread_id: 'a', messages: [] } },
    }));
    s = reducer(s, applyUiEvent({
      type: 'chat.messages.page.loaded',
      payload: { thread_id: 'b', messages: [{ id: 'm0' }], has_more: false },
    }));
    expect(selectActiveChatSessionState({ uiData: s }).pendingOlderPage).toBeNull();
  });

  it('document_types.snapshot replaces the document type list and marks loaded', () => {
    const items = [
      { id: 'kanban_card', label: 'Kanban Card', collection: true, document_schema: { type: 'object', properties: { title: { type: 'string' } } } },
      { id: 'inbox_message', label: 'Inbox Message', collection: false, document_schema: { type: 'object', properties: { subject: { type: 'string' } } } },
    ];
    const s = reducer(undefined, applyUiEvent({ type: 'document_types.snapshot', payload: { items } }));
    expect(s.documentTypes.items).toEqual(items);
    expect(s.documentTypes.loaded).toBe(true);
  });

  it('workflow snapshot/detail/run events populate the workflows domain', () => {
    let s = reducer(undefined, applyUiEvent({
      type: 'workflows.snapshot',
      payload: {
        items: [
          {
            id: 'wf_1',
            title: 'Card created',
            description: '',
            enabled: true,
            paused: false,
            version: 1,
            document_type: 'kanban_card',
            node_count: 2,
            updated_at: '2026-07-06T00:00:00Z',
            created_at: '2026-07-06T00:00:00Z',
          },
        ],
      },
    }));
    expect(selectWorkflows({ uiData: s })).toHaveLength(1);
    expect(selectWorkflowsState({ uiData: s }).loaded).toBe(true);

    s = reducer(s, applyUiEvent({
      type: 'workflow.updated',
      payload: {
        path: '/workflows/wf_1.json',
        workflow: {
          id: 'wf_1',
          title: 'Card created',
          description: '',
          enabled: true,
          paused: false,
          version: 1,
          document_type: 'kanban_card',
          graph: {
            nodes: {
              event_a: { kind: 'event', title: 'Event', document_type: 'kanban_card' },
              prompt_a: { kind: 'prompt', title: 'Prompt', prompt: 'Handle it', agent_name: 'progenitor', role: null },
            },
            edges: [{ from: { node: 'event_a', port: 'default' }, to: { node: 'prompt_a', port: 'default' } }],
          },
          ui: {},
          updated_at: '2026-07-06T00:00:00Z',
          created_at: '2026-07-06T00:00:00Z',
        },
      },
    }));
    expect(selectWorkflowDetailByPath({ uiData: s }, '/workflows/wf_1.json')?.workflow?.id).toBe('wf_1');

    s = reducer(s, applyUiEvent({
      type: 'workflow.run.completed',
      payload: {
        run_id: 'run_1',
        workflow_id: 'wf_1',
        status: 'completed',
        current_node_id: null,
        completed_node_ids: ['event_a', 'prompt_a'],
        thread_id: 'thread_1',
        trigger_event: { document_type: 'kanban_card', op: 'created', key: 'kanban_card:1', id: '1' },
        last_output: 'done',
        last_error: null,
        queued_at: '2026-07-06T00:00:00Z',
        started_at: '2026-07-06T00:00:01Z',
        updated_at: '2026-07-06T00:00:02Z',
        completed_at: '2026-07-06T00:00:02Z',
      },
    }));
    expect(selectWorkflowRuns({ uiData: s })).toHaveLength(1);
    expect(selectWorkflowLatestRunById({ uiData: s }, 'wf_1')?.run_id).toBe('run_1');
  });

  it('workflow.updated summarises every trigger on the list row from the saved graph', () => {
    const s = reducer(undefined, applyUiEvent({
      type: 'workflow.updated',
      payload: {
        path: '/workflows/wf_2.json',
        workflow: {
          id: 'wf_2',
          title: 'Digest',
          graph: {
            nodes: {
              tick: { kind: 'cron', title: 'Weekdays', expression: '0 9 * * 1-5', timezone: 'UTC' },
              card: { kind: 'event', title: 'Card', document_type: 'kanban_card' },
              say: { kind: 'agent', title: 'Say', template: 'Hi' },
            },
            edges: [],
          },
        },
      },
    }));
    const row = s.workflows.items.find((item) => item.id === 'wf_2');
    expect(row.triggers).toEqual(['cron 0 9 * * 1-5', 'kanban_card']);
    expect(row.document_type).toBe('kanban_card');
  });
});

describe('selectChatSessions (memoized)', () => {
  it('returns a stable reference until the chat list changes', () => {
    const s1 = reducer(undefined, snapshot(['a']));
    const r1 = selectChatSessions({ uiData: s1 });
    expect(selectChatSessions({ uiData: s1 })).toBe(r1);
    // unrelated state change keeps the same chats refs → memo holds
    expect(selectChatSessions({ uiData: { ...s1, status: { x: 1 } } })).toBe(r1);
  });

  it('recomputes after the chat list changes', () => {
    const s1 = reducer(undefined, snapshot(['a']));
    const r1 = selectChatSessions({ uiData: s1 });
    const s2 = reducer(s1, applyUiEvent({ type: 'chat.session.created', payload: { session: { thread_id: 'b' } } }));
    const r2 = selectChatSessions({ uiData: s2 });
    expect(r2).not.toBe(r1);
    expect(r2.map((c) => c.thread_id)).toEqual(['b', 'a']);
  });
});

const agentsSnapshot = (ids) => applyUiEvent({
  type: 'agents.snapshot',
  payload: { agents: ids.map((id) => ({ id, name: id })) },
});

describe('agent reducers', () => {
  it('agents.snapshot replaces the agent list and marks loaded', () => {
    const s = reducer(undefined, agentsSnapshot(['alpha', 'beta']));
    expect(s.agents.order).toEqual(['alpha', 'beta']);
    expect(s.agents.loaded).toBe(true);
    expect(selectAgentProfiles({ uiData: s }).map((a) => a.id)).toEqual(['alpha', 'beta']);
  });

  it('agents.snapshot refreshes the active detail when enabled changes', () => {
    let s = reducer(undefined, agentsSnapshot(['a2a-greetings']));
    s = reducer(s, applyUiEvent({
      type: 'agent.selected',
      payload: { agent: { id: 'a2a-greetings', enabled: false } },
    }));
    s = reducer(s, applyUiEvent({
      type: 'agents.snapshot',
      payload: { agents: [{ id: 'a2a-greetings', name: 'Greetings', enabled: true }] },
    }));
    expect(s.agents.byId['a2a-greetings'].enabled).toBe(true);
    expect(s.agents.active.detail.enabled).toBe(true);
  });

  it('agents.snapshot does not clear a loaded active routine graph omitted from the roster', () => {
    const graph = {
      status: 'Draft',
      entry: 'prompt-1',
      nodes: [{ id: 'prompt-1', kind: 'prompt' }],
      edges: [],
    };
    let s = reducer(undefined, agentsSnapshot(['routine-alpha']));
    s = reducer(s, applyUiEvent({
      type: 'agent.selected',
      payload: { agent: { id: 'routine-alpha', kind: 'routine', graph } },
    }));

    // On a cold page load the selected-detail reply can be followed by the
    // roster snapshot. The roster's graph:null must not replace the detail.
    s = reducer(s, applyUiEvent({
      type: 'agents.snapshot',
      payload: { agents: [{ id: 'routine-alpha', kind: 'routine', name: 'Alpha', graph: null }] },
    }));

    expect(s.agents.byId['routine-alpha'].graph).toBeNull();
    expect(s.agents.active.detail.graph).toEqual(graph);
  });

  it('agent.upserted merges into an existing agent and appends new ones', () => {
    let s = reducer(undefined, agentsSnapshot(['alpha']));
    s = reducer(s, applyUiEvent({ type: 'agent.upserted', payload: { agent: { id: 'alpha', role: 'changed' } } }));
    expect(s.agents.order).toEqual(['alpha']);
    expect(s.agents.byId.alpha.role).toBe('changed');
    s = reducer(s, applyUiEvent({ type: 'agent.upserted', payload: { agent: { id: 'gamma' } } }));
    expect(s.agents.order).toEqual(['alpha', 'gamma']);
  });

  it('agent.selected ignores a reply for an agent the selection has moved off', () => {
    let s = reducer(undefined, requestAgentDetail.pending('REQ1', {
      agentsUrl: '/x', agentId: 'alpha',
    }));
    expect(s.agents.active.agentId).toBe('alpha');
    s = reducer(s, applyUiEvent({ type: 'agent.selected', payload: { agent: { id: 'beta' }, request_id: 'REQ1' } }));
    expect(selectActiveAgentProfile({ uiData: s })).toBeNull();
    s = reducer(s, applyUiEvent({ type: 'agent.selected', payload: { agent: { id: 'alpha' }, request_id: 'REQ1' } }));
    expect(selectActiveAgentProfile({ uiData: s })).toEqual({ id: 'alpha' });
  });

  // A stream reconnect re-asks for the same agent, so several requests for it
  // can be outstanding. Keying staleness on request_id made each new ask
  // invalidate the previous one's answer, which under repeated reconnects
  // rejected every reply in turn and hung the detail pane on "Loading…".
  it('agent.selected accepts a reply for the selected agent from an earlier request', () => {
    let s = reducer(undefined, requestAgentDetail.pending('REQ1', {
      agentsUrl: '/x', agentId: 'alpha',
    }));
    s = reducer(s, requestAgentDetail.pending('REQ2', { agentsUrl: '/x', agentId: 'alpha' }));
    expect(s.agents.active.requestId).toBe('REQ2');
    s = reducer(s, applyUiEvent({ type: 'agent.selected', payload: { agent: { id: 'alpha' }, request_id: 'REQ1' } }));
    expect(selectActiveAgentProfile({ uiData: s })).toEqual({ id: 'alpha' });
  });

  it('agents.snapshot carries A2A fields (skills, url, transport) through to selectors', () => {
    const s = reducer(undefined, applyUiEvent({
      type: 'agents.snapshot',
      payload: {
        agents: [{
          id: 'a2a-observe',
          name: 'Observe Agent',
          kind: 'a2a',
          group: 'A2A agents',
          transport: 'JSONRPC',
          url: 'http://localhost:8080',
          card: {
            protocolVersion: '0.3.0',
            capabilities: { streaming: false, stateTransitionHistory: true },
            inputModes: ['text/plain'],
          },
          skills: [{ id: 'progenitor_health_report', name: 'Health', tags: ['observe'], examples: ['Run it'] }],
        }],
      },
    }));
    const agent = selectAgentProfiles({ uiData: s })[0];
    expect(agent.group).toBe('A2A agents');
    expect(agent.url).toBe('http://localhost:8080');
    expect(agent.transport).toBe('JSONRPC');
    expect(agent.card.protocolVersion).toBe('0.3.0');
    expect(agent.card.capabilities.stateTransitionHistory).toBe(true);
    expect(agent.skills.map((skill) => skill.id)).toEqual(['progenitor_health_report']);
    expect(agent.skills[0].examples).toEqual(['Run it']);
  });

  it('agent.graph.updated patches the active detail graph', () => {
    let s = reducer(undefined, agentsSnapshot(['alpha']));
    s = reducer(s, applyUiEvent({ type: 'agent.selected', payload: { agent: { id: 'alpha' } } }));
    const graph = { status: 'Designed', nodes: [{ id: 'a' }], edges: [] };
    s = reducer(s, applyUiEvent({ type: 'agent.graph.updated', payload: { agent_id: 'alpha', graph } }));
    expect(s.agents.active.detail.graph).toEqual(graph);
    expect(s.agents.byId.alpha.graph).toEqual(graph);
  });

  it('selectRoutineLatestRunStatusByAgentId maps latest run lifecycle to display status', () => {
    let s = reducer(undefined, agentsSnapshot(['routine-a', 'routine-b', 'routine-c']));
    s = reducer(s, applyUiEvent({
      type: 'agent.routine.run.started',
      payload: { agent_id: 'routine-a', request_id: 'r1', timestamp: '2026-10-07T00:00:01Z' },
    }));
    s = reducer(s, applyUiEvent({
      type: 'agent.routine.run.failed',
      payload: { agent_id: 'routine-b', request_id: 'r2', timestamp: '2026-10-07T00:00:02Z' },
    }));
    s = reducer(s, applyUiEvent({
      type: 'agent.routine.run.paused',
      payload: { agent_id: 'routine-c', request_id: 'r3', timestamp: '2026-10-07T00:00:03Z' },
    }));
    // A failed event that arrives late but is older than the started run
    // does not override it: the newest timestamp wins, not the last event.
    s = reducer(s, applyUiEvent({
      type: 'agent.routine.run.failed',
      payload: { agent_id: 'routine-a', request_id: 'r4', timestamp: '2026-10-07T00:00:00Z' },
    }));
    expect(selectRoutineLatestRunStatusByAgentId({ uiData: s })['routine-a']).toBe('Running');
    // A newer completed run leaves the routine in no filter but All.
    s = reducer(s, applyUiEvent({
      type: 'agent.routine.run.completed',
      payload: { agent_id: 'routine-a', request_id: 'r5', timestamp: '2026-10-07T00:00:04Z' },
    }));

    const statuses = selectRoutineLatestRunStatusByAgentId({ uiData: s });
    expect(statuses['routine-a']).toBeNull();
    expect(statuses['routine-b']).toBe('Failed');
    expect(statuses['routine-c']).toBe('Paused');
  });
});

describe('chat indicators (nav dot)', () => {

  it.each([
    ['idle when runtime id lists are explicitly empty', [], [], [], 'idle'],
    ['complete when unread chats are present', ['c2'], [], [], 'complete'],
    ['error outranks unread chats', ['c2'], [], ['c3'], 'error'],
    ['generating outranks error and unread chats', ['c2'], ['c1'], ['c3'], CHAT_STATUS.GENERATING],
  ])('derives aggregate %s status', (_description, unread, generating, errored, status) => {
    let s = reducer(undefined, setUnreadChatIds(unread));
    s = reducer(s, setGeneratingChatIds(generating));
    s = reducer(s, setErroredChatIds(errored));

    expect(selectChatIndicators({ uiData: s })).toEqual({
      hasUnread: unread.length > 0,
      isGenerating: generating.length > 0,
      hasError: errored.length > 0,
      status,
    });
  });

  it('adds, clears, and dedupes chat runtime ids', () => {
    let s = reducer(undefined, markChatUnread('c2'));
    s = reducer(s, markChatUnread('c2'));
    s = reducer(s, markChatGenerating('c1'));
    s = reducer(s, markChatGenerating('c1'));
    s = reducer(s, markChatErrored('c3'));
    s = reducer(s, markChatErrored('c3'));

    expect(selectUnreadChatIds({ uiData: s })).toEqual(['c2']);
    expect(selectGeneratingChatIds({ uiData: s })).toEqual(['c1']);
    expect(selectErroredChatIds({ uiData: s })).toEqual(['c3']);

    s = reducer(s, clearChatUnread('c2'));
    s = reducer(s, clearChatGenerating('c1'));
    s = reducer(s, clearChatErrored('c3'));

    expect(selectUnreadChatIds({ uiData: s })).toEqual([]);
    expect(selectGeneratingChatIds({ uiData: s })).toEqual([]);
    expect(selectErroredChatIds({ uiData: s })).toEqual([]);
  });

  it('normalizes full replacement id lists', () => {
    let s = reducer(undefined, setUnreadChatIds(['c2', '', 'c2', null]));
    s = reducer(s, setGeneratingChatIds(['c1', 'c1']));
    s = reducer(s, setErroredChatIds(['c3', undefined, 'c3']));

    expect(selectUnreadChatIds({ uiData: s })).toEqual(['c2']);
    expect(selectGeneratingChatIds({ uiData: s })).toEqual(['c1']);
    expect(selectErroredChatIds({ uiData: s })).toEqual(['c3']);
  });
});

describe('scheduleSnapshotReceived', () => {
  it('stores the provided schedule list directly', () => {
    const items = [{ id: 'sched-live', name: 'Live schedule' }];
    const s = reducer(undefined, scheduleSnapshotReceived(items));
    expect(selectSchedules({ uiData: s })).toEqual(items);
  });
});

describe('kanbanSnapshotReceived', () => {
  it('stores the provided tasks board directly', () => {
    const board = { id: 'board-1', cards: [] };
    const s = reducer(undefined, kanbanSnapshotReceived(board));
    expect(selectKanbanBoard({ uiData: s })).toEqual(board);
  });
});

describe('inbox events', () => {
  const inboxSnapshot = (messages) => applyUiEvent({
    type: 'inbox.snapshot',
    payload: { messages, total: messages.length, unread: messages.filter((m) => !m.read).length },
  });

  it('snapshot replaces the message list in server order and marks loaded', () => {
    const s = reducer(undefined, inboxSnapshot([
      { id: 'msg_new', title: 'New', read: false },
      { id: 'msg_old', title: 'Old', read: true },
    ]));
    expect(s.inbox.order).toEqual(['msg_new', 'msg_old']);
    expect(s.inbox.loaded).toBe(true);
    expect(selectInboxUnreadCount({ uiData: s })).toBe(1);
  });

  it('unread count excludes archived messages', () => {
    const s = reducer(undefined, inboxSnapshot([
      { id: 'msg_new', title: 'New', read: false, archived: false },
      { id: 'msg_archived', title: 'Archived', read: false, archived: true },
    ]));
    expect(selectInboxUnreadCount({ uiData: s })).toBe(1);
  });

  it('upserted merges an existing message and prepends a new one', () => {
    let s = reducer(undefined, inboxSnapshot([{ id: 'a', title: 'A', read: false }]));
    s = reducer(s, applyUiEvent({ type: 'inbox.message.upserted', payload: { message: { id: 'a', read: true } } }));
    expect(s.inbox.byId.a.title).toBe('A');
    expect(s.inbox.byId.a.read).toBe(true);
    expect(selectInboxUnreadCount({ uiData: s })).toBe(0);
    s = reducer(s, applyUiEvent({ type: 'inbox.message.upserted', payload: { message: { id: 'b', title: 'B', read: false } } }));
    expect(s.inbox.order).toEqual(['b', 'a']);
  });

  it('deleted removes the message', () => {
    let s = reducer(undefined, inboxSnapshot([
      { id: 'a', title: 'A', read: false },
      { id: 'b', title: 'B', read: false },
    ]));
    s = reducer(s, applyUiEvent({ type: 'inbox.message.deleted', payload: { message_id: 'a' } }));
    expect(s.inbox.order).toEqual(['b']);
    expect(s.inbox.byId.a).toBeUndefined();
    expect(selectInboxMessages({ uiData: s }).map((m) => m.id)).toEqual(['b']);
  });

  it('fetchInbox.fulfilled acknowledges without installing data until the matching inbox.snapshot event', () => {
    const acknowledgement = { request_id: 'REQ1', event_types: ['inbox.snapshot'] };
    let s = reducer(undefined, fetchInbox.pending('REQ1', { inboxUrl: '/api/ui/inbox' }));
    s = reducer(s, fetchInbox.fulfilled(acknowledgement, 'REQ1', { inboxUrl: '/api/ui/inbox' }));
    expect(selectInboxState({ uiData: s }).loaded).toBe(false);
    expect(selectInboxState({ uiData: s }).loading).toBe(true);
    expect(selectInboxMessages({ uiData: s })).toEqual([]);

    s = reducer(s, applyUiEvent({
      type: 'inbox.snapshot',
      payload: {
        request_id: 'REQ1',
        messages: [{ id: 'a', title: 'A', read: false }],
        total: 1,
        unread: 1,
      },
    }));
    expect(selectInboxState({ uiData: s }).loaded).toBe(true);
    expect(selectInboxState({ uiData: s }).loading).toBe(false);
    expect(selectInboxMessages({ uiData: s }).map((message) => message.id)).toEqual(['a']);
  });

  it('fetchInbox.rejected ignores an unmatched legacy finite-response error', () => {
    const action = fetchInbox.rejected(new Error('network down'), 'reqId', { inboxUrl: '/api/ui/inbox' });
    const s = reducer(undefined, action);
    expect(selectInboxState({ uiData: s }).loading).toBe(false);
    expect(selectInboxState({ uiData: s }).loaded).toBe(false);
    expect(selectInboxState({ uiData: s }).error).toBeNull();
  });
});

describe('nav activity tracking (unseen-page dots)', () => {
  it('a route\'s first-ever event is its baseline, not "new" activity', () => {
    const s = reducer(undefined, applyUiEvent({ type: 'kanban.snapshot', payload: {} }));
    expect(selectUnseenNavRoutes({ uiData: s })).toEqual({});
  });

  it('a later event for a route not currently open marks it unseen', () => {
    let s = reducer(undefined, applyUiEvent({ type: 'kanban.snapshot', payload: {} }));
    s = reducer(s, applyUiEvent({ type: 'kanban.snapshot', payload: {} }));
    expect(selectUnseenNavRoutes({ uiData: s })).toEqual({ '/tasks': true });
  });

  it('an event for the currently active route never marks it unseen', () => {
    let s = reducer(undefined, setActiveNavRoute('/tasks'));
    s = reducer(s, applyUiEvent({ type: 'kanban.snapshot', payload: {} }));
    s = reducer(s, applyUiEvent({ type: 'kanban.snapshot', payload: {} }));
    expect(selectUnseenNavRoutes({ uiData: s })).toEqual({});
  });

  it('navigating to a route clears its unseen flag', () => {
    let s = reducer(undefined, applyUiEvent({ type: 'kanban.snapshot', payload: {} }));
    s = reducer(s, applyUiEvent({ type: 'kanban.snapshot', payload: {} }));
    expect(selectUnseenNavRoutes({ uiData: s })).toEqual({ '/tasks': true });
    s = reducer(s, setActiveNavRoute('/tasks'));
    expect(selectUnseenNavRoutes({ uiData: s })).toEqual({});
  });

  it('workflow events light the workflows nav dot after the baseline snapshot', () => {
    let s = reducer(undefined, setActiveNavRoute('/tasks'));
    s = reducer(s, applyUiEvent({ type: 'workflows.snapshot', payload: { items: [] } }));
    expect(selectUnseenNavRoutes({ uiData: s })).toEqual({});
    s = reducer(s, applyUiEvent({
      type: 'workflow.updated',
      payload: {
        path: '/workflows/wf_1.json',
        workflow: {
          id: 'wf_1',
          title: 'Workflow',
          description: '',
          enabled: true,
          paused: false,
          version: 1,
          document_type: 'kanban_card',
          graph: { nodes: { event_a: { kind: 'event', title: 'Event', document_type: 'kanban_card' } }, edges: [] },
          ui: {},
          updated_at: '2026-07-06T00:00:00Z',
          created_at: '2026-07-06T00:00:00Z',
        },
      },
    }));
    expect(selectUnseenNavRoutes({ uiData: s })).toEqual({ '/workflows': true });
  });

  it('unmapped event types are ignored', () => {
    const s = reducer(undefined, applyUiEvent({ type: 'not.a.tracked.event', payload: {} }));
    expect(selectUnseenNavRoutes({ uiData: s })).toEqual({});
  });
});

describe('schedule snapshot recovery', () => {
  const args = { schedulesUrl: '/api/ui/schedules' };
  const schedule = { id: 'daily', name: 'Daily review', enabled: true, next_run: '2026-10-12T09:00:00Z' };

  it('keeps the list pending until its causal snapshot, including snapshot-before-ack ordering', () => {
    let state = reducer(undefined, fetchSchedules.pending('first', args));
    state = reducer(state, fetchSchedules.fulfilled({
      request_id: 'first', event_types: ['schedule.snapshot'],
    }, 'first', args));
    expect(selectSchedulesState({ uiData: state })).toMatchObject({
      items: [], loaded: false, loading: true, requestId: 'first',
    });

    state = reducer(state, applyUiEvent({
      type: 'schedule.snapshot', payload: { items: [schedule], request_id: 'first' },
    }));
    expect(selectSchedulesState({ uiData: state })).toMatchObject({
      items: [schedule], loaded: true, loading: false, error: null, requestId: null,
    });

    state = reducer(state, fetchSchedules.pending('recovery', args));
    state = reducer(state, applyUiEvent({
      type: 'schedule.snapshot', payload: { items: [], request_id: 'recovery' },
    }));
    state = reducer(state, fetchSchedules.fulfilled({
      request_id: 'recovery', event_types: ['schedule.snapshot'],
    }, 'recovery', args));
    expect(selectSchedulesState({ uiData: state })).toMatchObject({
      items: [], loaded: true, loading: false, error: null, requestId: null,
    });
  });

  it('does not let a disconnected request failure cancel a newer recovery request', () => {
    let state = reducer(undefined, fetchSchedules.pending('disconnected', args));
    state = reducer(state, fetchSchedules.pending('reconnected', args));
    state = reducer(state, fetchSchedules.rejected(new Error('Old connection failed'), 'disconnected', args));
    expect(selectSchedulesState({ uiData: state })).toMatchObject({
      loading: true, error: null, requestId: 'reconnected',
    });
    state = reducer(state, fetchSchedules.rejected(new Error('Recovery failed'), 'reconnected', args));
    expect(selectSchedulesState({ uiData: state })).toMatchObject({
      loading: false, error: 'Recovery failed', requestId: null,
    });
    state = reducer(state, applyUiEvent({
      type: 'schedule.snapshot', payload: { items: [schedule] },
    }));
    expect(selectSchedulesState({ uiData: state })).toMatchObject({
      items: [schedule], loaded: true, error: null,
    });
  });
});
