import { useMemo } from 'react';
import { Handle, NodeResizer, Position, useEdges } from '@xyflow/react';
import { ConfigureActionIcon } from '../../../components/layout/DetailHeader';
import WorkflowNodeTitle from './WorkflowNodeTitle';
import { schemaType, nodeOutputPorts, nodeInputPorts, portFromHandle, isUnpackableSchema } from '../models/schema';
import { visibleNodePortProperties } from '../models/nodePalette';

function WorkflowNodeHandles({ kind, inputSchema, outputSchema, nodeHeight, connectedInputPorts = [], connectedOutputPorts = [], showAllFields = null }) {
  const inputProperties = visibleNodePortProperties(nodeInputPorts(kind, inputSchema), nodeHeight, connectedInputPorts);
  const outputProperties = visibleNodePortProperties(nodeOutputPorts(kind, outputSchema), nodeHeight, connectedOutputPorts, showAllFields);
  const top = (index) => `${61 + index * 28}px`;
  return (
    <>
      {inputProperties.map(({ name }, index) => (
        <Handle key={`input-${name}`} type="target" position={Position.Left} id={`in:${name}`} className="canvas-handle workflow-node-external-handle" style={{ top: top(index) }} />
      ))}
      {outputProperties.map(({ name }, index) => (
        <Handle key={`output-${name}`} type="source" position={Position.Right} id={`out:${name}`} className="canvas-handle workflow-node-external-handle" style={{ top: top(index) }} />
      ))}
    </>
  );
}

// Renders port labels in exactly the order and subset WorkflowNodeHandles
// places handles, so every handle (and its edge) lines up with its label.
function WorkflowNodePorts({ kind, inputSchema, outputSchema, nodeHeight, connectedInputPorts = [], connectedOutputPorts = [], showAllFields = null, onToggleShowAllFields, onUnpack }) {
  const inputProperties = visibleNodePortProperties(nodeInputPorts(kind, inputSchema), nodeHeight, connectedInputPorts);
  const outputProperties = visibleNodePortProperties(nodeOutputPorts(kind, outputSchema), nodeHeight, connectedOutputPorts, showAllFields);
  const unconnectedCount = showAllFields !== null
    ? nodeOutputPorts(kind, outputSchema).filter((p) => !connectedOutputPorts.includes(p.name)).length
    : 0;
  return (
    <div className="workflow-node-ports">
      <div className="workflow-node-ports__column workflow-node-ports__column--inputs">
        {inputProperties.length ? inputProperties.map(({ name, definition }) => (
          <div className="workflow-node-port" key={`input-${name}`}>
            <span>{name}</span>
            <small>{schemaType(definition)}</small>
          </div>
        )) : <span className="workflow-node-ports__empty">No inputs</span>}
      </div>
      <div className="workflow-node-ports__column workflow-node-ports__column--outputs">
        {outputProperties.map(({ name, definition }) => (
          <div className="workflow-node-port" key={`output-${name}`}>
            {onUnpack && isUnpackableSchema(definition) ? (
              <button type="button" className="workflow-node-port__unpack" onClick={() => onUnpack(name)} aria-label={`Unpack ${name}`}>▸</button>
            ) : null}
            <small>{schemaType(definition)}</small>
            <span>{name}</span>
          </div>
        ))}
        {unconnectedCount > 0 ? (
          <button type="button" className="workflow-node-port workflow-node-port--toggle nodrag" onClick={onToggleShowAllFields} aria-expanded={Boolean(showAllFields)}>
            {showAllFields ? 'Hide unused fields' : `+ ${unconnectedCount} field${unconnectedCount === 1 ? '' : 's'}`}
          </button>
        ) : null}
        {!outputProperties.length && !unconnectedCount ? <span className="workflow-node-ports__empty">No outputs</span> : null}
      </div>
    </div>
  );
}

