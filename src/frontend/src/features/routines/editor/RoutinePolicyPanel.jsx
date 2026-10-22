import { useEffect, useMemo, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../../../context/ConfigContext';
import { entityDraftKey } from '../../../store/editorDraftsSlice';
import useEntityDraft from '../../../hooks/useEntityDraft';
import { fetchPolicies, savePolicy, selectPolicies } from '../../settings/state/policies';
import styles from './RoutinePolicyPanel.module.css';
import Select from '../../../components/Select';

const fields = [
  ['attempt_timeout', 'Attempt timeout (seconds)'], ['idle_timeout', 'Idle timeout (seconds)'],
  ['graph_step_limit', 'Graph step limit'], ['concurrency_limit', 'Concurrency limit'],
  ['default_fan_out_limit', 'Default fan-out limit'], ['cache_ttl', 'Cache TTL (seconds)'],
];
const nullable = new Set(['idle_timeout', 'default_fan_out_limit', 'cache_ttl']);
export function RoutinePolicyPanel({ policyId, onSelect, onClose, recordedPolicy, historical }) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const url = getApiUrl('policies');
  const catalog = useSelector(selectPolicies);
  const selected = useMemo(() => catalog.items.find((item) => item.id === policyId), [catalog.items, policyId]);
  const draftKey = entityDraftKey('routine-policy', policyId);
  const [draft, setDraft, controls] = useEntityDraft(draftKey, selected || null);
  useEffect(() => { dispatch(fetchPolicies({ url })); }, [dispatch, url]);
  const workspaceGenerationRef = useRef(0);
  useEffect(() => {
    const invalidateSaveContinuation = () => { workspaceGenerationRef.current += 1; };
    window.addEventListener('progenitor:workspace-switched', invalidateSaveContinuation);
    return () => window.removeEventListener('progenitor:workspace-switched', invalidateSaveContinuation);
  }, []);
  const values = historical ? recordedPolicy?.values : draft?.values;
  function update(key, value, retry = false) {
    setDraft((current) => ({
      ...current,
      values: retry
        ? { ...current.values, retry: { ...current.values.retry, [key]: value } }
        : { ...current.values, [key]: value },
    }));
  }
  async function save(copy = false) {
    if (!draft || controls.saving) return;
    const policy = copy ? { ...draft, id: `policy_${crypto.randomUUID().replaceAll('-', '')}`, name: `${draft.name} copy` } : draft;
    const ticket = controls.beginSave();
    if (!ticket) return;
    const workspaceGeneration = workspaceGenerationRef.current;
    const operation = dispatch(savePolicy({ url, policy }));
    controls.trackSave(ticket, { requestId: operation.requestId, events: ['policies.snapshot'] });
    try {
      await operation.unwrap();
      if (copy && workspaceGeneration === workspaceGenerationRef.current) onSelect(policy.id);
    } catch (err) {
      controls.failSave(ticket, err);
    }
  }
  return <aside className={`agents-graph-editor ${styles.panel}`} aria-label="Routine policy">
    <header className="agents-graph-editor__header"><h3>Policy</h3><button type="button" aria-label="Close policy" onClick={onClose}>×</button></header>
    <div className={styles.body}>
      {!historical && <label>Selected policy<Select
        ariaLabel="Selected policy"
        value={policyId}
        onChange={onSelect}
        options={[
          ...(!selected ? [{ value: policyId, label: `${policyId} (unavailable)` }] : []),
          ...catalog.items.map((item) => ({ value: item.id, label: item.name })),
        ]}
        portal
        className="routine-policy-panel__select nodrag nowheel"
      /></label>}
      {historical && <p>Recorded policy: {recordedPolicy?.id || 'Unavailable'}<br /><small>{recordedPolicy?.revision_id}</small></p>}
      {!historical && <p>Changes apply to new runs of every routine using this policy.</p>}
      {!values ? <p>{catalog.loaded ? 'Policy unavailable.' : 'Loading policy…'}</p> : <fieldset disabled={historical}>
        <label>Default model profile<input aria-label="Default model profile" value={values.default_model_profile || ''} placeholder="System default" onChange={(event) => update('default_model_profile', event.target.value || null)} /></label>
        {fields.map(([key, label]) => <label key={key}>{label}<input aria-label={label} type="number" min={['attempt_timeout', 'cache_ttl'].includes(key) ? 0 : 1} value={values[key] ?? ''} placeholder={nullable.has(key) ? 'No limit' : undefined} onChange={(event) => update(key, event.target.value === '' && nullable.has(key) ? null : Number(event.target.value))} /></label>)}
        <label className={styles.check}><input aria-label="Cache enabled" type="checkbox" checked={values.cache_enabled} onChange={(event) => update('cache_enabled', event.target.checked)} />Cache enabled</label>
        <p>Prompt, Classify and Eval nodes can cache results. An empty TTL means no expiry. Entries may be reused across runs.</p>
        <h4>Retries</h4>
        {Object.entries({ max_retries: 'Maximum retries', initial_interval: 'Initial delay (seconds)', backoff_factor: 'Backoff multiplier', max_interval: 'Maximum delay (seconds)' }).map(([key, label]) => <label key={key}>{label}<input aria-label={label} type="number" step="any" min={key === 'backoff_factor' ? 1 : 0} value={values.retry[key]} onChange={(event) => update(key, Number(event.target.value), true)} /></label>)}
        <label className={styles.check}><input aria-label="Retry jitter" type="checkbox" checked={values.retry.jitter} onChange={(event) => update('jitter', event.target.checked, true)} />Retry jitter</label>
        <label>Retryable errors<Select
          ariaLabel="Retryable errors"
          value={values.retry.retry_on}
          onChange={(next) => update('retry_on', next, true)}
          options={[
            { value: 'transient', label: 'Transient errors' },
            { value: 'model_response', label: 'Transient and model response errors' },
            { value: 'all', label: 'All node errors' },
          ]}
          disabled={historical}
          portal
          className="routine-policy-panel__select nodrag nowheel"
        /></label>
      </fieldset>}
      {(controls.error || catalog.error) && <p role="alert">{controls.error || catalog.error}</p>}
      {controls.saving && <p role="status">Saving…</p>}
      {!historical && draft && <div className={styles.actions}><button type="button" disabled={!controls.hasDraft || controls.saving} onClick={() => save()}>Save policy</button><button type="button" disabled={controls.saving} onClick={() => save(true)}>Create a copy</button></div>}
    </div>
  </aside>;
}
