import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../../../context/ConfigContext';
import Select from '../../../components/Select';
import { fetchInstruction, selectInstruction, selectInstructionContentRequest, selectInstructions } from '../../instructions/state';
import { selectAgentProfiles } from '../../agents/state/agents';
import { selectDocumentTypes } from '../../documents/state';
import InlineFailure from '../../../components/InlineFailure';
import Modal from '../../../components/Modal';
import { routineNodeSchemas } from '../models/nodePalette';
import { buildQueryInputSchema } from '../models/querySchema';
import { cloneValue } from '../models/values';
import { formatTemplatePatch, promptTemplatePatch, instructionPatch } from '../models/templates';

// Remounted per-node via `key={node.id}` on WorkflowNodeOptionsModal, so
// lazy initial state is sufficient -- no effect-driven resync needed.
function JsonFieldEditor({ label, value, onCommit }) {
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [error, setError] = useState('');
  return (
    <label className="workflow-node-options__field">
      {label}
      <textarea
        value={text}
        rows={6}
        onChange={(event) => {
          const nextText = event.target.value;
          setText(nextText);
          try {
            const parsed = JSON.parse(nextText);
            setError('');
            onCommit(parsed);
          } catch {
            setError('Invalid JSON');
          }
        }}
      />
      {error ? <small className="workflow-node-options__error">{error}</small> : null}
    </label>
  );
}

