import { useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../../context/ConfigContext';
import { fetchPolicies, savePolicy, selectPolicies } from '../../store/uiDataSlice';
import styles from './RoutinePolicyPanel.module.css';
import Select from '../common/Select';

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
  const [localDraft, setDraft] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState(null);
  const [status, setStatus] = useState('');
  useEffect(() => { dispatch(fetchPolicies({ url })); }, [dispatch, url]);
  const draft = dirty ? localDraft : selected;
  const values = historical ? recordedPolicy?.values : draft?.values;
  function update(key, value, retry = false) {
    setDirty(true); setStatus('');
    setDraft({ ...draft, values: retry
      ? { ...draft.values, retry: { ...draft.values.retry, [key]: value } }
      : { ...draft.values, [key]: value } });
  }
  async function save(copy = false) {
    setError(null); setStatus('Saving…');
    const policy = copy ? { ...draft, id: `policy_${crypto.randomUUID().replaceAll('-', '')}`, name: `${draft.name} copy` } : draft;
    try {
      await dispatch(savePolicy({ url, policy })).unwrap();
      setDirty(false); setStatus('Saved');
      if (copy) onSelect(policy.id);
    } catch (err) { setError(err.message || 'Unable to save policy'); setStatus(''); }
  }
  return <aside className={`agents-graph-editor ${styles.panel}`} aria-label="Routine policy">
    <header className="agents-graph-editor__header"><h3>Policy</h3><button type="button" aria-label="Close policy" onClick={onClose}>×</button></header>
    <div className={styles.body}>
      {!historical && <label>Selected policy<Select
        ariaLabel="Selected policy"
        value={policyId}
        onChange={(next) => { setDirty(false); setStatus(''); onSelect(next); }}
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
      {(error || catalog.error) && <p role="alert">{error || catalog.error}</p>}
      {status && <p role="status">{status}</p>}
      {!historical && draft && <div className={styles.actions}><button type="button" disabled={!dirty || status === 'Saving…'} onClick={() => save()}>Save policy</button><button type="button" disabled={status === 'Saving…'} onClick={() => save(true)}>Create a copy</button></div>}
    </div>
  </aside>;
}
