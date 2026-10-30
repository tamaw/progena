import { memo, useMemo, useSyncExternalStore } from 'react';
import StreamFilterMenu from './StreamFilterMenu';
import DetailHeader, { DetailHeaderAction, DetailHeaderActions } from '../../../components/layout/DetailHeader';
import EditableDetailTitle from '../../../components/EditableDetailTitle';
import InlineFailure from '../../../components/InlineFailure';
import { formatStartedTime } from '../models/conversationModel';
import { conversationUsageDisplay } from '../models/conversationUsageModel';
import { STREAM_EVENT_CATEGORIES, countCategorizedEvents } from '../models/streamFilters';
import { countReasoningTimelineEntries } from '../models/streamEventsModel';
import { CHAT_STATUS } from '../models/chatStatus';

export const LiveConversationHeader = memo(function LiveConversationHeader({
  liveTurnStore,
  isRouteDetailError,
  messages,
  cumulativeUsage,
  firstMessageTime,
  isContentLoading,
  isEmptyConversationPane,
  showEventFilters,
  streamFilters,
  updateStreamFilters,
  threadId,
  chatStatus,
  titleEditor,
  chatActions,
  title,
  canRename,
}) {
  const liveHeaderSnapshot = useSyncExternalStore(
    liveTurnStore.subscribeHeader,
    liveTurnStore.getHeaderSnapshot,
    liveTurnStore.getHeaderSnapshot,
  );
  const usageDisplay = conversationUsageDisplay(cumulativeUsage);
  const committedEventCounts = useMemo(() => {
    const counts = STREAM_EVENT_CATEGORIES.reduce((acc, c) => { acc[c.id] = 0; return acc; }, {});
    for (const message of messages) {
      if (message.role === 'user') continue;
      if (message.role === 'assistant') {
        counts.reasoning += countReasoningTimelineEntries(
          message.streamEvents,
          message.reasoningText,
        );
      }
      countCategorizedEvents(message.streamEvents, counts);
    }
    return counts;
  }, [messages]);
  const liveReasoningEventCount = liveHeaderSnapshot?.reasoningEventCount ?? 0;
  const eventCounts = useMemo(() => ({
    ...committedEventCounts,
    reasoning: committedEventCounts.reasoning + liveReasoningEventCount,
  }), [committedEventCounts, liveReasoningEventCount]);

  return (
    <>
      <DetailHeader
        className="chat-detail-header"
        titleClassName="chat-detail-header__title"
        title={(
          <EditableDetailTitle
            title={isContentLoading ? '' : isRouteDetailError ? 'Conversation unavailable' : (isEmptyConversationPane ? 'No conversation selected' : title)}
            editor={titleEditor}
            maxLength={120}
            ariaLabel="Conversation title"
            entityLabel="conversation"
            disabled={!canRename}
          />
        )}
        actions={(
          <DetailHeaderActions menuLabel="Conversation actions">
            {chatActions.map((action) => (
              <DetailHeaderAction
                key={action.key}
                icon={action.icon}
                label={action.label}
                tooltip={action.label}
                ariaLabel={action.label}
                variant={action.danger ? 'danger' : 'default'}
                className={[action.active ? 'is-active' : '', action.key === 'delete' ? 'chat-detail-header__delete' : ''].filter(Boolean).join(' ')}
                disabled={action.disabled}
                aria-pressed={action.role === 'menuitemcheckbox' ? action.checked : undefined}
                onClick={action.onClick}
              />
            ))}
          </DetailHeaderActions>
        )}
        meta={isContentLoading || isEmptyConversationPane ? [] : [
          <span key="status" className={`chat-page-header__status chat-page-header__status--${chatStatus}`}>
            <span className="chat-page-header__status-dot" aria-hidden="true" />
            {chatStatus === CHAT_STATUS.DELEGATING ? 'Delegating' : chatStatus}
          </span>,
          <span key="started" className="muted">Started {formatStartedTime(firstMessageTime) || 'not started'}</span>,
          <span key="tokens" className="muted">{usageDisplay.output}</span>,
          <span key="reasoning-tokens" className="muted">{usageDisplay.reasoning}</span>,
          threadId ? <span key="id" className="chat-session-id">{threadId}</span> : null,
        ]}
      />
      {titleEditor.error ? <InlineFailure failure={titleEditor.error} operation="Rename conversation" /> : null}
      {!isContentLoading && !isEmptyConversationPane && showEventFilters && (
        <StreamFilterMenu
          filters={streamFilters}
          onChange={updateStreamFilters}
          eventCounts={eventCounts}
        />
      )}
    </>
  );
});
