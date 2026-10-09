import { useState } from 'react';

export function HumanApprovalControls({ approval, onAnswer, disabled }) {
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState(null);
  const responses = approval.allowed_responses ?? ['approve', 'decline'];
  const pending = submitted || approval.answer_pending;
  async function answer(value) {
    setSubmitted(true);
    setError(null);
    try { await onAnswer(approval, value); }
    catch (error) { setSubmitted(false); setError(error.message || 'Could not submit the response'); }
  }
  return (
    <section className="routine-node-inspector__run routine-node-inspector__approval" aria-label="Pending approval">
      <span className="routine-node-inspector__run-output-label" role="status">{pending ? 'Response submitted' : 'Waiting for your response'}</span>
      <p className="routine-node-inspector__approval-message">{approval.message}</p>
      <div className="routine-node-inspector__run-actions">
        {responses.map((response) => (
          <button key={response} type="button" className={response === 'approve' ? 'routine-node-inspector__run-to-here' : 'routine-node-inspector__reset-run'}
            disabled={disabled || pending} onClick={() => answer(response)}>
            {response === 'reject' ? 'Decline' : response.charAt(0).toUpperCase() + response.slice(1)}
          </button>
        ))}
      </div>
      {!responses.length ? <p>No responses are configured for this step.</p> : null}
      {error ? <p className="routine-node-inspector__run-error" role="alert">{error}</p> : null}
    </section>
  );
}