export function PromptNode({ id, data, selected, height }) {
  const expanded = data.expanded;
  const edges = useEdges();
  const connectedInputPorts = useMemo(() => edges.filter((e) => e.target === id).map((e) => portFromHandle(e.targetHandle)), [edges, id]);
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer
        minWidth={240}
        minHeight={expanded ? 200 : 100}
        isVisible={selected}
        onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })}
      />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--${data.kind} ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className={`node-type-icon ${data.kind}`}>{data.kind[0].toUpperCase()}</span>
          <WorkflowNodeTitle
            expanded={expanded}
            title={data.title}
            placeholder="Node title"
            onChange={(event) => data.onChange(id, { title: event.target.value })}
          />
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
              aria-label="Delete node"
              onClick={() => data.onDelete(id)}
            >
              ×
            </button>
          ) : null}
        </div>
        <div className="node-body">
          <WorkflowNodePorts kind={data.kind} inputSchema={data.inputSchema} outputSchema={data.outputSchema} nodeHeight={height} connectedInputPorts={connectedInputPorts} connectedOutputPorts={connectedOutputPorts} onUnpack={data.onUnpack ? (port) => data.onUnpack(id, port) : undefined} />
        </div>
      </div>
      <WorkflowNodeHandles kind={data.kind} inputSchema={data.inputSchema} outputSchema={data.outputSchema} nodeHeight={height} connectedInputPorts={connectedInputPorts} connectedOutputPorts={connectedOutputPorts} />
    </>
  );
}

export function DocumentEventNode({ id, data, selected, height }) {
  const edges = useEdges();
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer minWidth={280} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--trigger ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className="node-type-icon event">E</span>
          <WorkflowNodeTitle expanded={false} title={data.title} placeholder="Event title" onChange={(event) => data.onChange(id, { title: event.target.value })} />
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
          {data.canDelete ? <button type="button" className="node-remove-btn" onClick={() => data.onDelete(id)} aria-label="Delete event">×</button> : null}
        </div>
        <div className="node-body">
          <WorkflowNodePorts kind="event" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} onUnpack={data.onUnpack ? (port) => data.onUnpack(id, port) : undefined} />
        </div>
      </div>
      <WorkflowNodeHandles kind="event" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} />
    </>
  );
}

export function CronNode({ id, data, selected, height }) {
  const edges = useEdges();
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer minWidth={280} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--cron ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className="node-type-icon cron">C</span>
          <WorkflowNodeTitle expanded={false} title={data.title} placeholder="Cron title" onChange={(event) => data.onChange(id, { title: event.target.value })} />
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
          {data.canDelete ? <button type="button" className="node-remove-btn" onClick={() => data.onDelete(id)} aria-label="Delete cron">×</button> : null}
        </div>
        <div className="node-body">
          <div className="workflow-graph-node__field">
            <span>{data.expression || '*/5 * * * *'}</span>
          </div>
          <WorkflowNodePorts kind="cron" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} onUnpack={data.onUnpack ? (port) => data.onUnpack(id, port) : undefined} />
        </div>
      </div>
      <WorkflowNodeHandles kind="cron" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} />
    </>
  );
}

export function ManualNode({ id, data, selected, height }) {
  const edges = useEdges();
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer minWidth={280} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--trigger ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className="node-type-icon manual">M</span>
          <WorkflowNodeTitle expanded={false} title={data.title} placeholder="Manual trigger title" onChange={(event) => data.onChange(id, { title: event.target.value })} />
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
          {data.canDelete ? <button type="button" className="node-remove-btn" onClick={() => data.onDelete(id)} aria-label="Delete manual trigger">×</button> : null}
        </div>
        <div className="node-body">
          <WorkflowNodePorts kind="manual" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} onUnpack={data.onUnpack ? (port) => data.onUnpack(id, port) : undefined} />
        </div>
      </div>
      <WorkflowNodeHandles kind="manual" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} />
    </>
  );
}

