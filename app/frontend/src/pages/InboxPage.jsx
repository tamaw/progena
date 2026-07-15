import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { useEntityUrlSync } from '../lib/routes';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import DetailHeader, {
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  DeleteActionIcon,
  EditActionIcon,
} from '../components/layout/DetailHeader';
import {
  PaneList,
  PaneListFilters,
  PaneListScroll,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
} from '../components/layout/PaneList';
import { useConfig } from '../context/ConfigContext';
import { renderMarkdownToHtml } from '../lib/markdown';
import {
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
  if (!content) return null;
  return <div className="markdown-content" dangerouslySetInnerHTML={{ __html: html }} />;
}

export default function InboxPage() {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  // `titleSlug` is display context only (e.g. /inbox/msg_42/weekly-digest) --
  // selection is driven entirely by the id, never by the slug.
  const { messageId: urlMessageId } = useParams();
  const { getApiUrl } = useConfig();
  const inboxUrl = getApiUrl('inbox');
  const messages = useSelector(selectInboxMessages);
  const inboxState = useSelector(selectInboxState);
  const [selectedId, setSelectedId] = useState(() => urlMessageId || null);
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
    () => messages.find((message) => message.id === selectedId) || null,
    [messages, selectedId],
  );

  // A selectedId whose message was deleted (here or in another tab) resolves to
  // null above, so the detail pane falls back to the empty state on its own.

  // Selecting a message (including the initial auto-select below) marks it
  // read (optimistic; the snapshot that follows reconciles).
  const selectMessage = useCallback((message) => {
    setSelectedId(message.id);
    if (!message.read) {
      dispatch(markInboxMessageRead({ inboxUrl, messageId: message.id, read: true }));
    }
  }, [dispatch, inboxUrl]);

  // On mount, a URL that already names a message (bookmark, reload, shared
  // link) selects that message directly instead of auto-selecting the top
  // card. Otherwise, default to the top of the (filtered/sorted) card list
  // once, the same way the Chat page opens onto its top session — only on
  // initial load, so it doesn't fight a selection the user already made.
  const hasAutoSelectedRef = useRef(false);
  useEffect(() => {
    if (hasAutoSelectedRef.current || !inboxState.loaded) {
      return;
    }
    hasAutoSelectedRef.current = true;
    if (urlMessageId) {
      const target = messages.find((message) => message.id === urlMessageId);
      if (target) {
        if (selectedId !== target.id) {
          Promise.resolve().then(() => selectMessage(target));
        } else if (!target.read) {
          dispatch(markInboxMessageRead({ inboxUrl, messageId: target.id, read: true }));
        }
        return;
      }
      // The linked message doesn't exist (deleted, or never real) - fall
      // back to auto-selecting the top card below.
      Promise.resolve().then(() => setSelectedId(null));
      navigate('/inbox', { replace: true });
    }
    if (visibleMessages.length === 0) {
      return;
    }
    const topMessage = visibleMessages[0];
    // Deferred a tick so the state update happens in a callback rather than
    // synchronously in the effect body (react-hooks/set-state-in-effect).
    Promise.resolve().then(() => selectMessage(topMessage));
  }, [dispatch, inboxUrl, inboxState.loaded, messages, navigate, selectMessage, selectedId, urlMessageId, visibleMessages]);

  // Keep the URL in sync with whichever message is selected. The title slug
  // is cosmetic; only the id segment is ever read back.
  useEntityUrlSync('/inbox', selectedMessage?.id, selectedMessage?.title);

  const selectedMessageId = selectedMessage?.id;
  const selectedMessageRead = selectedMessage?.read;
  const selectedMessageTitle = selectedMessage?.title;

  const toggleRead = useCallback(() => {
    if (!selectedMessageId) return;
    dispatch(markInboxMessageRead({
      inboxUrl,
      messageId: selectedMessageId,
      read: !selectedMessageRead,
    }));
  }, [dispatch, inboxUrl, selectedMessageId, selectedMessageRead]);

  const removeMessage = useCallback(() => {
    if (!selectedMessageId) return;
    dispatch(deleteInboxMessage({ inboxUrl, messageId: selectedMessageId }));
    setSelectedId(null);
  }, [dispatch, inboxUrl, selectedMessageId]);

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
        messageId: selectedMessageId,
        messageTitle: selectedMessageTitle,
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
        <PaneListScroll>
          {visibleMessages.length === 0 ? (
            <div className="chat-pane-empty">{listEmptyMessage}</div>
          ) : (
            visibleMessages.map((message) => (
              <article
                key={message.id}
                className={`chat-session-card${message.id === selectedId ? ' is-selected' : ''}${message.read ? '' : ' is-unread'}`}
                role="button"
                tabIndex={0}
                aria-current={message.id === selectedId ? 'true' : undefined}
                onClick={() => selectMessage(message)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    selectMessage(message);
                  }
                }}
              >
                <span
                  className={`chat-session-card__dot${message.read ? '' : ' chat-session-card__dot--unread'}`}
                  aria-hidden="true"
                />
                <div className="chat-session-card__top">
                  <span className="chat-session-card__title">{message.title || message.id}</span>
                  <span className="chat-session-card__age">{formatRelativeTime(message.created_at)}</span>
                </div>
                {previewText(message.body) ? (
                  <div className="chat-session-card__preview">{previewText(message.body)}</div>
                ) : null}
              </article>
            ))
          )}
        </PaneListScroll>
      </PaneList>

      <DetailPane className="inbox-detail" label="Inbox message">
        <DetailHeader
          title={selectedMessage
            ? (selectedMessage.title || selectedMessage.id)
            : (messages.length === 0 ? 'Inbox is empty' : 'No message selected')}
          meta={selectedMessage ? [
            <DetailStatus key="status" tone={selectedMessage.read ? 'ok' : 'info'}>
              {selectedMessage.read ? 'Read' : 'Unread'}
            </DetailStatus>,
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
              <DetailHeaderAction
                icon={<DeleteActionIcon />}
                label="Delete"
                variant="danger"
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
