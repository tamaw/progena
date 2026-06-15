import { memo, useCallback, useMemo, useState } from 'react';
import { Handle, NodeResizer, Position } from '@xyflow/react';
import { useWorkspaceActions, useWorkspaceMeta } from '../../../context/WorkspaceContext';
import WidgetTabs from './WidgetTabs';
import NodeHelpButton from './NodeHelpButton';
import { resolveWidgetDefaultTab } from './widgetTabUtils';
import { DetailHeaderActions, DetailHeaderAction, PlayActionIcon, DeleteActionIcon } from '../../layout/DetailHeader';

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

const PROMPT_ROLES = ['system', 'user', 'assistant'];

const PROMPT_STARTERS = [
  { label: 'Summarize', text: 'Summarize the following:\n\n{{ input }}' },
  { label: 'Draft', text: 'Draft a {{ type }} about {{ topic }}.' },
  { label: 'Extract', text: 'Extract {{ fields }} from:\n\n{{ input }}' },
];

function PromptNode({ id, data, selected }) {
  const {
    connectPromptToAgent,
    updateNodeData,
    toggleExpanded,
    runPromptNode,
    removeNodes,
  } = useWorkspaceActions();
  const {
    agentOptions,
    getWidgetTabs,
    getWidgetDefaultTab,
    getWidgetHelpText,
  } = useWorkspaceMeta();
  const { expanded } = data;
  const associatedAgents = data.associatedAgents || [];
  const targetAgentOptions = buildTargetOptions(data, agentOptions);
  const activeTargetAgent = data.targetAgent || targetAgentOptions[0] || 'progenitor';
  const tabs = getWidgetTabs('prompt');
  const helpText = getWidgetHelpText('prompt');
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

  const onTitleChange = useCallback((e) => {
    updateNodeData(id, { title: e.target.value });
  }, [id, updateNodeData]);

  const applyStarter = useCallback((text) => {
    updateNodeData(id, { prompt: text });
  }, [id, updateNodeData]);

  const insertVariable = useCallback(() => {
    const base = data.prompt || '';
    updateNodeData(id, { prompt: base ? `${base} {{ variable }}` : '{{ variable }}' });
  }, [data.prompt, id, updateNodeData]);

  const onEditorKeyDown = useCallback((e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      runPromptNode(id);
    }
  }, [id, runPromptNode]);

  const enabled = data.enabled !== false;

  const onToggleEnabled = useCallback(() => {
    updateNodeData(id, { enabled: data.enabled === false });
  }, [data.enabled, id, updateNodeData]);

  const onToggleEnabledKeyDown = useCallback((e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onToggleEnabled();
    }
  }, [onToggleEnabled]);

  const onDelete = useCallback(() => {
    removeNodes([id]);
  }, [id, removeNodes]);

  const promptText = data.prompt || '';
  const isPromptEmpty = !promptText.trim();
  const promptPreview = data.prompt || 'Empty prompt';
  const responsePreview = data.summaryState || '';

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
      <div className={`workspace-node ${selected ? 'selected' : ''} ${enabled ? '' : 'is-disabled'}`}>
        <div className="node-title-bar drag-handle">
          <div className="node-type-icon prompt">P</div>
          <input
            className="node-title-input nodrag"
            value={data.title || ''}
            onChange={onTitleChange}
            onPointerDown={(e) => e.stopPropagation()}
            placeholder="Untitled prompt"
            aria-label="Prompt title"
          />
          {activeTargetAgent && !expanded && (
            <span className="node-meta-chip prompt-agent">{formatAgentLabel(activeTargetAgent)}</span>
          )}
          {!enabled && (
            <span className="node-status-badge disabled">disabled</span>
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
                <select className="nodrag app-select" value={data.role || ''} onChange={onRoleChange}>
                  <option value="">No role</option>
                  {PROMPT_ROLES.map((role) => (
                    <option key={role} value={role}>
                      {role.charAt(0).toUpperCase() + role.slice(1)}
                    </option>
                  ))}
                </select>
                <select className="nodrag app-select" value={activeTargetAgent} onChange={onTargetAgentChange}>
                  {targetAgentOptions.map((agent) => (
                    <option key={agent} value={agent}>
                      {agent === 'progenitor' ? 'Run as progenitor' : `Call ${agent}`}
                    </option>
                  ))}
                </select>
              </div>
              {(data.sourcePath || associatedAgents.length > 0) && (
                <div className="prompt-source-meta">
                  {data.sourcePath && (
                    <div className="schedule-info">
                      <div className="schedule-row">
                        <span className="schedule-row-label">Source</span>
                        <span className="schedule-row-value">{data.sourcePath}</span>
                      </div>
                    </div>
                  )}
                  {associatedAgents.length > 0 && (
                    <div className="agent-chip-row">
                      {associatedAgents.map((agent) => (
                        <span key={agent} className="agent-chip">{agent}</span>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {resolvedActiveTab === 'output' ? (
                <div className="node-tab-panel">
                  <div className="node-section-label">Latest response</div>
                  {data.lastOutput || data.summaryState ? (
                    <div className="node-output-panel">{data.lastOutput || data.summaryState}</div>
                  ) : (
                    <div className="agent-empty-state">No output yet. Run the prompt to see a response.</div>
                  )}
                  {data.linkedOutputIds?.length > 0 && (
                    <div className="agent-chip-row">
                      {data.linkedOutputIds.map((outputId) => (
                        <span key={outputId} className="agent-chip">{outputId}</span>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <div className="prompt-editor-wrap">
                  {isPromptEmpty && (
                    <div className="prompt-starters">
                      <span className="prompt-starters-label">Start with</span>
                      {PROMPT_STARTERS.map((starter) => (
                        <button
                          key={starter.label}
                          type="button"
                          className="prompt-starter-chip nodrag"
                          onClick={() => applyStarter(starter.text)}
                        >
                          {starter.label}
                        </button>
                      ))}
                      <button
                        type="button"
                        className="prompt-starter-chip nodrag"
                        onClick={insertVariable}
                        title="Insert a {{ variable }} placeholder"
                      >
                        + Variable
                      </button>
                    </div>
                  )}
                  <textarea
                    className="prompt-editor nodrag nowheel"
                    value={promptText}
                    onChange={onPromptChange}
                    onKeyDown={onEditorKeyDown}
                    placeholder="Enter your prompt..."
                  />
                  <div className="prompt-editor-meta">
                    <span className="prompt-char-count">{promptText.length} chars</span>
                  </div>
                </div>
              )}
              {data.tags?.length > 0 && (
                <div className="agent-chip-row">
                  {data.tags.map((tag, i) => (
                    <span key={i} className="agent-chip">{tag}</span>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="node-body-compact">
              {(data.sourcePath || associatedAgents.length > 0) && (
                <div className="prompt-source-meta compact">
                  {data.sourcePath && (
                    <div className="schedule-info">
                      <div className="schedule-row">
                        <span className="schedule-row-label">Source</span>
                        <span className="schedule-row-value">{data.sourcePath}</span>
                      </div>
                    </div>
                  )}
                  {associatedAgents.length > 0 && (
                    <div className="agent-chip-row">
                      {associatedAgents.map((agent) => (
                        <span key={agent} className="agent-chip">{agent}</span>
                      ))}
                    </div>
                  )}
                </div>
              )}
              <div className="prompt-preview">{promptPreview}</div>
              {responsePreview && <div className="prompt-response-preview">{responsePreview}</div>}
              {data.tags?.length > 0 && (
                <div className="agent-chip-row">
                  {data.tags.map((tag, i) => (
                    <span key={i} className="agent-chip">{tag}</span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {expanded && (
          <div className="node-actions node-actions--split">
            <div
              className="node-toggle nodrag"
              role="switch"
              aria-checked={enabled}
              tabIndex={0}
              onClick={onToggleEnabled}
              onKeyDown={onToggleEnabledKeyDown}
              title={enabled ? 'Disable prompt' : 'Enable prompt'}
            >
              <span className={`node-toggle-switch ${enabled ? 'enabled' : ''}`} />
              <span className="node-toggle-label">{enabled ? 'Enabled' : 'Disabled'}</span>
            </div>
            <DetailHeaderActions>
              <DetailHeaderAction
                icon={<PlayActionIcon />}
                ariaLabel="Run prompt"
                tooltip="Run"
                onClick={() => runPromptNode(id)}
                disabled={!enabled}
              />
              <DetailHeaderAction
                icon={<DeleteActionIcon />}
                ariaLabel="Delete prompt"
                tooltip="Delete"
                variant="danger"
                className="node-delete-action"
                onClick={onDelete}
              />
              <NodeHelpButton description={helpText} title={data.title} />
            </DetailHeaderActions>
          </div>
        )}
      </div>
    </>
  );
}

export default memo(PromptNode);