export function WorkflowNodeOptionsModal({ node, onChange, onClose }) {

  const kind = node.data?.kind || node.type;
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const instructionsUrl = getApiUrl('instructions');
  const instructionPath = node.data?.instruction || '';
  const loadedInstruction = useSelector((state) => selectInstruction(state, instructionPath));
  const instructionRequest = useSelector((state) => selectInstructionContentRequest(state, instructionPath));

  // Fetch the selected document once, then derive its input ports once per
  // loaded text: `onChange` is a fresh function every render and the graph
  // may normalise the stored schema, so neither may drive a re-apply.
  const requestedInstructionPathRef = useRef(null);
  const appliedInstructionRef = useRef(null);
  const onChangeRef = useRef(onChange);
  const loadInstruction = useCallback(() => {
    if (instructionPath && !instructionRequest.loading) {
      requestedInstructionPathRef.current = instructionPath;
      dispatch(fetchInstruction({ instructionsUrl, path: instructionPath }));
    }
  }, [dispatch, instructionPath, instructionRequest.loading, instructionsUrl]);
  useLayoutEffect(() => {
    onChangeRef.current = onChange;
  });
  useEffect(() => {
    if ((kind !== 'agent' && kind !== 'extract') || !instructionPath) return;
    if (loadedInstruction?.path === instructionPath) {
      const content = loadedInstruction.content || '';
      const applied = appliedInstructionRef.current;
      if (applied?.path === instructionPath && applied.content === content) return;
      appliedInstructionRef.current = { path: instructionPath, content };
      const patch = instructionPatch(instructionPath, content);
      if (JSON.stringify(node.data?.inputSchema) !== JSON.stringify(patch.inputSchema)) {
        onChangeRef.current(node.id, patch);
      }
      return;
    }
    if (instructionRequest.loading || instructionRequest.error || requestedInstructionPathRef.current === instructionPath) return;
    loadInstruction();
  }, [instructionRequest.error, instructionRequest.loading, kind, loadInstruction, node.data?.inputSchema, node.id, loadedInstruction, instructionPath]);

  const documentTypes = useSelector(selectDocumentTypes);

  const documentTypeOptions = useMemo(() =>
    documentTypes.map((dt) => ({ value: dt.id, label: dt.label || dt.id })),
  [documentTypes]);

  const readableTypeOptions = useMemo(() =>
    documentTypes.map((dt) => ({ value: dt.id, label: dt.label || dt.id })),
  [documentTypes]);

  const collectionTypeOptions = useMemo(() =>
    documentTypes.filter((dt) => dt.collection).map((dt) => ({ value: dt.id, label: dt.label || dt.id })),
  [documentTypes]);

  const schemaCollectionTypeOptions = useMemo(() =>
    documentTypes.filter((dt) => dt.collection && dt.document_schema).map((dt) => ({ value: dt.id, label: dt.label || dt.id })),
  [documentTypes]);

  const writableTypeOptions = useMemo(() =>
    documentTypes.filter((dt) => dt.writable_fields?.length).map((dt) => ({ value: dt.id, label: dt.label || dt.id })),
  [documentTypes]);

  const selectedDocumentType = useMemo(() =>
    documentTypes.find((dt) => dt.id === node.data?.documentType) || null,
  [documentTypes, node.data?.documentType]);

  const fieldOptions = useMemo(() => {
    const props = selectedDocumentType?.document_schema?.properties;
    if (!props || typeof props !== 'object') return [];
    return Object.keys(props).map((key) => ({ value: key, label: key }));
  }, [selectedDocumentType]);

  // Bundled agent instructions are filled by their agent's code, not by workflows.
  const instructions = useSelector(selectInstructions);
  const instructionOptions = useMemo(() =>
    instructions
      .filter((item) => item.editable)
      .map((item) => ({ value: item.path, label: item.title || item.path })),
  [instructions]);

  const agentProfiles = useSelector(selectAgentProfiles);
  const subagentOptions = useMemo(() => {
    const names = agentProfiles.map((a) => a.name).filter(Boolean);
    return [...new Set(names)].sort().map((name) => ({ value: name, label: name }));
  }, [agentProfiles]);

  const routineOptions = useMemo(() =>
    agentProfiles
      .filter((agent) => agent.kind === 'routine')
      .map((agent) => ({ value: agent.id, label: agent.name || agent.id })),
  [agentProfiles]);

  const handleRoutineChange = (routineId) => {
    const routine = agentProfiles.find((agent) => agent.kind === 'routine' && agent.id === routineId);
    onChange(node.id, { routine: routineId, ...routineNodeSchemas(routine?.graph) });
  };

  const knownSchema = useMemo(() =>
    documentTypeOptions.some((o) => o.value === node.data?.schema),
  [documentTypeOptions, node.data?.schema]);

  const [cronPreview, setCronPreview] = useState({ nextRuns: [], error: '' });
  const cronAbortRef = useRef(null);
  const cronDebounceRef = useRef(null);

  useEffect(() => {
    if (kind !== 'cron') return undefined;
    const expression = node.data?.expression || '';
    const timezone = node.data?.timezone || '';
    clearTimeout(cronDebounceRef.current);
    if (!expression || !timezone) {
      return () => clearTimeout(cronDebounceRef.current);
    }
    cronDebounceRef.current = setTimeout(() => {
      cronAbortRef.current?.abort();
      const controller = new AbortController();
      cronAbortRef.current = controller;
      fetch(`/api/ui/workflows/cron-preview?expression=${encodeURIComponent(expression)}&timezone=${encodeURIComponent(timezone)}&count=3`, {
        signal: controller.signal,
      })
        .then(async (res) => {
          if (res.status === 200) {
            const data = await res.json();
            setCronPreview({ nextRuns: data.next_runs || [], error: '' });
          } else if (res.status === 400) {
            const data = await res.json();
            setCronPreview({ nextRuns: [], error: data.detail || 'Invalid expression' });
          } else {
            setCronPreview({ nextRuns: [], error: `HTTP ${res.status}` });
          }
        })
        .catch((err) => {
          if (err.name !== 'AbortError') {
            setCronPreview({ nextRuns: [], error: err.message });
          }
        });
    }, 300);
    return () => {
      clearTimeout(cronDebounceRef.current);
    };
  }, [kind, node.data?.expression, node.data?.timezone]);

  const handleEventTypeChange = (documentTypeId) => {
    const dt = documentTypes.find((t) => t.id === documentTypeId);
    onChange(node.id, {
      documentType: documentTypeId,
      outputSchema: cloneValue(dt?.event_output_schema) || null,
      changedFields: [],
    });
  };

  const handleReadTypeChange = (documentTypeId) => {
    const dt = documentTypes.find((t) => t.id === documentTypeId);
    onChange(node.id, {
      documentType: documentTypeId,
      inputSchema: cloneValue(dt?.read_input_schema) || null,
      outputSchema: cloneValue(dt?.read_output_schema) || null,
    });
  };

  const handleQueryTypeChange = (documentTypeId) => {
    const dt = documentTypes.find((t) => t.id === documentTypeId);
    const filters = [];
    onChange(node.id, {
      documentType: documentTypeId,
      filters,
      orderBy: null,
      order: 'asc',
      inputSchema: buildQueryInputSchema(filters, dt?.document_schema),
      outputSchema: cloneValue(dt?.query_output_schema) || null,
    });
  };

  const handleQueryFiltersChange = (filters) => {
    onChange(node.id, {
      filters,
      inputSchema: buildQueryInputSchema(filters, selectedDocumentType?.document_schema),
    });
  };

  const handleDocumentOperationTypeChange = (documentTypeId, operation) => {
    const dt = documentTypes.find((t) => t.id === documentTypeId);
    onChange(node.id, {
      documentType: documentTypeId,
      inputSchema: cloneValue(dt?.[`${operation}_input_schema`]) || null,
      outputSchema: cloneValue(dt?.[`${operation}_output_schema`]) || null,
    });
  };

  return (
    <Modal
      className="workflow-node-options"
      backdropClassName="workflow-node-options-backdrop"
      ariaLabelledby="workflow-node-options-title"
      onClose={onClose}
    >
      <header className="workflow-node-options__head">
          <div>
            <span className="sidebar-modal__eyebrow">Node options</span>
            <h2 id="workflow-node-options-title">{node.data?.title || 'Workflow node'}</h2>
          </div>
          <button type="button" className="sidebar-modal__close" aria-label="Close node options" onClick={onClose}>×</button>
        </header>
        <div className="workflow-node-options__body">
          <section>
            <h3>Configuration</h3>
            <div className="workflow-node-options__mapping-form">
              <label>Title<input value={node.data?.title || ''} onChange={(event) => onChange(node.id, { title: event.target.value })} /></label>
            </div>
            {kind === 'event' ? (
              <>
                <label className="workflow-node-options__field">
                  Document type
                  <Select value={node.data?.documentType || ''} onChange={handleEventTypeChange} options={documentTypeOptions} ariaLabel="Document type" />
                </label>
                <label className="workflow-node-options__field">
                  Key
                  <input
                    value={node.data?.key || ''}
                    onChange={(event) => onChange(node.id, { key: event.target.value || null })}
                    placeholder="Any key"
                  />
                </label>
                <div className="workflow-node-options__field">
                  <span>Operations</span>
                  <div className="workflow-node-options__checkboxes">
                    {['created', 'updated', 'deleted'].map((op) => (
                      <label key={op} className="workflow-node-options__checkbox">
                        <input
                          type="checkbox"
                          checked={node.data?.operations?.includes(op) || false}
                          onChange={(event) => {
                            const current = new Set(node.data?.operations || []);
                            if (event.target.checked) current.add(op);
                            else current.delete(op);
                            onChange(node.id, { operations: Array.from(current) });
                          }}
                        />
                        {op}
                      </label>
                    ))}
                  </div>
                </div>
                <label className="workflow-node-options__field">
                  Changed fields
                  <Select
                    value=""
                    onChange={(field) => {
                      if (!field) return;
                      const current = new Set(node.data?.changedFields || []);
                      current.add(field);
                      onChange(node.id, { changedFields: Array.from(current) });
                    }}
                    options={[{ value: '', label: 'Add field…' }, ...fieldOptions]}
                    ariaLabel="Add changed field"
                  />
                  <div className="workflow-node-options__tags">
                    {(node.data?.changedFields || []).map((field) => (
                      <span key={field} className="workflow-node-options__tag">
                        {field}
                        <button
                          type="button"
                          onClick={() => {
                            const next = (node.data?.changedFields || []).filter((f) => f !== field);
                            onChange(node.id, { changedFields: next });
                          }}
                          aria-label={`Remove ${field}`}
                        >×</button>
                      </span>
                    ))}
                  </div>
                </label>
                <JsonFieldEditor label="Match" value={node.data?.match} onCommit={(match) => onChange(node.id, { match })} />
              </>
            ) : null}
            {kind === 'cron' ? (
              <>
                <label className="workflow-node-options__field">
                  Expression
                  <input
                    value={node.data?.expression || ''}
                    onChange={(event) => onChange(node.id, { expression: event.target.value })}
                    placeholder="*/5 * * * *"
                  />
                </label>
                <div className="workflow-node-options__field">
                  <span>Presets</span>
                  <div className="workflow-node-options__checkboxes">
                    {[
                      { label: 'Every 5 min', expr: '*/5 * * * *' },
                      { label: 'Hourly', expr: '0 * * * *' },
                      { label: 'Daily 9:00', expr: '0 9 * * *' },
                      { label: 'Weekdays 9:00', expr: '0 9 * * 1-5' },
                    ].map((preset) => (
                      <button
                        key={preset.expr}
                        type="button"
                        className="node-action-btn"
                        onClick={() => onChange(node.id, { expression: preset.expr })}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>
                <label className="workflow-node-options__field">
                  Timezone
                  <input
                    value={node.data?.timezone || ''}
                    onChange={(event) => onChange(node.id, { timezone: event.target.value })}
                    placeholder="Europe/London"
                  />
                </label>
                <label className="workflow-node-options__field">
                  Overlap
                  <Select
                    value={node.data?.overlap || 'skip'}
                    onChange={(val) => onChange(node.id, { overlap: val })}
                    options={[{ value: 'skip', label: 'Skip if still running' }, { value: 'queue', label: 'Queue' }]}
                    ariaLabel="Overlap behaviour"
                  />
                </label>
                <div className="workflow-node-options__field">
                  <span>Next runs</span>
                  {cronPreview.error ? (
                    <small className="workflow-node-options__error">{cronPreview.error}</small>
                  ) : (
                    <ul className="workflow-node-options__next-runs">
                      {cronPreview.nextRuns.map((run, i) => (
                        <li key={i}>{run}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </>
            ) : null}
            {kind === 'document_read' ? (
              <>
                <label className="workflow-node-options__field">
                  Document type
                  <Select value={node.data?.documentType || ''} onChange={handleReadTypeChange} options={readableTypeOptions} ariaLabel="Document type" />
                </label>
              </>
            ) : null}
            {kind === 'document_query' ? (
              <>
                <label className="workflow-node-options__field">
                  Document type
                  <Select value={node.data?.documentType || ''} onChange={handleQueryTypeChange} options={collectionTypeOptions} ariaLabel="Document type" />
                </label>
                <div className="workflow-node-options__field">
                  <span>Filters</span>
                  {(node.data?.filters || []).map((filter, index) => (
                    <div key={`${filter.field}-${filter.op}-${index}`} className="workflow-node-options__mapping-form">
                      <Select
                        value={filter.field}
                        onChange={(field) => {
                          const next = cloneValue(node.data?.filters) || [];
                          next[index] = { ...next[index], field };
                          handleQueryFiltersChange(next);
                        }}
                        options={fieldOptions}
                        ariaLabel={`Filter ${index + 1} field`}
                      />
                      <Select
                        value={filter.op}
                        onChange={(op) => {
                          const next = cloneValue(node.data?.filters) || [];
                          next[index] = { ...next[index], op };
                          handleQueryFiltersChange(next);
                        }}
                        options={['eq', 'ne', 'lt', 'lte', 'gt', 'gte', 'in', 'contains', 'exists'].map((op) => ({ value: op, label: op }))}
                        ariaLabel={`Filter ${index + 1} operator`}
                      />
                      <button type="button" className="node-action-btn" onClick={() => {
                        handleQueryFiltersChange((node.data?.filters || []).filter((_, itemIndex) => itemIndex !== index));
                      }}>Remove</button>
                    </div>
                  ))}
                  <button type="button" className="node-action-btn" disabled={!fieldOptions.length} onClick={() => {
                    handleQueryFiltersChange([...(node.data?.filters || []), { field: fieldOptions[0].value, op: 'eq' }]);
                  }}>Add filter</button>
                </div>
                <label className="workflow-node-options__field">
                  Order by
                  <Select
                    value={node.data?.orderBy || ''}
                    onChange={(orderBy) => onChange(node.id, { orderBy: orderBy || null })}
                    options={[{ value: '', label: 'Document key' }, ...fieldOptions]}
                    ariaLabel="Order by field"
                  />
                </label>
                <label className="workflow-node-options__field">
                  Order
                  <Select value={node.data?.order || 'asc'} onChange={(order) => onChange(node.id, { order })} options={[{ value: 'asc', label: 'Ascending' }, { value: 'desc', label: 'Descending' }]} ariaLabel="Order direction" />
                </label>
                <label className="workflow-node-options__field">
                  Limit
                  <input type="number" min={1} max={200} value={node.data?.limit || 50} onChange={(event) => {
                    const val = parseInt(event.target.value, 10);
                    onChange(node.id, { limit: Number.isFinite(val) ? Math.max(1, Math.min(200, val)) : 50 });
                  }} />
                </label>
              </>
            ) : null}
            {kind === 'document_write' ? (
              <label className="workflow-node-options__field">
                Document type
                <Select value={node.data?.documentType || ''} onChange={(documentTypeId) => handleDocumentOperationTypeChange(documentTypeId, 'write')} options={writableTypeOptions} ariaLabel="Document type" />
              </label>
            ) : null}
            {kind === 'document_create' || kind === 'document_delete' ? (
              <label className="workflow-node-options__field">
                Document type
                <Select value={node.data?.documentType || ''} onChange={(documentTypeId) => handleDocumentOperationTypeChange(documentTypeId, kind === 'document_create' ? 'create' : 'delete')} options={schemaCollectionTypeOptions} ariaLabel="Document type" />
              </label>
            ) : null}
            {kind === 'routine' ? (
              <label className="workflow-node-options__field">
                Routine
                <Select value={node.data?.routine || ''} onChange={handleRoutineChange} options={routineOptions} ariaLabel="Routine" />
              </label>
            ) : null}
            {kind === 'agent' || kind === 'extract' ? (
              <>
                <div className="workflow-node-options__field">
                  <span>Template source</span>
                  <div className="workflow-node-options__checkboxes">
                    <label className="workflow-node-options__checkbox">
                      <input
                        type="radio"
                        name={`template-source-${node.id}`}
                        checked={node.data?.instruction != null}
                        onChange={() => {
                          onChange(node.id, { instruction: instructionOptions[0]?.value || '', template: null });
                        }}
                      />
                      Instruction
                    </label>
                    <label className="workflow-node-options__checkbox">
                      <input
                        type="radio"
                        name={`template-source-${node.id}`}
                        checked={node.data?.instruction == null}
                        onChange={() => onChange(node.id, { instruction: null, template: '' })}
                      />
                      Inline template
                    </label>
                  </div>
                </div>
                {node.data?.instruction != null ? (
                  <>
                    <label className="workflow-node-options__field">
                      Instruction
                      <Select
                        value={node.data?.instruction || ''}
                        onChange={(path) => onChange(node.id, { instruction: path || null, template: null })}
                        options={[{ value: '', label: 'Select instruction…' }, ...instructionOptions]}
                        ariaLabel="Instruction"
                      />
                    </label>
                    {instructionRequest.error ? (
                      <InlineFailure
                        failure={instructionRequest.error}
                        operation="Load instruction content"
                        onRetry={loadInstruction}
                      />
                    ) : null}
                  </>
                ) : (
                  <label className="workflow-node-options__field">
                    Template
                    <textarea
                      value={node.data?.template || ''}
                      onChange={(event) => onChange(node.id, promptTemplatePatch(event.target.value))}
                      rows={6}
                    />
                  </label>
                )}
              </>
            ) : null}
            {kind === 'format' ? (
              <label className="workflow-node-options__field">
                Template
                <textarea
                  value={node.data?.template || ''}
                  onChange={(event) => onChange(node.id, formatTemplatePatch(event.target.value))}
                  rows={6}
                />
              </label>
            ) : null}
            {kind === 'agent' ? (
              <label className="workflow-node-options__field">
                Subagent
                <Select
                  value={node.data?.subagent || ''}
                  onChange={(val) => onChange(node.id, { subagent: val || null })}
                  options={[{ value: '', label: 'Progenitor' }, ...subagentOptions]}
                  ariaLabel="Subagent"
                />
              </label>
            ) : null}
            {kind === 'extract' ? (
              <>
                <label className="workflow-node-options__field">
                  Result schema
                  <Select
                    value={knownSchema ? (node.data?.schema || '') : '__custom__'}
                    onChange={(val) => {
                      if (val === '__custom__') {
                        onChange(node.id, { schema: '' });
                      } else {
                        onChange(node.id, { schema: val || null });
                      }
                    }}
                    options={[{ value: '', label: 'Infer' }, ...documentTypeOptions, { value: '__custom__', label: 'Other…' }]}
                    ariaLabel="Result schema"
                  />
                </label>
                {!knownSchema ? (
                  <label className="workflow-node-options__field">
                    Schema name
                    <input
                      value={node.data?.schema || ''}
                      onChange={(event) => onChange(node.id, { schema: event.target.value || null })}
                      placeholder="Type a schema name"
                    />
                  </label>
                ) : null}
              </>
            ) : null}
            {kind === 'data' ? (
              <>
                <JsonFieldEditor label="Value" value={node.data?.value} onCommit={(value) => onChange(node.id, { value })} />
                <JsonFieldEditor label="Output schema" value={node.data?.outputSchema} onCommit={(outputSchema) => onChange(node.id, { outputSchema })} />
              </>
            ) : null}
            {kind === 'condition' ? (
              <>
                <label className="workflow-node-options__field">Expression<textarea value={node.data?.expression || ''} onChange={(event) => onChange(node.id, { expression: event.target.value })} rows={3} /></label>
                <JsonFieldEditor label="Input schema" value={node.data?.inputSchema} onCommit={(inputSchema) => onChange(node.id, { inputSchema })} />
              </>
            ) : null}
            {kind === 'approval' ? (
              <>
                <label className="workflow-node-options__field">Message<textarea value={node.data?.message || ''} onChange={(event) => onChange(node.id, { message: event.target.value })} rows={3} /></label>
                <label className="workflow-node-options__field">
                  Who can decide
                  <Select
                    value={node.data?.approver || 'any'}
                    onChange={(approver) => onChange(node.id, { approver })}
                    options={[{ value: 'any', label: 'A person or Progenitor' }, { value: 'human', label: 'Only a person' }]}
                    ariaLabel="Who can decide"
                  />
                </label>
                <JsonFieldEditor label="Input schema" value={node.data?.inputSchema} onCommit={(inputSchema) => onChange(node.id, { inputSchema })} />
              </>
            ) : null}
          </section>
        </div>
    </Modal>
  );
}
