import { PaneCollapseSeam, PaneEmptyState, PaneExpandRail, PaneListFilters, PaneListSearch, PaneListSortButton, PaneListToolbar, PaneListRows, PaneSection } from '../../../components/layout/PaneList';

// Sort modes for the chat sidebar dropdown (mirrors the Agents/Tasks dropdown).
// "Recent" keeps the recency-bucketed grouping; "Name" is a flat A→Z list.
const SIDEBAR_SORT_MODES = ['Recent', 'Name'];

const CHAT_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'unread', label: 'Unread' },
  { value: 'error', label: 'Error' },
  { value: 'archived', label: 'Archived' },
];

function SessionItem({ item, selectSessionItem }) {
  return (
    <article
      className={`chat-session-card${item.selected ? ' is-selected' : ''}${item.unread ? ' is-unread' : ''}`}
      data-thread-id={item.threadId || item.id}
      data-chat-source={item.sessionSource || item.source || 'unknown'}
      role="button"
      tabIndex={0}
      aria-current={item.selected ? 'true' : undefined}
      onClick={() => {
        void selectSessionItem(item);
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          void selectSessionItem(item);
        }
      }}
    >
      <div className="chat-session-card__top">
        <span
          className={`chat-session-card__dot chat-session-card__dot--${item.status || 'idle'}`}
          aria-hidden="true"
        />
        <span className="chat-session-card__title">{item.title}</span>
        <span className="chat-session-card__age">{item.meta}</span>
      </div>
      <div className="chat-session-card__preview">
        {item.detail && (
          <span className="chat-session-card__state">{item.detail}</span>
        )}
        {item.preview}
      </div>
    </article>
  );
}

export function ConversationSessionsPane({ sessionsPaneClassName, sessionsCollapsed, setSessionsCollapsed, sidebarFilter, setSidebarFilter, startNewChatSession, isClearingSession, sidebarQuery, setSidebarQuery, sidebarSort, setSidebarSort, visibleSessionGroups, virtualList, sidebarEmptyMessageText, selectSessionItem }) {
  return (
<PaneSection as="aside" className={sessionsPaneClassName} aria-label="Conversation sessions" onItemSelect={() => setSessionsCollapsed(true)}>
        {sessionsCollapsed ? (
          <PaneExpandRail label="Chats" onExpand={() => setSessionsCollapsed(false)} />
        ) : (
        <>
        <PaneCollapseSeam label="Chats" onCollapse={() => setSessionsCollapsed(true)} />
        <PaneListFilters
          filters={CHAT_FILTERS}
          activeFilter={sidebarFilter}
          onFilterChange={setSidebarFilter}
          actionLabel="+ New"
          onAction={() => startNewChatSession()}
          actionDisabled={isClearingSession}
        />
        <PaneListToolbar>
          <PaneListSearch value={sidebarQuery} placeholder="Search chats" onChange={setSidebarQuery} />
          <PaneListSortButton
            title="Sort chats"
            options={SIDEBAR_SORT_MODES}
            value={sidebarSort}
            onSelect={setSidebarSort}
          />
        </PaneListToolbar>
        {(virtualList ? virtualList.total === 0 : visibleSessionGroups.length === 0) ? (
          <PaneEmptyState>{sidebarEmptyMessageText}</PaneEmptyState>
        ) : (
          <PaneListRows
            groups={virtualList ? undefined : visibleSessionGroups.map((group) => ({
              key: group.key,
              title: group.label,
              items: group.items,
            }))}
            virtual={virtualList}
            renderItem={(item) => <SessionItem item={item} selectSessionItem={selectSessionItem} />}
          />
        )}
        </>
        )}
      </PaneSection>
  );
}
