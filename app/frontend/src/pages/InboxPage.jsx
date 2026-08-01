import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../lib/routes';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import DetailHeader, {
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  ApproveActionIcon,
  DeleteActionIcon,
  EditActionIcon,
  RejectActionIcon,
} from '../components/layout/DetailHeader';
import {
  PaneEmptyState,
  PaneList,
  PaneListFilters,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListVirtualized,
} from '../components/layout/PaneList';
import { useConfig } from '../context/ConfigContext';
import { useMermaidRender } from '../hooks/useMermaidRender';
import { renderMarkdownToHtml } from '../lib/markdown';
import {
  decideInboxMessage,
  deleteInboxMessage,
  markInboxMessageRead,
  selectInboxMessages,
  selectInboxState,
} from '../store/uiDataSlice';
import { formatRelativeTime } from './tasksPageModel';

const INBOX_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'unread', label: 'Unread' },
];
const INBOX_SORT_MODES = ['Recent', 'Name'];

function MarkUnreadActionIcon() {
  return (
    <svg className="detail-header-action__svg" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M2.75 4.25h10.5v8H2.75z" />
      <path d="m2.75 4.75 5.25 4 5.25-4" />
    </svg>
  );
}

function formatTimestamp(isoText) {
  if (!isoText) return '';
  const timestamp = Date.parse(isoText);
  if (Number.isNaN(timestamp)) return isoText;
  return new Date(timestamp).toLocaleString();
}

function previewText(body) {
  const firstLine = String(body || '')
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line.length > 0) || '';
  return firstLine.length > 120 ? `${firstLine.slice(0, 120)}…` : firstLine;
}

function InboxMessageBody({ content }) {
  const html = useMemo(() => renderMarkdownToHtml(content), [content]);
  const containerRef = useRef(null);
  useMermaidRender(containerRef, content);
  if (!content) return null;
  return (
    <div ref={containerRef} className="markdown-content" dangerouslySetInnerHTML={{ __html: html }} />
  );
}

