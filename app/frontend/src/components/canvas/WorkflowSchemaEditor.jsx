import { useEffect, useMemo, useState } from 'react';

const SCHEMA_TYPES = ['string', 'number', 'boolean', 'object', 'array'];

function SchemaPropertyRow({ name, schema, required, onChange, onDelete }) {
  return (
    <div className="schema-property-row">
      <input
        className="schema-property-row__name nodrag"
        value={name}
        onChange={(event) => onChange({ name: event.target.value })}
        placeholder="field name"
      />
      <select
        className="schema-property-row__type nodrag"
        value={schema?.type || 'string'}
        onChange={(event) => onChange({ type: event.target.value })}
      >
        {SCHEMA_TYPES.map((t) => (
          <option key={t} value={t}>{t}</option>
        ))}
      </select>
      <label className="schema-property-row__required nodrag">
        <input
          type="checkbox"
          checked={required}
          onChange={(event) => onChange({ required: event.target.checked })}
        />
        <span>Required</span>
      </label>
      <button
        type="button"
        className="schema-property-row__delete"
        onClick={onDelete}
        aria-label={`Remove ${name}`}
      >
        ×
      </button>
    </div>
  );
}

function buildEmptySchema() {
  return { type: 'object', properties: {}, required: [] };
}

function normalizeSchema(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return buildEmptySchema();
  }
  if (value.type !== 'object') {
    // Non-object schemas are supported but the visual editor only edits
    // object properties; the JSON tab handles everything else.
    return value;
  }
  return {
    type: 'object',
    properties: value.properties || {},
    required: Array.isArray(value.required) ? value.required : [],
  };
}

export default function WorkflowSchemaEditor({ label, value, onChange }) {
  const isObjectLike = value && typeof value === 'object' && !Array.isArray(value) && value.type === 'object';
  const [mode, setMode] = useState(isObjectLike ? 'visual' : 'json');
  const [jsonDraft, setJsonDraft] = useState('');
  const [jsonError, setJsonError] = useState('');

  const serialized = useMemo(() => (value ? JSON.stringify(value, null, 2) : ''), [value]);

  useEffect(() => {
    setJsonDraft(serialized);
    setJsonError('');
  }, [serialized]);

  useEffect(() => {
    // If the user switches to visual mode but the schema isn't a visualisable
    // object, create an empty object schema so the editor has something to
    // show, but don't fire onChange until they actually edit.
    if (mode === 'visual' && !isObjectLike) {
      // Keep the current value in JSON tab; switching back preserves it.
    }
  }, [mode, isObjectLike]);

  const handleAddProperty = () => {
    const base = normalizeSchema(value);
    const existingNames = Object.keys(base.properties || {});
    let nextName = 'field_1';
    let counter = 1;
    while (existingNames.includes(nextName)) {
      counter += 1;
      nextName = `field_${counter}`;
    }
    onChange({
      ...base,
      properties: { ...(base.properties || {}), [nextName]: { type: 'string' } },
    });
  };

  const handleUpdateProperty = (oldName, updates) => {
    const base = normalizeSchema(value);
    const properties = { ...(base.properties || {}) };
    const required = [...(base.required || [])];

    if (updates.name && updates.name !== oldName) {
      const oldSchema = properties[oldName];
      delete properties[oldName];
      properties[updates.name] = oldSchema;
      const reqIndex = required.indexOf(oldName);
      if (reqIndex >= 0) {
        required[reqIndex] = updates.name;
      }
    }

    if (updates.type !== undefined) {
      const targetName = updates.name || oldName;
      properties[targetName] = { ...properties[targetName], type: updates.type };
    }

    if (updates.required !== undefined) {
      const targetName = updates.name || oldName;
      const reqSet = new Set(required);
      if (updates.required) {
        reqSet.add(targetName);
      } else {
        reqSet.delete(targetName);
      }
      onChange({ ...base, properties, required: Array.from(reqSet) });
      return;
    }

    onChange({ ...base, properties, required });
  };

  const handleDeleteProperty = (name) => {
    const base = normalizeSchema(value);
    const properties = { ...(base.properties || {}) };
    delete properties[name];
    const required = (base.required || []).filter((r) => r !== name);
    onChange({ ...base, properties, required });
  };

  const handleJsonBlur = () => {
    if (!jsonDraft.trim()) {
      onChange(null);
      setJsonError('');
      return;
    }
    try {
      const parsed = JSON.parse(jsonDraft);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
        throw new Error('must be a JSON object');
      }
      onChange(parsed);
      setJsonError('');
    } catch (parseError) {
      setJsonError(`Invalid schema: ${parseError.message}`);
    }
  };

  const canUseVisual = isObjectLike;

  return (
    <label className="workflow-graph-node__field">
      <span className="workflow-schema-editor__label">
        {label}
        <span className="workflow-schema-editor__tabs">
          <button
            type="button"
            className={mode === 'visual' ? 'active' : ''}
            onClick={() => {
              if (!canUseVisual) {
                onChange(buildEmptySchema());
              }
              setMode('visual');
            }}
            disabled={!canUseVisual && mode === 'json'}
            title={canUseVisual ? 'Visual editor' : 'Visual editor (only for object schemas)'}
          >
            Visual
          </button>
          <button
            type="button"
            className={mode === 'json' ? 'active' : ''}
            onClick={() => setMode('json')}
          >
            JSON
          </button>
        </span>
      </span>

      {mode === 'visual' && canUseVisual ? (
        <div className="workflow-schema-editor__visual nodrag">
          {Object.entries(value.properties || {}).map(([name, schema]) => (
            <SchemaPropertyRow
              key={name}
              name={name}
              schema={schema}
              required={(value.required || []).includes(name)}
              onChange={(updates) => handleUpdateProperty(name, updates)}
              onDelete={() => handleDeleteProperty(name)}
            />
          ))}
          {Object.keys(value.properties || {}).length === 0 ? (
            <p className="workflow-schema-editor__empty">No fields yet.</p>
          ) : null}
          <button
            type="button"
            className="node-action-btn"
            onClick={handleAddProperty}
          >
            + Add field
          </button>
        </div>
      ) : (
        <textarea
          aria-label={label}
          className="nodrag"
          value={jsonDraft}
          onChange={(event) => setJsonDraft(event.target.value)}
          onBlur={handleJsonBlur}
          placeholder='{"type":"object","properties":{}}'
          rows={5}
        />
      )}
      {jsonError ? <small className="workflow-graph-node__error">{jsonError}</small> : null}
    </label>
  );
}
