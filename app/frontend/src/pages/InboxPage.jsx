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
  ArchiveActionIcon,
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
  PaneListRows,
} from '../components/layout/PaneList';
import { useConfig } from '../context/ConfigContext';
import { formatStartedTime } from '../components/chat/conversationModel';
import {
  archiveInboxMessage,
  decideInboxMessage,
  deleteInboxMessage,
  fetchInbox,
  markInboxMessageRead,
  selectInboxMessages,
  selectInboxState,
} from '../store/uiDataSlice';
import { formatCompactRelativeAge } from '../lib/displayTime';
import InlineFailure from '../components/common/InlineFailure';
import StructuredInboxBody from './StructuredInboxBody';
import {
  filterInboxMessages,
  getInboxMessageStatus,
  inboxMessageNeedsDecision,
  INBOX_FILTERS,
  INBOX_STATUS_META,
  sortInboxMessages,
} from './inboxPageModel';

const INBOX_SORT_MODES = ['Recent', 'Name'];

function MarkUnreadActionIcon() {
  return (
    <svg className="detail-header-action__svg" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M2.75 4.25h10.5v8H2.75z" />
      <path d="m2.75 4.75 5.25 4 5.25-4" />
    </svg>
  );
}

function DecisionIcon({ decision }) {
  return (
    <svg className="inbox-detail__decision-icon" viewBox="0 0 16 16" aria-hidden="true">
      {decision === 'approved' ? (
        <path d="M3.25 8.5 6.5 11.75 12.75 4.5" />
      ) : (
        <>
          <path d="M4 4l8 8" />
          <path d="M12 4l-8 8" />
        </>
      )}
    </svg>
  );
}

function previewText(body) {
  const firstLine = String(body || '')
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line.length > 0) || '';
  return firstLine.length > 120 ? `${firstLine.slice(0, 120)}…` : firstLine;
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

  const visibleMessages = useMemo(
    () => sortInboxMessages(filterInboxMessages(messages, filter, query), sort),
    [messages, query, filter, sort],
  );

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
  const selectedMessageStatus = getInboxMessageStatus(selectedMessage);

  // Selecting a message (whether by route, auto-select, or a click above) marks
  // it read optimistically; the snapshot that follows reconciles. Keyed by id
  // (not read) so this only fires once per *selection*, not every time --
  // otherwise a manual "mark unread" on the still-selected message would
  // immediately be flipped back to read by this same effect.
  const autoMarkedReadIdRef = useRef(null);
  useEffect(() => {
    if (!selectedMessageId || autoMarkedReadIdRef.current === selectedMessageId) {
      return;
    }
    autoMarkedReadIdRef.current = selectedMessageId;
    if (selectedMessageRead) {
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

  const toggleArchive = useCallback(() => {
    if (!selectedMessageId) return;
    dispatch(archiveInboxMessage({
      inboxUrl,
      messageId: selectedMessageId,
      archived: !selectedMessage?.archived,
    }));
    // Archiving (or unarchiving) the selected message can push it out of the
    // current filter's visible list, same as delete -- clear the selection
    // instead of leaving the detail pane stuck on a now-hidden message.
    setFallbackSelectedId(null);
    navigate('/inbox', { replace: true });
  }, [dispatch, inboxUrl, navigate, selectedMessage?.archived, selectedMessageId]);

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

  const listEmptyMessage = query.trim()
      ? 'No messages match your search'
      : filter === 'unread'
        ? 'No unread messages'
        : filter === 'pending'
          ? 'No messages pending approval'
          : filter === 'archived'
            ? 'No archived messages'
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
        <InlineFailure failure={inboxState.error} operation="Load messages" onRetry={() => dispatch(fetchInbox({ inboxUrl }))} />
        {visibleMessages.length === 0 ? (
          !inboxState.error && <PaneEmptyState>{listEmptyMessage}</PaneEmptyState>
        ) : (
          <PaneListRows
            items={visibleMessages}
            getItemKey={(message) => message.id}
            renderItem={(message) => {
              const status = getInboxMessageStatus(message);
              const isUnread = status === 'unread';
              return (
                <article
                  className={`chat-session-card${message.id === selectedMessageId ? ' is-selected' : ''}${isUnread ? ' is-unread' : ''}`}
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
                      className={`chat-session-card__dot ${INBOX_STATUS_META[status].dotClass}`}
                      aria-hidden="true"
                    />
                    <span className="chat-session-card__title">{message.title || message.id}</span>
                    <span className="chat-session-card__age">{formatCompactRelativeAge(message.created_at)}</span>
                  </div>
                  {previewText(message.body) ? (
                    <div className="chat-session-card__preview">{previewText(message.body)}</div>
                  ) : null}
                </article>
              );
            }}
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
              tone={INBOX_STATUS_META[selectedMessageStatus].tone}
            >
              {INBOX_STATUS_META[selectedMessageStatus].label}
            </DetailStatus>,
            selectedMessage.decision
              ? (
                <span key="decision" className="muted inbox-detail__decision">
                  <DecisionIcon decision={selectedMessage.decision} />
                  {selectedMessage.decision === 'approved' ? 'Approved' : 'Rejected'}
                </span>
              )
              : null,
            selectedMessage.created_at
              ? <span key="created" className="muted">Created {formatStartedTime(selectedMessage.created_at)}</span>
              : null,
            selectedMessage.read_at
              ? <span key="read_at" className="muted">Read {formatStartedTime(selectedMessage.read_at)}</span>
              : null,
            <span key="id" className="taskid">#{selectedMessage.id}</span>,
          ] : []}
          actions={(
            <DetailHeaderActions>
              {inboxMessageNeedsDecision(selectedMessage) && (
                <DetailHeaderAction
                  icon={<ApproveActionIcon />}
                  label="Approve"
                  onClick={() => decide('approved')}
                />
              )}
              {inboxMessageNeedsDecision(selectedMessage) && (
                <DetailHeaderAction
                  icon={<RejectActionIcon />}
                  label="Reject"
                  onClick={() => decide('rejected')}
                />
              )}
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
                icon={<ArchiveActionIcon />}
                label={selectedMessage?.archived ? 'Unarchive' : 'Archive'}
                aria-pressed={selectedMessage ? Boolean(selectedMessage.archived) : undefined}
                onClick={toggleArchive}
                disabled={!selectedMessage}
              />
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
            <StructuredInboxBody key={selectedMessage.id} message={selectedMessage} inboxUrl={inboxUrl} />
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
