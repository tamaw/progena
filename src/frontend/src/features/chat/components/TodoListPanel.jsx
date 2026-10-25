/**
 * Plan checklist pieces for `todos_update` snapshots (the agent's current
 * plan from write_todos). A plan usually renders as its write_todos call's
 * one-line entry (ToolCallEntry.jsx) with an inline TodoProgress and the
 * TodoChecklist behind its chevron; TodoListPanel is the standalone form for
 * a plan update with no call to attach to: the progress bar as a
 * closed-by-default disclosure whose body is the checklist.
 */

import { todoProgress } from '../models/workGroupModel';

const STATUS_GLYPH = {
  pending: '○',
  in_progress: '◉',
  completed: '✓',
};

export function TodoProgress({ todos }) {
  const { percent } = todoProgress(todos);
  return (
    <div className="todo-panel__bar">
      <div className="todo-panel__bar-fill" style={{ width: `${percent}%` }} />
    </div>
  );
}

export function TodoChecklist({ todos }) {
  return (
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
  );
}

export default function TodoListPanel({ todos }) {
  if (!Array.isArray(todos) || todos.length === 0) {
    return null;
  }
  const { completed, total } = todoProgress(todos);
  return (
    <details className="todo-panel tool-event-disclosure">
      <summary aria-label={`Plan checklist, ${completed} of ${total} completed`}>
        <TodoProgress todos={todos} />
        <span className="tool-event-disclosure__chevron" aria-hidden="true" />
      </summary>
      <TodoChecklist todos={todos} />
    </details>
  );
}
