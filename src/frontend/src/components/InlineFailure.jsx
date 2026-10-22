import { failureMessage } from '../lib/failures';

export default function InlineFailure({ failure, onRetry, operation = 'Load content' }) {
  if (!failure) return null;
  const { title, message } = failureMessage(failure, operation);
  return (
    <div className="task-alert" role="alert">
      <strong>{title}</strong>
      {message ? <div style={{ whiteSpace: 'pre-wrap' }}>{message}</div> : null}
      {onRetry ? <button type="button" className="task-btn task-btn--secondary" onClick={onRetry}>Retry</button> : null}
    </div>
  );
}