export function WorkflowDataNode({ id, data, selected, height }) {
  const edges = useEdges();
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer minWidth={240} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--data ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className="node-type-icon data">D</span>
          <WorkflowNodeTitle expanded={false} title={data.title} placeholder="Data title" onChange={(event) => data.onChange(id, { title: event.target.value })} />
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
          <button type="button" className="node-remove-btn" onClick={() => data.onDelete(id)} aria-label="Delete data node">×</button>
        </div>
        <div className="node-body">
          <WorkflowNodePorts kind="data" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} onUnpack={data.onUnpack ? (port) => data.onUnpack(id, port) : undefined} />
        </div>
      </div>
      <WorkflowNodeHandles kind="data" inputSchema={null} outputSchema={data.outputSchema} nodeHeight={height} connectedOutputPorts={connectedOutputPorts} />
    </>
  );
}

export function WorkflowControlNode({ id, data, selected, height }) {
  const label = data.kind === 'condition' ? 'Condition' : 'Approval';
  const edges = useEdges();
  const connectedInputPorts = useMemo(() => edges.filter((e) => e.target === id).map((e) => portFromHandle(e.targetHandle)), [edges, id]);
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer minWidth={260} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--${data.kind} ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className={`node-type-icon ${data.kind}`}>{data.kind[0].toUpperCase()}</span>
          <WorkflowNodeTitle expanded={false} title={data.title} placeholder={`${label} title`} onChange={(event) => data.onChange(id, { title: event.target.value })} />
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
          <button type="button" className="node-remove-btn" onClick={() => data.onDelete(id)} aria-label={`Delete ${label}`}>×</button>
        </div>
        <div className="node-body">
          <WorkflowNodePorts kind={data.kind} inputSchema={data.inputSchema} outputSchema={data.outputSchema} nodeHeight={height} connectedInputPorts={connectedInputPorts} connectedOutputPorts={connectedOutputPorts} />
        </div>
      </div>
      <WorkflowNodeHandles kind={data.kind} inputSchema={data.inputSchema} outputSchema={data.outputSchema} nodeHeight={height} connectedInputPorts={connectedInputPorts} connectedOutputPorts={connectedOutputPorts} />
    </>
  );
}

export function UnpackNode({ id, data, selected, height }) {
  const edges = useEdges();
  const connectedInputPorts = useMemo(() => edges.filter((e) => e.target === id).map((e) => portFromHandle(e.targetHandle)), [edges, id]);
  const connectedOutputPorts = useMemo(() => edges.filter((e) => e.source === id).map((e) => portFromHandle(e.sourceHandle)), [edges, id]);

  return (
    <>
      <NodeResizer minWidth={260} minHeight={110} isVisible={selected} onResizeEnd={(_event, params) => data.onResize(id, { width: params.width, height: params.height })} />
      <div className={`canvas-node workflow-graph-node workflow-graph-node--data ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <span className="node-type-icon unpack">U</span>
          <WorkflowNodeTitle expanded={false} title={data.title} placeholder="Unpack" onChange={(event) => data.onChange(id, { title: event.target.value })} />
          <button type="button" className="node-options-btn nodrag" onClick={() => data.onOpenOptions(id)} aria-label="Configure node" data-tooltip="Configure node"><ConfigureActionIcon /></button>
          <button type="button" className="node-remove-btn" onClick={() => data.onDelete(id)} aria-label="Delete unpack node">×</button>
        </div>
        <div className="node-body">
          <WorkflowNodePorts
            kind={data.kind}
            inputSchema={data.inputSchema}
            outputSchema={data.outputSchema}
            nodeHeight={height}
            connectedInputPorts={connectedInputPorts}
            connectedOutputPorts={connectedOutputPorts}
            showAllFields={Boolean(data.showAllFields)}
            onToggleShowAllFields={() => data.onChange(id, { showAllFields: !data.showAllFields })}
          />
        </div>
      </div>
      <WorkflowNodeHandles kind={data.kind} inputSchema={data.inputSchema} outputSchema={data.outputSchema} nodeHeight={height} connectedInputPorts={connectedInputPorts} connectedOutputPorts={connectedOutputPorts} showAllFields={Boolean(data.showAllFields)} />
    </>
  );
}
