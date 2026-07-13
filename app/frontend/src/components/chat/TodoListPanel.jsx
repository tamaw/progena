/**
 * Live plan checklist rendered inside a `todos_update` timeline entry
 * (see TimelineItem.jsx). Purely presentational -- `todos` is the agent's
 * current plan snapshot from write_todos. The entry itself already shows a
 * "Plan" badge/title and "n/total done" count in its header, so this only
 * renders the progress bar and the checklist body. Renders nothing once
 * there are no todos.
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
    <div className="todo-panel">
      <div className="todo-panel__bar">
        <div className="todo-panel__bar-fill" style={{ width: `${progressPct}%` }} />
      </div>
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
    </div>
  );
}
