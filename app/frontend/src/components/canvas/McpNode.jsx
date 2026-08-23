import { useEffect, useMemo } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { Handle, NodeResizer, Position } from '@xyflow/react';
import { useConfig } from '../../context/ConfigContext';
import {
  fetchMcpServerDetail,
  selectMcpServerDetail,
} from '../../store/uiDataSlice';
import WorkflowNodeTitle from './WorkflowNodeTitle';
import { ConfigureActionIcon } from '../layout/DetailHeader';


export default function McpNode({ id, data, selected }) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const mcpUrl = getApiUrl('mcp');
  const serverDetail = useSelector((state) => selectMcpServerDetail(state, data.serverId));

  useEffect(() => {
    if (!data.serverId || serverDetail) return;
    dispatch(fetchMcpServerDetail({ mcpUrl, id: data.serverId })).catch(() => {});
  }, [dispatch, mcpUrl, data.serverId, serverDetail]);


  const tools = useMemo(
    () => (serverDetail?.data?.tools || []).filter((t) => t.enabled !== false),
    [serverDetail],
  );


  const selectedTool = useMemo(
    () => tools.find((t) => t.name === data.toolName || t.original_name === data.toolName),
    [tools, data.toolName],
  );

  const inputSchema = selectedTool?.inputSchema || selectedTool?.input_schema || null;
  const inputProperties = Object.entries(inputSchema?.properties || {}).map(([name, schema]) => [name, schema || {}]);
  const outputProperties = Object.entries(data.outputSchema?.properties || {}).map(([name, schema]) => [name, schema || {}]);

  const isRunning = data.currentNodeId === id;
  const isCompleted = Array.isArray(data.completedNodeIds) && data.completedNodeIds.includes(id);
  const statusClass = isRunning ? 'running' : (isCompleted ? 'completed' : 'idle');
  const statusLabel = isRunning ? 'Running' : (isCompleted ? 'Done' : 'MCP');
  const expanded = data.expanded;


  return (
    <>
      <NodeResizer
        minWidth={280}
        minHeight={expanded ? 240 : 110}
        isVisible={selected}
        onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })}
      />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--mcp ${selected ? 'selected' : ''}`}>
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
            className="node-options-btn nodrag"
            onClick={() => data.onOpenOptions(id)}
            aria-label="Configure node"
            data-tooltip="Configure node"
          >
            <ConfigureActionIcon />
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
          <div className="workflow-node-ports">
            <div className="workflow-node-ports__column workflow-node-ports__column--inputs">
              {inputProperties.length ? inputProperties.map(([name, schema]) => (
                <div className="workflow-node-port" key={name}>
                  <span>{name}</span>
                  <small>{schema.type || 'any'}</small>
                </div>
              )) : <span className="workflow-node-ports__empty">No inputs</span>}
            </div>
            <div className="workflow-node-ports__column workflow-node-ports__column--outputs">
              {(outputProperties.length ? outputProperties : [['output', {}]]).map(([name, schema]) => (
                <div className="workflow-node-port" key={name}>
                  <small>{schema.type || 'any'}</small>
                  <span>{name}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      {inputProperties.map(([name], index) => (
        <Handle key={`input-${name}`} type="target" position={Position.Left} id={`in:${name}`} className="canvas-handle workflow-node-external-handle" style={{ top: `${54 + index * 23}px` }} />
      ))}
      {(outputProperties.length ? outputProperties : [['output', {}]]).map(([name], index) => (
        <Handle key={`output-${name}`} type="source" position={Position.Right} id={`out:/${name}`} className="canvas-handle workflow-node-external-handle" style={{ top: `${54 + index * 23}px` }} />
      ))}
    </>
  );
}
