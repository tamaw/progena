import { useEffect, useMemo } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { Handle, NodeResizer, Position } from '@xyflow/react';
import { useConfig } from '../../context/ConfigContext';
import Select from '../common/Select';
import {
  fetchMcpServerDetail,
  selectMcpServers,
  selectMcpServerDetail,
} from '../../store/uiDataSlice';
import WorkflowNodeTitle from './WorkflowNodeTitle';

const BINDING_SOURCES = [
  { value: 'literal', label: 'Literal' },
  { value: 'trigger', label: 'Trigger' },
  { value: 'previous_output', label: 'Previous output' },
  { value: 'reference', label: 'Reference' },
];

function McpBindingField({ name, schema, binding, onChange, onDelete }) {
  const source = binding?.source || 'literal';
  return (
    <div className="mcp-binding-field">
      <div className="mcp-binding-field__header">
        <span className="mcp-binding-field__name">{name}</span>
        <span className="mcp-binding-field__type">{schema?.type || 'any'}</span>
      </div>
      <div className="mcp-binding-field__controls">
        <select
          className="nodrag"
          value={source}
          onChange={(event) => onChange({ source: event.target.value })}
        >
          {BINDING_SOURCES.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
        {source === 'literal' && (
          <input
            className="nodrag"
            value={binding?.value ?? ''}
            onChange={(event) => onChange({ source, value: event.target.value })}
            placeholder="Value"
          />
        )}
        {(source === 'trigger' || source === 'previous_output') && (
          <input
            className="nodrag"
            value={binding?.path ?? ''}
            onChange={(event) => onChange({ source, path: event.target.value })}
            placeholder="JSON path, e.g. payload.id"
          />
        )}
        {source === 'reference' && (
          <input
            className="nodrag"
            value={binding?.reference ?? ''}
            onChange={(event) => onChange({ source, reference: event.target.value })}
            placeholder="Reference key"
          />
        )}
        <button
          type="button"
          className="mcp-binding-field__clear"
          onClick={onDelete}
          aria-label={`Clear ${name} binding`}
          title="Clear binding"
        >
          ×
        </button>
      </div>
    </div>
  );
}

export default function McpNode({ id, data, selected }) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const mcpUrl = getApiUrl('mcp');
  const servers = useSelector(selectMcpServers);
  const serverDetail = useSelector((state) => selectMcpServerDetail(state, data.serverId));

  useEffect(() => {
    if (!data.serverId || serverDetail) return;
    dispatch(fetchMcpServerDetail({ mcpUrl, id: data.serverId })).catch(() => {});
  }, [dispatch, mcpUrl, data.serverId, serverDetail]);

  const serverOptions = useMemo(
    () => servers.map((s) => ({ value: s.id, label: s.name || s.id })),
    [servers],
  );

  const tools = useMemo(
    () => (serverDetail?.data?.tools || []).filter((t) => t.enabled !== false),
    [serverDetail],
  );

  const toolOptions = useMemo(
    () => tools.map((t) => ({
      value: t.name,
      label: t.original_name || t.name || t.name,
      description: t.description || '',
    })),
    [tools],
  );

  const selectedTool = useMemo(
    () => tools.find((t) => t.name === data.toolName || t.original_name === data.toolName),
    [tools, data.toolName],
  );

  const inputSchema = selectedTool?.inputSchema || selectedTool?.input_schema || null;

  const isRunning = data.currentNodeId === id;
  const isCompleted = Array.isArray(data.completedNodeIds) && data.completedNodeIds.includes(id);
  const statusClass = isRunning ? 'running' : (isCompleted ? 'completed' : 'idle');
  const statusLabel = isRunning ? 'Running' : (isCompleted ? 'Done' : 'MCP');
  const expanded = data.expanded;

  const updateBinding = (field, patch) => {
    const nextBinding = { ...(data.inputs?.[field] || {}), ...patch };
    // Clean undefined/nullish keys so serialised output stays tidy.
    const cleaned = Object.fromEntries(
      Object.entries(nextBinding).filter(([, v]) => v !== undefined && v !== null),
    );
    data.onChange(id, {
      inputs: { ...(data.inputs || {}), [field]: cleaned },
    });
  };

  const deleteBinding = (field) => {
    const next = { ...(data.inputs || {}) };
    delete next[field];
    data.onChange(id, { inputs: next });
  };

  return (
    <>
      <NodeResizer
        minWidth={280}
        minHeight={expanded ? 240 : 110}
        isVisible={selected}
        onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })}
      />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--mcp ${selected ? 'selected' : ''}`}>
        <Handle type="target" position={Position.Left} className="canvas-handle" />
        <Handle type="source" position={Position.Right} className="canvas-handle" />
        <div className="node-title-bar drag-handle">
          <span className="node-type-icon mcp">M</span>
          <WorkflowNodeTitle
            expanded={expanded}
            title={data.title}
            placeholder="MCP title"
            onChange={(event) => data.onChange(id, { title: event.target.value })}
          />
          <span className={`node-status-badge workflow-graph-node__kind ${statusClass}`}>{statusLabel}</span>
          <button
            type="button"
            className="node-expand-btn nodrag"
            onClick={() => data.onToggleExpanded(id)}
            title={expanded ? 'Show data' : 'Show settings'}
            aria-label={expanded ? 'Show data' : 'Show settings'}
          >
            {expanded ? '▲' : '▼'}
          </button>
          {data.canDelete ? (
            <button
              type="button"
              className="node-remove-btn"
              aria-label="Delete MCP node"
              onClick={() => data.onDelete(id)}
            >
              ×
            </button>
          ) : null}
        </div>
        <div className="node-body">
          {expanded ? (
            <div className="node-body-expanded workflow-graph-node__body">
              <label className="workflow-graph-node__field">
                <span>Server</span>
                <Select
                  className="nodrag"
                  value={data.serverId || ''}
                  onChange={(nextValue) => data.onChange(id, { serverId: nextValue, toolName: '' })}
                  options={serverOptions}
                  placeholder="Select MCP server…"
                  searchable
                  portal
                  ariaLabel="MCP server"
                />
              </label>
              <label className="workflow-graph-node__field">
                <span>Tool</span>
                <Select
                  className="nodrag"
                  value={data.toolName || ''}
                  onChange={(nextValue) => data.onChange(id, { toolName: nextValue })}
                  options={toolOptions}
                  placeholder={data.serverId ? 'Select tool…' : 'Select server first'}
                  disabled={!data.serverId}
                  searchable
                  portal
                  ariaLabel="MCP tool"
                />
              </label>

              {selectedTool ? (
                <div className="mcp-tool-detail">
                  {selectedTool.description ? (
                    <p className="mcp-tool-description">{selectedTool.description}</p>
                  ) : null}
                  {inputSchema && typeof inputSchema === 'object' && inputSchema.properties ? (
                    <div className="mcp-bindings">
                      <strong className="mcp-bindings__title">Bindings</strong>
                      {Object.entries(inputSchema.properties).map(([propName, propSchema]) => (
                        <McpBindingField
                          key={propName}
                          name={propName}
                          schema={propSchema}
                          binding={data.inputs?.[propName]}
                          onChange={(patch) => updateBinding(propName, patch)}
                          onDelete={() => deleteBinding(propName)}
                        />
                      ))}
                    </div>
                  ) : inputSchema ? (
                    <p className="mcp-schema-fallback">
                      Tool schema is not an object with properties. Use the workflow JSON editor to set bindings manually.
                    </p>
                  ) : (
                    <p className="mcp-schema-missing">
                      Tool schema not available from discovery. You can still configure bindings via the JSON editor.
                    </p>
                  )}
                </div>
              ) : null}

            </div>
          ) : (
            <div className="node-body-compact">
              {data.serverId ? (
                <span className="node-meta-chip mcp-server">
                  {servers.find((s) => s.id === data.serverId)?.name || data.serverId}
                </span>
              ) : null}
              {data.toolName ? (
                <span className="node-meta-chip mcp-tool">{data.toolName}</span>
              ) : null}
              <div className="prompt-preview">
                {data.toolName
                  ? `Call ${data.toolName}`
                  : (data.serverId ? 'Select a tool' : 'Select an MCP server')}
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
