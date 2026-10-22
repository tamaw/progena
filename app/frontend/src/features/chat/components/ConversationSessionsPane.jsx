import { PaneCollapseSeam, PaneEmptyState, PaneExpandRail, PaneListFilters, PaneListSearch, PaneListSortButton, PaneListToolbar, PaneListRows, PaneSection } from '../../../components/layout/PaneList';

// Sort modes for the chat sidebar dropdown (mirrors the Agents/Tasks dropdown).
// "Recent" keeps the recency-bucketed grouping; "Name" is a flat A→Z list.
const SIDEBAR_SORT_MODES = ['Recent', 'Name'];

const CHAT_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'unread', label: 'Unread' },
  { value: 'archived', label: 'Archived' },
];

export function ConversationSessionsPane({ sessionsPaneClassName, sessionsCollapsed, setSessionsCollapsed, sidebarFilter, setSidebarFilter, hasUnreadChats, startNewChatSession, isClearingSession, sidebarQuery, setSidebarQuery, sidebarSort, setSidebarSort, visibleSessionGroups, sidebarEmptyMessageText, selectSessionItem }) {
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
          renderFilter={(item) => (
            <>
              {item.value === 'unread' && hasUnreadChats ? <span className="chat-pane-tab__dot" aria-hidden="true" /> : null}
              {item.label}
            </>
          )}
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
        {visibleSessionGroups.length === 0 ? (
          <PaneEmptyState>{sidebarEmptyMessageText}</PaneEmptyState>
        ) : (
          <PaneListRows
            groups={visibleSessionGroups.map((group) => ({
              key: group.key,
              title: group.label,
              items: group.items,
            }))}
            getItemKey={(item) => item.id}
            renderItem={(item) => (
              <article
                className={`chat-session-card${item.selected ? ' is-selected' : ''}${item.unread ? ' is-unread' : ''}`}
                data-thread-id={item.threadId || item.id}
                data-chat-source={item.source || 'unknown'}
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
                {(item.preview || item.detail) && (
                  <div className="chat-session-card__preview">
                    {item.detail && (
                      <span className="chat-session-card__state">{item.detail}</span>
                    )}
                    {item.preview}
                  </div>
                )}
              </article>
            )}
          />
        )}
        </>
        )}
      </PaneSection>
  );
}
