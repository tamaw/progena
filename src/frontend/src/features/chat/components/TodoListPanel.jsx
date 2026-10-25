/**
 * Live plan checklist rendered inside a `todos_update` timeline entry
 * (see TimelineItem.jsx). Purely presentational -- `todos` is the agent's
 * current plan snapshot from write_todos. The entry itself already shows the
 * active item and "n/total done" in its header, so this renders the progress
 * bar as a closed-by-default disclosure (same chevron as tool rows) whose
 * body is the checklist. Renders nothing once there are no todos.
 */

const STATUS_GLYPH = {
  pending: '○',
  in_progress: '◉',
  completed: '✓',
};

export default function TodoListPanel({ todos }) {
  if (!Array.isArray(todos) || todos.length === 0) {
    return null;
  }

  const completedCount = todos.filter((todo) => todo.status === 'completed').length;
  const progressPct = Math.round((completedCount / todos.length) * 100);

  return (
    <details className="todo-panel tool-event-disclosure">
      <summary>
        <div className="todo-panel__bar">
          <div className="todo-panel__bar-fill" style={{ width: `${progressPct}%` }} />
        </div>
        <span className="tool-event-disclosure__chevron" aria-hidden="true" />
      </summary>
      <ul className="todo-panel__list">
        {todos.map((todo, index) => (
          <li
            key={`${index}-${todo.content}`}
            className={`todo-panel__item todo-panel__item--${todo.status || 'pending'}`}
          >
            <span className="todo-panel__glyph" aria-hidden="true">
              {STATUS_GLYPH[todo.status] || STATUS_GLYPH.pending}
            </span>
            <span className="todo-panel__content">{todo.content}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
