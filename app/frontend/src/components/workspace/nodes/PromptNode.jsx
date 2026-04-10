import { memo, useCallback, useMemo, useState } from 'react';
import { Handle, NodeResizer, Position } from '@xyflow/react';
import { useWorkspaceActions, useWorkspaceMeta } from '../../../context/WorkspaceContext';
import WidgetTabs from './WidgetTabs';
import { resolveWidgetDefaultTab } from './widgetTabUtils';

function buildTargetOptions(data, agentOptions) {
  return [...new Set(
    [
      data.targetAgent,
      data.primaryAgent,
      ...(data.associatedAgents || []),
      ...agentOptions,
      'progenitor',
    ]
      .map((value) => (typeof value === 'string' ? value.trim() : ''))
      .filter(Boolean)
  )];
}

function formatAgentLabel(value) {
  return String(value || '')
    .replace(/[_-]+/g, ' ')
    .trim();
}

function PromptNode({ id, data, selected }) {
  const {
    connectPromptToAgent,
    updateNodeData,
    toggleExpanded,
    runPromptNode,
    generateFromPromptNode,
  } = useWorkspaceActions();
  const {
    agentOptions,
    getWidgetTabs,
    getWidgetDefaultTab,
  } = useWorkspaceMeta();
  const { expanded } = data;
  const associatedAgents = data.associatedAgents || [];
  const targetAgentOptions = buildTargetOptions(data, agentOptions);
  const activeTargetAgent = data.targetAgent || targetAgentOptions[0] || 'progenitor';
  const tabs = getWidgetTabs('prompt');
  const defaultTab = useMemo(
    () => resolveWidgetDefaultTab(tabs, getWidgetDefaultTab('prompt'), 'editor'),
    [getWidgetDefaultTab, tabs]
  );
  const [activeTab, setActiveTab] = useState(defaultTab);
  const resolvedActiveTab = useMemo(
    () => resolveWidgetDefaultTab(tabs, activeTab, defaultTab),
    [activeTab, defaultTab, tabs]
  );

  const onPromptChange = useCallback((e) => {
    updateNodeData(id, { prompt: e.target.value });
  }, [id, updateNodeData]);

  const onRoleChange = useCallback((e) => {
    updateNodeData(id, { role: e.target.value });
  }, [id, updateNodeData]);

  const onTargetAgentChange = useCallback((e) => {
    connectPromptToAgent(id, e.target.value);
  }, [connectPromptToAgent, id]);

  const onToggle = useCallback(() => {
    toggleExpanded(id);
  }, [id, toggleExpanded]);

  const promptPreview = data.prompt
    ? data.prompt.substring(0, 120) + (data.prompt.length > 120 ? '...' : '')
    : 'Empty prompt';
  const responsePreview = data.summaryState
    ? data.summaryState.substring(0, 160) + (data.summaryState.length > 160 ? '...' : '')
    : '';

  return (
    <>
      <NodeResizer
        minWidth={240}
        minHeight={120}
        isVisible={selected}
        lineClassName="react-flow__resize-control line"
        handleClassName="react-flow__resize-control handle"
      />
      <Handle type="target" position={Position.Left} className="workspace-handle" />
      <Handle type="source" position={Position.Right} className="workspace-handle" />
      <div className={`workspace-node ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <div className="node-type-icon prompt">P</div>
          <div className="node-title">{data.title}</div>
          {activeTargetAgent && (
            <span className="node-meta-chip prompt-agent">{formatAgentLabel(activeTargetAgent)}</span>
          )}
          {data.status !== 'idle' && (
            <span className={`node-status-badge ${data.status}`}>{data.status}</span>
          )}
          <button className="node-expand-btn" onClick={onToggle} title={expanded ? 'Collapse' : 'Expand'}>
            {expanded ? '\u25B2' : '\u25BC'}
          </button>
        </div>

        <div className="node-body">
          {expanded ? (
            <div className="node-body-expanded">
              <WidgetTabs tabs={tabs} activeTab={resolvedActiveTab} onChange={setActiveTab} />
              <div className="prompt-meta">
                <input
                  className="nodrag"
                  placeholder="Role (e.g. system, user)"
                  value={data.role || ''}
                  onChange={onRoleChange}
                />
                <select className="nodrag" value={activeTargetAgent} onChange={onTargetAgentChange}>
                  {targetAgentOptions.map((agent) => (
                    <option key={agent} value={agent}>
                      {agent === 'progenitor' ? 'Run as progenitor' : `Call ${agent}`}
                    </option>
                  ))}
                </select>
              </div>
              {(data.sourcePath || associatedAgents.length > 0) && (
                <div className="prompt-source-meta">
                  {data.sourcePath && <div className="prompt-source-path">{data.sourcePath}</div>}
                  {associatedAgents.length > 0 && (
                    <div className="prompt-tags">
                      {associatedAgents.map((agent) => (
                        <span key={agent} className="prompt-tag">{agent}</span>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {resolvedActiveTab === 'output' ? (
                <div className="node-tab-panel">
                  <div className="prompt-response">
                    <div className="prompt-response-label">Latest response</div>
                    <div className="prompt-response-body">{data.summaryState || 'No output yet.'}</div>
                  </div>
                  {data.linkedOutputIds?.length > 0 && (
                    <div className="node-output-meta">
                      {data.linkedOutputIds.map((outputId) => (
                        <span key={outputId} className="prompt-tag">{outputId}</span>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <textarea
                  className="prompt-editor nodrag nowheel"
                  value={data.prompt || ''}
                  onChange={onPromptChange}
                  placeholder="Enter your prompt..."
                />
              )}
              {data.tags?.length > 0 && (
                <div className="prompt-tags">
                  {data.tags.map((tag, i) => (
                    <span key={i} className="prompt-tag">{tag}</span>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="node-body-compact">
              {(data.sourcePath || associatedAgents.length > 0) && (
                <div className="prompt-source-meta compact">
                  {data.sourcePath && <div className="prompt-source-path">{data.sourcePath}</div>}
                  {associatedAgents.length > 0 && (
                    <div className="prompt-tags">
                      {associatedAgents.map((agent) => (
                        <span key={agent} className="prompt-tag">{agent}</span>
                      ))}
                    </div>
                  )}
                </div>
              )}
              <div className="prompt-preview">{promptPreview}</div>
              {responsePreview && <div className="prompt-response-preview">{responsePreview}</div>}
              {data.tags?.length > 0 && (
                <div className="prompt-tags">
                  {data.tags.map((tag, i) => (
                    <span key={i} className="prompt-tag">{tag}</span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {expanded && (
          <div className="node-actions">
            <button className="node-action-btn primary" onClick={() => runPromptNode(id)}>Run</button>
            <button className="node-action-btn" onClick={() => generateFromPromptNode(id)}>Generate Code</button>
          </div>
        )}
      </div>
    </>
  );
}

export default memo(PromptNode);
