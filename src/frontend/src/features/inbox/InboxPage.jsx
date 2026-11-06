import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../../lib/routes';
import { dispatchQuickChatOpen, QUICK_CHAT_MODE } from '../../lib/browserContracts';
import PageShell from '../../components/layout/PageShell';
import DetailPane from '../../components/layout/DetailPane';
import DetailHeader, {
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  ApproveActionIcon,
  ArchiveActionIcon,
  DeleteActionIcon,
  EditActionIcon,
  RejectActionIcon,
} from '../../components/layout/DetailHeader';
import {
  PaneEmptyState,
  PaneList,
  PaneListFilters,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListRows,
} from '../../components/layout/PaneList';
import { useConfig } from '../../context/ConfigContext';
import { formatStartedTime } from '../chat/models/conversationModel';
import { selectRealtimeStreamReady } from '../../realtime/realtimeSlice';
import usePositionalListRange from '../../hooks/usePositionalListRange';
import useStreamResync from '../../hooks/useStreamResync';
import useStreamTopic from '../../hooks/useStreamTopic';
import {
  archiveInboxMessage,
  decideInboxMessage,
  deleteInboxMessage,
  fetchInboxList,
  INBOX_LIST_PAGE_SIZE,
  markInboxMessageRead,
  normalizeInboxView,
  requestInboxMessageDetail,
  sameInboxView,
  selectInboxState,
  setInboxMessageInterest,
} from './state';
import { formatCompactRelativeAge } from '../../lib/displayTime';
import InlineFailure from '../../components/InlineFailure';
import StructuredInboxBody from './StructuredInboxBody';
import {
  getInboxMessageStatus,
  inboxMessageNeedsDecision,
  INBOX_FILTERS,
  INBOX_STATUS_META,
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

function previewText(preview) {
  const firstLine = String(preview || '').trim();
  return firstLine.length > 120 ? `${firstLine.slice(0, 120)}…` : firstLine;
}

export default function InboxPage() {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { messageId: urlMessageId } = useParams();
  const routedMessageId = typeof urlMessageId === 'string' && urlMessageId.trim() ? urlMessageId : null;
  const { getApiUrl } = useConfig();
  const inboxUrl = getApiUrl('inbox');
  const streamReady = useSelector(selectRealtimeStreamReady);
  const inboxState = useSelector(selectInboxState);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('Recent');
  const view = useMemo(() => normalizeInboxView({
    q: query,
    filter,
    sort: sort === 'Name' ? 'name' : 'recent',
  }), [filter, query, sort]);
  useStreamTopic('inbox');


  const fetchPage = useCallback(
    ({ view: pageView, offset, limit }) => dispatch(fetchInboxList({ inboxUrl, view: pageView, offset, limit })),
    [dispatch, inboxUrl],
  );
  // PaneListRows requests scroll ranges once it exists; the hook also covers its
  // empty initial layout, debounces server-side search, and reloads on (re)connect.
  const requestListRange = usePositionalListRange({
    list: inboxState,
    view,
    sameView: sameInboxView,
    streamReady,
    pageSize: INBOX_LIST_PAGE_SIZE,
    fetchPage,
  });

  const selectedMessageId = routedMessageId;
  const selectedRow = selectedMessageId ? inboxState.byId[selectedMessageId] || null : null;
  const selectedDetail = inboxState.active.messageId === selectedMessageId
    ? inboxState.active.detail
    : null;
  const selectedMessage = selectedDetail || selectedRow;

  // A deletion is an explicit absence signal; unlike an unloaded deep-link
  // row, it may safely clear the selection route.
  useEffect(() => {
    if (!selectedMessageId || !inboxState.tombstones[selectedMessageId]) return;
    navigate('/inbox', { replace: true });
  }, [inboxState.tombstones, navigate, selectedMessageId]);

  // A deep link can target a row that is outside the list window. Its direct
  // detail request is authoritative; an unloaded sidebar row never clears it.
  const requestedDetailRef = useRef(null);
  // A reconnect can have missed a selected-only update, and the server drops the
  // selected-message interest with the stream. Ask for the open message again:
  // GET selection both rehydrates the body and installs the renewed interest.
  const openMessageIdRef = useRef(selectedMessageId);
  useEffect(() => {
    openMessageIdRef.current = selectedMessageId;
  }, [selectedMessageId]);
  useStreamResync(() => {
    const messageId = openMessageIdRef.current;
    if (!messageId) return;
    requestedDetailRef.current = `${inboxUrl}:${messageId}`;
    dispatch(requestInboxMessageDetail({ inboxUrl, messageId }));
  });
  useEffect(() => {
    if (!streamReady || !selectedMessageId) return;
    const detailKey = `${inboxUrl}:${selectedMessageId}`;
    if (requestedDetailRef.current === detailKey) return;
    requestedDetailRef.current = detailKey;
    dispatch(requestInboxMessageDetail({ inboxUrl, messageId: selectedMessageId }));
  }, [dispatch, inboxUrl, selectedMessageId, streamReady]);
  useEffect(() => () => {
    dispatch(setInboxMessageInterest({ inboxUrl, messageId: null, watching: false }));
  }, [dispatch, inboxUrl]);
  useEffect(() => {
    if (selectedMessageId || !requestedDetailRef.current) return;
    requestedDetailRef.current = null;
    dispatch(setInboxMessageInterest({ inboxUrl, messageId: null }));
  }, [dispatch, inboxUrl, selectedMessageId]);

  const hasAutoSelectedRef = useRef(false);
  useEffect(() => {
    if (!inboxState.loaded || routedMessageId || hasAutoSelectedRef.current) return;
    hasAutoSelectedRef.current = true;
    const firstId = inboxState.rowsByIndex[0];
    const firstMessage = firstId ? inboxState.byId[firstId] : null;
    if (firstMessage) {
      navigate(buildEntityPath('/inbox', firstMessage.id, firstMessage.title), { replace: true });
    }
  }, [inboxState.byId, inboxState.loaded, inboxState.rowsByIndex, navigate, routedMessageId]);

  const selectMessage = useCallback((message) => {
    navigate(buildEntityPath('/inbox', message.id, message.title), { replace: true });
  }, [navigate]);

  // The full selected detail, not a sidebar cache row, controls the one-time
  // read acknowledgement. A manual unread toggle therefore remains explicit.
  const autoMarkedReadIdRef = useRef(null);
  useEffect(() => {
    if (!selectedDetail?.id || autoMarkedReadIdRef.current === selectedDetail.id) return;
    autoMarkedReadIdRef.current = selectedDetail.id;
    if (!selectedDetail.read) {
      dispatch(markInboxMessageRead({ inboxUrl, messageId: selectedDetail.id, read: true }));
    }
  }, [dispatch, inboxUrl, selectedDetail]);

  useEntityUrlSync('/inbox', selectedMessageId, selectedMessage?.title);

  const toggleRead = useCallback(() => {
    if (!selectedMessageId || !selectedMessage) return;
    dispatch(markInboxMessageRead({
      inboxUrl,
      messageId: selectedMessageId,
      read: !selectedMessage.read,
    }));
  }, [dispatch, inboxUrl, selectedMessage, selectedMessageId]);
  const decide = useCallback((decision) => {
    if (selectedMessageId) dispatch(decideInboxMessage({ inboxUrl, messageId: selectedMessageId, decision }));
  }, [dispatch, inboxUrl, selectedMessageId]);
  const toggleArchive = useCallback(() => {
    if (!selectedMessageId || !selectedMessage) return;
    dispatch(archiveInboxMessage({
      inboxUrl,
      messageId: selectedMessageId,
      archived: !selectedMessage.archived,
    }));
    navigate('/inbox', { replace: true });
  }, [dispatch, inboxUrl, navigate, selectedMessage, selectedMessageId]);
  const removeMessage = useCallback(() => {
    if (!selectedMessageId) return;
    dispatch(deleteInboxMessage({ inboxUrl, messageId: selectedMessageId }));
    navigate('/inbox', { replace: true });
  }, [dispatch, inboxUrl, navigate, selectedMessageId]);
  const openCompose = useCallback(() => {
    dispatchQuickChatOpen({ mode: QUICK_CHAT_MODE.INBOX_COMPOSE });
  }, []);
  const openEditInChat = useCallback(() => {
    if (selectedMessageId) {
      dispatchQuickChatOpen({ mode: QUICK_CHAT_MODE.INBOX_EDIT, subtitle: selectedMessage?.title });
    }
  }, [selectedMessage?.title, selectedMessageId]);

  const listEmptyMessage = query.trim()
    ? 'No messages match your search'
    : filter === 'unread'
      ? 'No unread messages'
      : filter === 'pending'
        ? 'No messages pending approval'
        : filter === 'archived'
          ? 'No archived messages'
          : 'No messages yet';
  const selectedStatus = getInboxMessageStatus(selectedMessage);
  const virtualList = useMemo(() => ({
    total: inboxState.total,
    rowHeight: 51,
    headerHeight: 0,
    groups: [],
    rowAt: (index) => {
      const messageId = inboxState.rowsByIndex[index];
      return messageId ? inboxState.byId[messageId] : undefined;
    },
    onRangeNeeded: requestListRange,
    refreshKey: inboxState.listRefreshVersion || streamReady,
    rowDelta: inboxState.rowDelta,
  }), [
    inboxState.byId,
    inboxState.listRefreshVersion,
    inboxState.rowDelta,
    inboxState.rowsByIndex,
    inboxState.total,
    requestListRange,
    streamReady,
  ]);

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
          <PaneListSortButton title="Sort messages" options={INBOX_SORT_MODES} value={sort} onSelect={setSort} />
        </PaneListToolbar>
        <InlineFailure failure={inboxState.error} operation="Load messages" onRetry={() => requestListRange(0, INBOX_LIST_PAGE_SIZE, true)} />
        {inboxState.total === 0 ? (
          !inboxState.error && <PaneEmptyState>{listEmptyMessage}</PaneEmptyState>
        ) : (
          <PaneListRows
            virtual={virtualList}
            getItemKey={(message) => message.id}
            renderItem={(message) => {
              const status = getInboxMessageStatus(message);
              const preview = previewText(message.preview);
              return (
                <article
                  className={`chat-session-card${message.id === selectedMessageId ? ' is-selected' : ''}${status === 'unread' ? ' is-unread' : ''}`}
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
                    <span className={`chat-session-card__dot ${INBOX_STATUS_META[status].dotClass}`} aria-hidden="true" />
                    <span className="chat-session-card__title">{message.title || message.id}</span>
                    <span className="chat-session-card__age">{formatCompactRelativeAge(message.created_at)}</span>
                  </div>
                  {preview ? <div className="chat-session-card__preview">{preview}</div> : null}
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
            : (inboxState.total === 0 ? 'Inbox is empty' : 'No message selected')}
          meta={selectedMessage ? [
            <DetailStatus key="status" tone={INBOX_STATUS_META[selectedStatus].tone}>
              {INBOX_STATUS_META[selectedStatus].label}
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
              ? <span key="read" className="muted">Read {formatStartedTime(selectedMessage.read_at)}</span>
              : null,
            <span key="id" className="taskid">#{selectedMessage.id}</span>,
          ] : []}
          actions={(
            <DetailHeaderActions>
              {inboxMessageNeedsDecision(selectedMessage) && (
                <DetailHeaderAction icon={<ApproveActionIcon />} label="Approve" onClick={() => decide('approved')} />
              )}
              {inboxMessageNeedsDecision(selectedMessage) && (
                <DetailHeaderAction icon={<RejectActionIcon />} label="Reject" onClick={() => decide('rejected')} />
              )}
              <DetailHeaderAction icon={<EditActionIcon />} label="Edit in chat" onClick={openEditInChat} disabled={!selectedMessage} />
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
          {selectedDetail ? (
            <StructuredInboxBody key={selectedDetail.id} message={selectedDetail} />
          ) : selectedMessageId && inboxState.active.loading ? (
            <div className="welcome-message"><h2>Loading message…</h2></div>
          ) : inboxState.active.error ? (
            <InlineFailure
              failure={inboxState.active.error}
              operation="Load message"
              onRetry={() => dispatch(requestInboxMessageDetail({ inboxUrl, messageId: selectedMessageId }))}
            />
          ) : (
            <div className="welcome-message">
              <h2>{inboxState.total === 0 ? 'Your inbox is empty' : 'No message selected'}</h2>
              <p className="muted">
                {inboxState.total === 0
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
