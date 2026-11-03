import { useCallback, useSyncExternalStore } from 'react';
import { formatTimestamp } from '../models/messageRenderUtils';
import { currentTodo, todoProgress } from '../models/workGroupModel';
import { buildDefaultAssistantTimeline } from '../models/liveAssistantTurnModel';
import { TodoChecklist, TodoProgress } from './TodoListPanel';
import { EntryLine } from './ChatEntryLine';
import StickyChatEntry from './StickyChatEntry';
import entryStyles from './StickyChatEntry.module.css';
import styles from './StickyTodoProgress.module.css';

const EMPTY_TODOS = Object.freeze([]);
const EMPTY_PLAN = Object.freeze({ metadata: Object.freeze({ todos: EMPTY_TODOS }) });
const subscribeNone = () => () => {};

function latestPlanEvent(items) {
  if (!Array.isArray(items)) return EMPTY_PLAN;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const event = items[index]?.event;
    if (event?.eventType === 'todos_update' && Array.isArray(event.metadata?.todos)) {
      return event;
    }
  }
  return EMPTY_PLAN;
}

function planFromMessage(message) {
  return latestPlanEvent(Array.isArray(message?.timelineItems)
    ? message.timelineItems : buildDefaultAssistantTimeline(message));
}

function latestPersistedPlan(messages) {
  if (!Array.isArray(messages)) return EMPTY_PLAN;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    // A new user turn must not inherit the previous reply's completed plan.
    if (messages[index]?.role === 'user') return EMPTY_PLAN;
    if (messages[index]?.role !== 'assistant') continue;
    return planFromMessage(messages[index]);
  }
  return EMPTY_PLAN;
}

/**
 * A local live-turn subscriber for the compact plan nearest the composer.
 * Its snapshot is the authoritative event object, so unrelated streamed
 * tokens retain the same reference and do not rerender this component.
 */
export default function StickyTodoProgress({ liveTurnStore, isActive, messages }) {
  const getPlanSnapshot = useCallback(() => {
    const turn = liveTurnStore?.getSnapshot();
    return turn ? latestPlanEvent(turn.timelineItems) : null;
  }, [liveTurnStore]);
  const livePlan = useSyncExternalStore(
    liveTurnStore?.subscribe ?? subscribeNone,
    getPlanSnapshot,
    getPlanSnapshot,
  );
  if (!isActive) return null;

  // A live turn is authoritative; only use persisted data when reopening an
  // already-running thread before its response stream has reattached.
  const plan = livePlan ?? latestPersistedPlan(messages);
  const todos = plan.metadata.todos;
  if (todos.length === 0) return null;

  const { completed, total } = todoProgress(todos);
  if (completed === total) return null;

  const current = currentTodo(todos);
  const currentText = current?.content || 'Plan';
  const time = formatTimestamp(plan.timestamp);
  const summaryLabel = `Current plan, ${completed} of ${total} completed: ${currentText}`;

  return (
    <section className={`chat-transcript ${entryStyles.region} ${styles.panel}`} aria-label="Current plan">
      <StickyChatEntry badgeClassName="entry__badge--plan" active>
        <EntryLine
          title="Todo"
          text={(
            <>
              <span className="tool-call-entry__summary">{currentText}</span>
              <span className="tool-call-entry__plan">
                <TodoProgress todos={todos} />
                <span className="muted">{completed}/{total}</span>
              </span>
            </>
          )}
          trailing={time && <span className="msg__time entry__time">{time}</span>}
          summaryLabel={summaryLabel}
        >
          <div className="tool-call-entry__detail">
            <div className="todo-panel">
              <TodoChecklist todos={todos} />
            </div>
          </div>
        </EntryLine>
      </StickyChatEntry>
    </section>
  );
}
