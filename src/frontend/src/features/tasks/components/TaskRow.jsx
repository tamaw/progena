import { formatCompactRelativeAge } from '../../../lib/displayTime';
import { getCardAssignee, getCardService, normalizePriority, priorityLabel, statusMeta } from '../tasksPageModel';
import { ChevronRightIcon } from './TaskIcons';

// Tiny priority glyph sized to the status dot (~7px): up = high, flag = medium,
// down = low. Colour matches the kanban priority palette via currentColor.
function PriorityIcon({ priority }) {
  const className = `task-pri-ico task-pri-ico--${priority}`;
  if (priority === 'high') {
    return (
      <svg className={className} viewBox="0 0 10 10" width="10" height="10" aria-hidden="true">
        <path d="M5 1.5 9 7.5H1z" fill="currentColor" />
      </svg>
    );
  }
  if (priority === 'low') {
    return (
      <svg className={className} viewBox="0 0 10 10" width="10" height="10" aria-hidden="true">
        <path d="M5 8.5 1 2.5h8z" fill="currentColor" />
      </svg>
    );
  }
  return (
    <svg className={className} viewBox="0 0 10 10" width="10" height="10" aria-hidden="true">
      <path d="M2.4 1v8.2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <path d="M2.9 1.5h4.5l-1.2 1.8 1.2 1.8H2.9z" fill="currentColor" />
    </svg>
  );
}

export function PriorityPill({ priority, inline = false }) {
  return (
    <span className={inline ? 'task-pri-inline' : `task-pri task-pri--${priority}`}>
      {inline ? <PriorityIcon priority={priority} /> : <span className={`task-pdot task-pri-${priority}`} />}
      {inline ? `${priorityLabel(priority)} priority` : priorityLabel(priority)}
    </span>
  );
}

export function TaskRow({ card, index, selected, compact, onSelect }) {
  const priority = normalizePriority(card);
  const assignee = getCardAssignee(card, index);
  const cardStatus = statusMeta(card.status);

  return (
    <article className={`task-card ${selected ? 'is-selected' : ''} ${compact ? 'task-card--compact' : ''}`}>
      <button type="button" className="task-card__button" onClick={() => onSelect(card.id)}>
        <div className="task-card__title">{card.title}</div>
        {compact ? (
          <div className="task-card__foot">
            <PriorityPill priority={priority} />
            <span className="task-dot-sep">·</span>
            <span className="task-svc-plain">{getCardService(card)}</span>
            <span className="task-card__chev"><ChevronRightIcon /></span>
          </div>
        ) : (
          <>
            <div className="task-card__meta">
              <PriorityPill priority={priority} />
              <span className={`task-pri-inline detail-status detail-status--${cardStatus.tone}`}>{cardStatus.label}</span>
              <span className="task-card__age">{formatCompactRelativeAge(card.updated_at)}</span>
            </div>
            <div className="task-card__foot">
              <span className="task-svc"><span className="task-svc-ic" />{getCardService(card)}</span>
              <span className="task-assignee"><span className="adot" style={{ background: assignee.color }} />{assignee.name}</span>
            </div>
          </>
        )}
      </button>
    </article>
  );
}
