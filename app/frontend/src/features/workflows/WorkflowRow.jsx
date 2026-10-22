import { formatRelativeTime } from './runs/runModel';
import { workflowDotTone } from './runs/runModel';

export function WorkflowRow({ item, latestRun, selectedWorkflowId, onSelect }) {
  const dotTone = workflowDotTone(item, latestRun);
  return (
    <button
      type="button"
      className={`pane-card-row ${selectedWorkflowId === item.id ? 'active' : ''}`}
      onClick={() => onSelect(item.id)}
      title={item.description || item.title}
    >
      <span className={`pane-card-row__dot${dotTone ? ` pane-card-row__dot--${dotTone}` : ''}`} aria-hidden="true" />
      <div className="pane-card-row__body">
        <div className="pane-card-row__top">
          <span className="pane-card-row__title">{item.title}</span>
          <span className="pane-card-row__age">{formatRelativeTime(latestRun?.updated_at)}</span>
        </div>
        <div className="pane-card-row__preview">{item.description || 'No description yet.'}</div>
        {(item.triggers || []).length > 0 ? (
          <div className="pane-card-row__meta">{item.triggers.join(' · ')}</div>
        ) : null}
      </div>
    </button>
  );
}