export default function InboxPage() {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  // `titleSlug` is display context only (e.g. /inbox/msg_42/weekly-digest) --
  // selection is driven entirely by the id, never by the slug.
  const { messageId: urlMessageId } = useParams();
  const routedMessageId = typeof urlMessageId === 'string' && urlMessageId.trim().length > 0
    ? urlMessageId
    : null;
  const { getApiUrl } = useConfig();
  const inboxUrl = getApiUrl('inbox');
  const messages = useSelector(selectInboxMessages);
  const inboxState = useSelector(selectInboxState);
  const [fallbackSelectedId, setFallbackSelectedId] = useState(null);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('Recent');

  // No fetch-on-mount here: RealtimeBridge seeds the inbox globally on
  // connect (same convention ConversationPage follows for chat sessions),
  // so the page only ever reads state instead of re-triggering its own GET.

  const visibleMessages = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matching = messages.filter((message) => (
      (filter !== 'unread' || !message.read)
      && (!needle
        || String(message.title || '').toLowerCase().includes(needle)
        || String(message.body || '').toLowerCase().includes(needle))
    ));
    // Server order is already created_at desc ("Recent"); "Name" re-sorts A→Z.
    if (sort === 'Name') {
      return [...matching].sort((a, b) => String(a.title || '').localeCompare(String(b.title || '')));
    }
    return matching;
  }, [messages, query, filter, sort]);

  const selectedMessage = useMemo(
    () => messages.find((message) => message.id === (routedMessageId || fallbackSelectedId)) || null,
    [fallbackSelectedId, messages, routedMessageId],
  );

  // A routed message id that no longer exists is cleared back to /inbox above;
  // otherwise the detail pane simply reflects the currently selected message.

  // Clicking a card owns the route: mounted navigation to another id must swap
  // detail immediately instead of waiting for mount-only state to catch up.
  const selectMessage = useCallback((message) => {
    navigate(buildEntityPath('/inbox', message.id, message.title), { replace: true });
  }, [navigate]);

  // On mount, a bare /inbox route auto-selects the top visible card once so the
  // page opens onto useful content. A routed message id stays authoritative on
  // every change instead of being treated as mount-only state.
  const hasAutoSelectedRef = useRef(false);
  useEffect(() => {
    if (!inboxState.loaded) {
      return;
    }
    if (routedMessageId) {
      hasAutoSelectedRef.current = true;
      if (!messages.some((message) => message.id === routedMessageId)) {
        navigate('/inbox', { replace: true });
      }
      return;
    }
    if (hasAutoSelectedRef.current) {
      return;
    }
    hasAutoSelectedRef.current = true;
    if (visibleMessages.length === 0) {
      return;
    }
    Promise.resolve().then(() => setFallbackSelectedId(visibleMessages[0].id));
  }, [inboxState.loaded, messages, navigate, routedMessageId, visibleMessages]);

  // Selecting a message (whether by route, auto-select, or a click above) marks
  const selectedMessageId = selectedMessage?.id;
  const selectedMessageRead = selectedMessage?.read;
  const selectedMessageTitle = selectedMessage?.title;

  // Selecting a message (whether by route, auto-select, or a click above) marks
  // it read optimistically; the snapshot that follows reconciles.
  useEffect(() => {
    if (!selectedMessageId || selectedMessageRead) {
      return;
    }
    dispatch(markInboxMessageRead({ inboxUrl, messageId: selectedMessageId, read: true }));
  }, [dispatch, inboxUrl, selectedMessageId, selectedMessageRead]);

  // Keep the URL in sync with whichever message is selected. The title slug
  // is cosmetic; only the id segment is ever read back.
  useEntityUrlSync('/inbox', selectedMessage?.id, selectedMessage?.title);

  const toggleRead = useCallback(() => {
    if (!selectedMessageId) return;
    dispatch(markInboxMessageRead({
      inboxUrl,
      messageId: selectedMessageId,
      read: !selectedMessageRead,
    }));
  }, [dispatch, inboxUrl, selectedMessageId, selectedMessageRead]);

  const decide = useCallback((decision) => {
    if (!selectedMessageId) return;
    dispatch(decideInboxMessage({ inboxUrl, messageId: selectedMessageId, decision }));
  }, [dispatch, inboxUrl, selectedMessageId]);

  const removeMessage = useCallback(() => {
    if (!selectedMessageId) return;
    dispatch(deleteInboxMessage({ inboxUrl, messageId: selectedMessageId }));
    setFallbackSelectedId(null);
    navigate('/inbox', { replace: true });
  }, [dispatch, inboxUrl, navigate, selectedMessageId]);

  // Composing/editing is delegated to the Progenitor agent via quick chat (same
  // pattern as Tasks and Schedules): the agent writes /inbox/<id>.md and the
  // new/changed message arrives here live via inbox.snapshot.
  const openCompose = useCallback(() => {
    window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
      detail: { mode: 'inbox-compose' },
    }));
  }, []);

  const openEditInChat = useCallback(() => {
    if (!selectedMessageId) return;
    window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
      detail: {
        mode: 'inbox-edit',
        subtitle: selectedMessageTitle,
      },
    }));
  }, [selectedMessageId, selectedMessageTitle]);

  const listEmptyMessage = inboxState.error
    ? inboxState.error
    : query.trim()
      ? 'No messages match your search'
      : filter === 'unread'
        ? 'No unread messages'
        : 'No messages yet';

  return (
    <PageShell className="inbox-page" label="Inbox">
      <PaneList label="Messages" className="inbox-list">
        <PaneListFilters
          filters={INBOX_FILTERS}
          activeFilter={filter}
          onFilterChange={setFilter}
          actionLabel="+ New"
          onAction={openCompose}
        />
        <PaneListToolbar>
          <PaneListSearch value={query} placeholder="Search messages" onChange={setQuery} />
          <PaneListSortButton
            title="Sort messages"
            options={INBOX_SORT_MODES}
            value={sort}
            onSelect={setSort}
          />
        </PaneListToolbar>
        {visibleMessages.length === 0 ? (
          <PaneEmptyState>{listEmptyMessage}</PaneEmptyState>
        ) : (
          <PaneListVirtualized
            items={visibleMessages}
            getItemKey={(message) => message.id}
            estimatedItemHeight={70}
            renderItem={(message) => (
              <article
                className={`chat-session-card${message.id === selectedMessageId ? ' is-selected' : ''}${message.read ? '' : ' is-unread'}`}
                role="button"
                tabIndex={0}
                aria-current={message.id === selectedMessageId ? 'true' : undefined}
                onClick={() => selectMessage(message)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    selectMessage(message);
                  }
                }}
              >
                <div className="chat-session-card__top">
                  <span
                    className={`chat-session-card__dot${message.read ? ' chat-session-card__dot--read' : ' chat-session-card__dot--unread'}`}
                    aria-hidden="true"
                  />
                  <span className="chat-session-card__title">{message.title || message.id}</span>
                  <span className="chat-session-card__age">{formatRelativeTime(message.created_at)}</span>
                </div>
                {previewText(message.body) ? (
                  <div className="chat-session-card__preview">{previewText(message.body)}</div>
                ) : null}
              </article>
            )}
          />
        )}
      </PaneList>

      <DetailPane className="inbox-detail" label="Inbox message">
        <DetailHeader
          title={selectedMessage
            ? (selectedMessage.title || selectedMessage.id)
            : (messages.length === 0 ? 'Inbox is empty' : 'No message selected')}
          meta={selectedMessage ? [
            <DetailStatus
              key="status"
              tone={selectedMessage.read ? 'muted' : 'ok'}
              className={selectedMessage.read ? 'inbox-detail__status--read' : 'inbox-detail__status--unread'}
            >
              {selectedMessage.read ? 'Read' : 'Unread'}
            </DetailStatus>,
            selectedMessage.requires_approval
              ? (
                <DetailStatus
                  key="decision"
                  tone={selectedMessage.decision === 'approved'
                    ? 'ok'
                    : selectedMessage.decision === 'rejected' ? 'error' : 'warning'}
                  className={`inbox-detail__status--decision-${selectedMessage.decision || 'pending'}`}
                >
                  {selectedMessage.decision === 'approved'
                    ? 'Approved'
                    : selectedMessage.decision === 'rejected' ? 'Rejected' : 'Approval required'}
                </DetailStatus>
              )
              : null,
            selectedMessage.created_at
              ? <span key="created" className="taskid">Created {formatTimestamp(selectedMessage.created_at)}</span>
              : null,
            selectedMessage.read_at
              ? <span key="read_at" className="taskid">Read {formatTimestamp(selectedMessage.read_at)}</span>
              : null,
            <span key="id" className="taskid">#{selectedMessage.id}</span>,
          ] : []}
          actions={(
            <DetailHeaderActions>
              <DetailHeaderAction
                icon={<EditActionIcon />}
                label="Edit in chat"
                onClick={openEditInChat}
                disabled={!selectedMessage}
              />
              <DetailHeaderAction
                icon={<MarkUnreadActionIcon />}
                label={selectedMessage?.read === false ? 'Mark read' : 'Mark unread'}
                aria-pressed={selectedMessage ? !selectedMessage.read : undefined}
                onClick={toggleRead}
                disabled={!selectedMessage}
              />
              {selectedMessage?.requires_approval && !selectedMessage.decision && (
                <DetailHeaderAction
                  icon={<ApproveActionIcon />}
                  label="Approve"
                  variant="success"
                  className="inbox-detail__approve"
                  onClick={() => decide('approved')}
                />
              )}
              {selectedMessage?.requires_approval && !selectedMessage.decision && (
                <DetailHeaderAction
                  icon={<RejectActionIcon />}
                  label="Reject"
                  variant="danger"
                  className="inbox-detail__reject"
                  onClick={() => decide('rejected')}
                />
              )}
              <DetailHeaderAction
                icon={<DeleteActionIcon />}
                label="Delete"
                variant="danger"
                className="inbox-detail__delete"
                onClick={removeMessage}
                disabled={!selectedMessage}
              />
            </DetailHeaderActions>
          )}
        />
        <div className="detail-pane__body inbox-detail__body">
          {selectedMessage ? (
            <InboxMessageBody content={selectedMessage.body} />
          ) : (
            <div className="welcome-message">
              <h2>{messages.length === 0 ? 'Your inbox is empty' : 'No message selected'}</h2>
              <p className="muted">
                {messages.length === 0
                  ? 'Messages the agent sends you will show up here.'
                  : 'Select a message from the list to read it.'}
              </p>
            </div>
          )}
        </div>
      </DetailPane>
    </PageShell>
  );
}
