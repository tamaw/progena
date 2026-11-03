/**
 * One tool call (see mergeToolCallItems in workGroupModel.js) on one line:
 * tool name, a summary of its input, then duration, time and a chevron.
 * Opening it shows matching Input and Output boxes (ToolIOBox) shaped per
 * tool by toolViewModel.js.
 *
 * A write_todos call that produced a plan update renders as that plan
 * instead: "Todo", the current item, an inline progress bar and
 * "n/total", opening to the checklist.
 */

import { memo, useMemo } from 'react';
import { formatDuration, formatTimestamp, formatToolInputSummary, prettifyLabel } from '../models/messageRenderUtils';
import { isToolEventFailure } from '../models/toolFailureModel';
import { buildToolCallView } from '../models/toolViewModel';
import { currentTodo, todoProgress } from '../models/workGroupModel';
import { TodoChecklist, TodoProgress } from './TodoListPanel';
import ToolIOBox from './ToolIOBox';
import { EntryLine, Trailing } from './ChatEntryLine';


function PlanLine({ plan, time, children }) {
  const todos = Array.isArray(plan.event?.metadata?.todos) ? plan.event.metadata.todos : [];
  const { completed, total } = todoProgress(todos);
  const current = currentTodo(todos);
  const hasBody = todos.length > 0 || Boolean(children);
  return (
    <EntryLine
      title="Todo"
      text={(
        <>
          {current?.content && <span className="tool-call-entry__summary">{current.content}</span>}
          <span className="tool-call-entry__plan">
            <TodoProgress todos={todos} />
            <span className="muted">{completed}/{total}</span>
          </span>
        </>
      )}
      trailing={<Trailing time={time} />}
    >
      {hasBody && (
        <div className="tool-call-entry__detail">
          {children}
          {todos.length > 0 && <div className="todo-panel"><TodoChecklist todos={todos} /></div>}
        </div>
      )}
    </EntryLine>
  );
}

function ToolCallDetail({ view }) {
  return (
    <>
      <ToolIOBox view={view.input} />
      <ToolIOBox view={view.output} />
    </>
  );
}

const ToolCallEntry = memo(function ToolCallEntry({ item }) {
  const start = item.start?.event || null;
  const end = item.end?.event || null;
  const metadata = (start || end)?.metadata || {};
  const toolName = typeof metadata.tool_name === 'string' && metadata.tool_name ? metadata.tool_name : 'tool';
  const status = !end ? 'running' : isToolEventFailure(end) ? 'error' : 'success';
  const time = formatTimestamp(start?.timestamp ?? end?.timestamp);
  const badgeClass = status === 'error'
    ? 'entry__badge--err'
    : item.plan ? 'entry__badge--plan' : 'entry__badge--tool';

  const view = useMemo(() => buildToolCallView({
    toolName,
    argsPreview: start?.metadata?.args_preview,
    resultText: end?.metadata?.result_preview,
    errorText: typeof end?.metadata?.error === 'string' ? end.metadata.error : '',
    artifact: end?.metadata?.artifact,
    status,
  }), [toolName, start, end, status]);

  let line;
  if (item.plan) {
    line = (
      <PlanLine plan={item.plan} time={time}>
        {status === 'error' && <ToolCallDetail view={view} />}
      </PlanLine>
    );
  } else {
    const summary = start ? formatToolInputSummary(toolName, start.metadata?.args_preview) : (view.output?.meta || '');
    const durationMs = end?.metadata?.duration_ms;
    const hasBody = Boolean(view.input || view.output);
    line = (
      <EntryLine
        title={prettifyLabel(toolName)}
        text={summary && <span className="tool-call-entry__summary">{summary}</span>}
        trailing={<Trailing duration={Number.isFinite(durationMs) ? formatDuration(durationMs) : ''} time={time} />}
      >
        {hasBody && (
          <div className="tool-call-entry__detail">
            <ToolCallDetail view={view} />
          </div>
        )}
      </EntryLine>
    );
  }

  return (
    <div className="chat-entry-chain--item">
      <article className={`message-stream-event chat-event-entry entry entry--chain-item tool-call-entry tool-call-entry--${status}`} role="listitem">
        <div className="entry__gutter" aria-hidden="true">
          <span className={`entry__badge ${badgeClass}${status === 'running' ? ' work-step__badge--running' : ''}`} />
        </div>
        <div className="entry__main">{line}</div>
      </article>
    </div>
  );
});


export default ToolCallEntry;
