import { useState } from 'react';
import { useDispatch } from 'react-redux';
import { useConfig } from '../../../context/ConfigContext';
import { answerWorkflowRunApproval } from '../state/workflows';
import { PaneEmptyState } from '../../../components/layout/PaneList';
import { formatRelativeTime } from './runModel';

export function WorkflowRunsList({ runs, loading, workflowId }) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const [approvalError, setApprovalError] = useState('');
  const answerApproval = async (runId, approved) => {
    setApprovalError('');
    try {
      await dispatch(answerWorkflowRunApproval({
        workflowsUrl: getApiUrl('workflows'), workflowId, runId, approved,
      })).unwrap();
    } catch (error) {
      setApprovalError(error?.message || 'Update approval');
    }
  };
  if (loading) {
    return <PaneEmptyState>Loading workflow runs...</PaneEmptyState>;
  }
  if (!runs.length) {
    return <PaneEmptyState>No runs yet. Trigger the workflow to inspect its execution path.</PaneEmptyState>;
  }
  return (
    <div className="workflow-runs">
      {runs.map((run) => {
        const runTokens = Number.isFinite(run.tokens?.total_tokens) ? run.tokens.total_tokens : null;
        const stepAudits = Array.isArray(run.step_audit)
          ? run.step_audit.filter((step) => Number.isFinite(step.tokens?.total_tokens))
          : [];
        return (
          <article key={run.run_id} className="workflow-run-card">
            <div className="workflow-run-card__head">
              <strong>{run.status}</strong>
              <span>{formatRelativeTime(run.updated_at || run.queued_at)}</span>
            </div>
            <div className="workflow-run-card__meta">
              <span>Run {run.run_id}</span>
              {run.current_node_id ? <span>Current: {run.current_node_id}</span> : null}
              {run.thread_id ? <span>Thread: {run.thread_id}</span> : null}
              {runTokens !== null ? <span>Total: {runTokens} tok</span> : null}
            </div>
            {stepAudits.length ? (
              <div className="workflow-run-card__steps" aria-label="Step token usage">
                {stepAudits.map((step, index) => (
                  <span key={`${step.node_id || 'step'}-${index}`}>
                    {step.node_id || 'Step'}: {step.tokens.total_tokens} tok
                  </span>
                ))}
              </div>
            ) : null}
            {run.pending_approval ? (
              <div className="workflow-run-card__approval">
                <p>{run.pending_approval.message || 'Approval required.'}</p>
                <button type="button" onClick={() => answerApproval(run.run_id, true)}>Approve</button>
                <button type="button" onClick={() => answerApproval(run.run_id, false)}>Reject</button>
                {approvalError ? <p className="workflow-run-card__error">{approvalError}</p> : null}
              </div>
            ) : null}
            {run.last_error ? <p className="workflow-run-card__error">{run.last_error}</p> : null}
            {run.last_output ? <pre className="workflow-run-card__output">{run.last_output}</pre> : null}
          </article>
        );
      })}
    </div>
  );
}
