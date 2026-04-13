import { memo, useCallback, useMemo, useState } from 'react';
import { Handle, NodeResizer, Position } from '@xyflow/react';
import { useWorkspaceActions, useWorkspaceMeta } from '../../../context/WorkspaceContext';
import WidgetTabs from './WidgetTabs';
import NodeHelpButton from './NodeHelpButton';
import { resolveWidgetDefaultTab } from './widgetTabUtils';

function formatDateTime(value) {
  if (!value) return '';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
}

function formatPrettyJson(value) {
  if (value === null || value === undefined || value === '') {
    return '';
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function getToolParameterNames(tool) {
  const properties = tool?.inputSchema?.properties;
  if (!properties || typeof properties !== 'object') {
    return [];
  }
  return Object.keys(properties);
}

function renderCatalogTitle(item, fallback) {
  return item?.name || item?.title || item?.id || item?.uri || fallback;
}

function buildActionSelection(kind, item, fallback) {
  const value = item?.id || item?.name || item?.uri || item?.title || fallback;
  return {
    key: `${kind}:${String(value)}`,
    kind,
    label: renderCatalogTitle(item, fallback),
    value: String(value),
    description: item?.description || item?.title || item?.uri || '',
  };
}

function formatActionLabel(selection) {
  if (!selection) {
    return '';
  }

  const kind = selection.kind
    ? `${selection.kind.charAt(0).toUpperCase()}${selection.kind.slice(1)}: `
    : '';
  return `${kind}${selection.label}`;
}

function resolveAvailabilityStatus(data) {
  const explicitStatus = String(data.availabilityStatus || '').trim().toLowerCase();
  if (explicitStatus) {
    return explicitStatus;
  }

  if (data.refreshError) {
    return 'failed';
  }

  const runtimeStatus = String(data.agentStatus || data.status || '').trim().toLowerCase();
  if (['offline', 'disabled', 'stopped'].includes(runtimeStatus)) {
    return 'offline';
  }
  if (['failed', 'error'].includes(runtimeStatus)) {
    return 'failed';
  }
  if (data.inspection) {
    return 'online';
  }
  if (['running', 'busy', 'ready', 'enabled', 'completed', 'success'].includes(runtimeStatus)) {
    return 'online';
  }
  return 'offline';
}

function getAvailabilityBadgeClass(status) {
  if (status === 'online') {
    return 'online';
  }
  if (status === 'failed') {
    return 'failed';
  }
  return 'offline';
}

function hasDisplayContent(value) {
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (value && typeof value === 'object') {
    return Object.keys(value).length > 0;
  }
  return Boolean(value);
}

function AgentNode({ id, data, selected }) {
  const {
    toggleExpanded,
    refreshWidgetNode,
    updateNodeData,
  } = useWorkspaceActions();
  const { getWidgetTabs, getWidgetDefaultTab } = useWorkspaceMeta();
  const { expanded } = data;
  const widgetKind = data.widgetKind || 'agent';
  const icon = widgetKind === 'mcp' ? 'M' : 'A';
  const titleLabel = widgetKind === 'mcp' ? 'MCP' : 'Agent';
  const actionTabId = widgetKind === 'agent' ? 'skills' : 'tools';
  const actionTabLabel = widgetKind === 'agent' ? 'Skills' : 'Tools';
  const baseTabs = getWidgetTabs(widgetKind);
  const inspection = data.inspection || null;
  const inspectionErrors = inspection?.errors || {};
  const statusClass = data.agentStatus || data.status || 'idle';
  const availabilityStatus = resolveAvailabilityStatus(data);
  const availabilityBadgeClass = getAvailabilityBadgeClass(availabilityStatus);
  const catalogItems = useMemo(
    () => (widgetKind === 'agent' ? (inspection?.skills || []) : (inspection?.tools || [])),
    [inspection, widgetKind]
  );
  const resourceItems = useMemo(
    () => (widgetKind === 'mcp' ? (inspection?.resources || []) : []),
    [inspection, widgetKind]
  );
  const promptItems = useMemo(
    () => (widgetKind === 'mcp' ? (inspection?.prompts || []) : []),
    [inspection, widgetKind]
  );
  const hasCapabilities = widgetKind === 'agent' && hasDisplayContent(inspection?.capabilities);
  const tabs = useMemo(
    () => baseTabs.filter((tab) => tab.id !== 'capabilities' || hasCapabilities),
    [baseTabs, hasCapabilities]
  );
  const defaultTab = useMemo(
    () => resolveWidgetDefaultTab(tabs, getWidgetDefaultTab(widgetKind), 'overview'),
    [getWidgetDefaultTab, tabs, widgetKind]
  );
  const [activeTab, setActiveTab] = useState(defaultTab);
  const resolvedActiveTab = useMemo(
    () => resolveWidgetDefaultTab(tabs, activeTab, defaultTab),
    [activeTab, defaultTab, tabs]
  );
  const endpoint = widgetKind === 'mcp' ? data.url || inspection?.endpoint : data.url || inspection?.card_url;
  const selectedAction = data.selectedAction || null;
  const compactSummary = selectedAction
    ? `Selected ${formatActionLabel(selectedAction)}`
    : (data.summaryState || `${titleLabel} ${statusClass}`);

  const onToggle = useCallback(() => {
    toggleExpanded(id);
  }, [id, toggleExpanded]);

  const onRefresh = useCallback(() => {
    refreshWidgetNode(id);
  }, [id, refreshWidgetNode]);

  const selectAction = useCallback((selection) => {
    updateNodeData(id, { selectedAction: selection });
  }, [id, updateNodeData]);

  const onRun = useCallback(() => {
    if (selectedAction) {
      setActiveTab('overview');
      return;
    }

    let fallbackSelection = null;
    if (widgetKind === 'agent' && catalogItems.length > 0) {
      fallbackSelection = buildActionSelection('skill', catalogItems[0], `${titleLabel} skill 1`);
    } else if (widgetKind === 'mcp' && catalogItems.length > 0) {
      fallbackSelection = buildActionSelection('tool', catalogItems[0], `${titleLabel} tool 1`);
    } else if (widgetKind === 'mcp' && promptItems.length > 0) {
      fallbackSelection = buildActionSelection('prompt', promptItems[0], 'Prompt 1');
    } else if (widgetKind === 'mcp' && resourceItems.length > 0) {
      fallbackSelection = buildActionSelection('resource', resourceItems[0], 'Resource 1');
    }

    if (fallbackSelection) {
      updateNodeData(id, { selectedAction: fallbackSelection });
      setActiveTab('overview');
      return;
    }

    setActiveTab(actionTabId);
  }, [
    actionTabId,
    catalogItems,
    id,
    promptItems,
    resourceItems,
    selectedAction,
    titleLabel,
    updateNodeData,
    widgetKind,
  ]);

  const renderOverview = () => (
    <div className="node-tab-panel">
      <div className="schedule-info" style={{ marginBottom: 'var(--spacing-sm)' }}>
        <div className="schedule-row">
          <span className="schedule-row-label">Availability</span>
          <span className={`node-status-badge ${availabilityBadgeClass}`}>{availabilityStatus}</span>
        </div>
        <div className="schedule-row">
          <span className="schedule-row-label">Action</span>
          <span className="schedule-row-value">
            {selectedAction ? formatActionLabel(selectedAction) : `Select from ${actionTabLabel}`}
          </span>
        </div>
        {data.transport && (
          <div className="schedule-row">
            <span className="schedule-row-label">Transport</span>
            <span className="schedule-row-value">{data.transport}</span>
          </div>
        )}
        {endpoint && (
          <div className="schedule-row">
            <span className="schedule-row-label">Endpoint</span>
            <span className="schedule-row-value">{endpoint}</span>
          </div>
        )}
        {data.currentThreadId && (
          <div className="schedule-row">
            <span className="schedule-row-label">Thread ID</span>
            <span className="schedule-row-value">{data.currentThreadId}</span>
          </div>
        )}
        {inspection?.refreshed_at && (
          <div className="schedule-row">
            <span className="schedule-row-label">Refreshed</span>
            <span className="schedule-row-value">{formatDateTime(inspection.refreshed_at)}</span>
          </div>
        )}
      </div>

      {data.refreshError && (
        <div className="agent-empty-state error">{data.refreshError}</div>
      )}

      <div className="node-section-label">Summary</div>
      <div className="node-output-panel">
        {data.summaryState || data.description || `No ${titleLabel.toLowerCase()} details yet.`}
      </div>

      {data.logs?.length > 0 && (
        <>
          <div className="node-section-label">Recent Events</div>
          <div className="agent-events">
            {data.logs.slice(0, 10).map((log, index) => (
              <div key={`${log.time || 'log'}-${index}`} className="agent-event-item">
                <span className="agent-event-time">{formatDateTime(log.time)}</span>
                <span>{log.message}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );

  const renderDetails = () => {
    if (!inspection) {
      return (
        <div className="agent-empty-state">
          Use Refresh to load the latest {widgetKind === 'mcp' ? 'MCP metadata' : 'agent card'}.
        </div>
      );
    }

    return (
      <div className="node-tab-panel">
        <div className="schedule-info">
          <div className="schedule-row">
            <span className="schedule-row-label">Refreshed</span>
            <span className="schedule-row-value">{formatDateTime(inspection.refreshed_at)}</span>
          </div>
          {data.transport && (
            <div className="schedule-row">
              <span className="schedule-row-label">Transport</span>
              <span className="schedule-row-value">{data.transport}</span>
            </div>
          )}
          {endpoint && (
            <div className="schedule-row">
              <span className="schedule-row-label">Endpoint</span>
              <span className="schedule-row-value">{endpoint}</span>
            </div>
          )}
          {widgetKind === 'agent' && inspection.card_url && (
            <div className="schedule-row">
              <span className="schedule-row-label">Agent Card</span>
              <span className="schedule-row-value">{inspection.card_url}</span>
            </div>
          )}
          {inspection.protocol_version && (
            <div className="schedule-row">
              <span className="schedule-row-label">Protocol</span>
              <span className="schedule-row-value">{inspection.protocol_version}</span>
            </div>
          )}
          {widgetKind === 'agent' && inspection.preferred_transport && (
            <div className="schedule-row">
              <span className="schedule-row-label">Preferred</span>
              <span className="schedule-row-value">{inspection.preferred_transport}</span>
            </div>
          )}
          {widgetKind === 'agent' && inspection.default_input_modes?.length > 0 && (
            <div className="schedule-row">
              <span className="schedule-row-label">Input Modes</span>
              <span className="schedule-row-value">{inspection.default_input_modes.join(', ')}</span>
            </div>
          )}
          {widgetKind === 'agent' && inspection.default_output_modes?.length > 0 && (
            <div className="schedule-row">
              <span className="schedule-row-label">Output Modes</span>
              <span className="schedule-row-value">{inspection.default_output_modes.join(', ')}</span>
            </div>
          )}
        </div>

        {widgetKind === 'mcp' && inspection.server_info && (
          <div className="agent-section">
            <div className="node-section-label">Server Info</div>
            <pre className="agent-code-block">{formatPrettyJson(inspection.server_info)}</pre>
          </div>
        )}

        {widgetKind === 'mcp' && inspection.capabilities && (
          <div className="agent-section">
            <div className="node-section-label">Capabilities</div>
            <pre className="agent-code-block">{formatPrettyJson(inspection.capabilities)}</pre>
          </div>
        )}

        {widgetKind === 'mcp' && inspection.instructions && (
          <div className="agent-section">
            <div className="node-section-label">Instructions</div>
            <div className="node-output-panel">{inspection.instructions}</div>
          </div>
        )}

        {Object.keys(inspectionErrors).length > 0 && (
          <div className="agent-section">
            <div className="node-section-label">Optional Calls</div>
            <div className="agent-catalog">
              {Object.entries(inspectionErrors).map(([method, message]) => (
                <div key={method} className="agent-catalog-item">
                  <div className="agent-catalog-header">
                    <span className="agent-catalog-title">{method}</span>
                  </div>
                  <div className="agent-catalog-description">{message}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  const renderCapabilities = () => {
    if (!inspection) {
      return (
        <div className="agent-empty-state">
          Use Refresh to load the latest agent card.
        </div>
      );
    }

    if (!hasCapabilities) {
      return (
        <div className="agent-empty-state">
          This agent card did not advertise any capabilities.
        </div>
      );
    }

    return (
      <div className="node-tab-panel">
        <div className="agent-section">
          <div className="node-section-label">Capabilities</div>
          <pre className="agent-code-block">{formatPrettyJson(inspection.capabilities)}</pre>
        </div>
      </div>
    );
  };

  const renderActionCatalog = () => {
    if (catalogItems.length === 0) {
      return (
        <div className="agent-empty-state">
          {inspection
            ? `No ${widgetKind === 'mcp' ? 'MCP tools' : 'agent skills'} were returned.`
            : 'Use Refresh to load the current catalog.'}
        </div>
      );
    }

    return (
      <div className="agent-catalog">
        {catalogItems.map((item, index) => {
          const actionKind = widgetKind === 'agent' ? 'skill' : 'tool';
          const selection = buildActionSelection(
            actionKind,
            item,
            `${widgetKind === 'agent' ? 'Skill' : 'Tool'} ${index + 1}`
          );
          const isSelected = selectedAction?.key === selection.key;

          return (
            <button
              key={selection.key}
              type="button"
              className={`agent-catalog-item nodrag ${isSelected ? 'selected' : ''}`}
              onClick={() => selectAction(selection)}
            >
              <div className="agent-catalog-header">
                <span className="agent-catalog-title">{selection.label}</span>
                {(item.id || item.uri) && (
                  <span className="agent-catalog-id">{item.id || item.uri}</span>
                )}
              </div>
              {(item.description || item.title) && (
                <div className="agent-catalog-description">{item.description || item.title}</div>
              )}
              {widgetKind === 'agent' && item.tags?.length > 0 && (
                <div className="agent-chip-row">
                  {item.tags.map((tag) => (
                    <span key={tag} className="agent-chip">{tag}</span>
                  ))}
                </div>
              )}
              {widgetKind === 'agent' && item.examples?.length > 0 && (
                <div className="agent-catalog-meta">
                  Examples: {item.examples.join(' | ')}
                </div>
              )}
              {widgetKind === 'mcp' && getToolParameterNames(item).length > 0 && (
                <div className="agent-catalog-meta">
                  Parameters: {getToolParameterNames(item).join(', ')}
                </div>
              )}
            </button>
          );
        })}
      </div>
    );
  };

  const renderResources = () => {
    if (resourceItems.length === 0) {
      return (
        <div className="agent-empty-state">
          {inspection ? 'No MCP resources were returned.' : 'Use Refresh to load resources.'}
        </div>
      );
    }

    return (
      <div className="agent-catalog">
        {resourceItems.map((item, index) => {
          const selection = buildActionSelection('resource', item, `Resource ${index + 1}`);
          const isSelected = selectedAction?.key === selection.key;

          return (
            <button
              key={selection.key}
              type="button"
              className={`agent-catalog-item nodrag ${isSelected ? 'selected' : ''}`}
              onClick={() => selectAction(selection)}
            >
              <div className="agent-catalog-header">
                <span className="agent-catalog-title">{selection.label}</span>
              </div>
              {(item.description || item.uri) && (
                <div className="agent-catalog-description">{item.description || item.uri}</div>
              )}
              {(item.uri || item.mimeType) && (
                <div className="agent-catalog-meta">
                  {[item.uri, item.mimeType].filter(Boolean).join(' | ')}
                </div>
              )}
            </button>
          );
        })}
      </div>
    );
  };

  const renderPrompts = () => {
    if (promptItems.length === 0) {
      return (
        <div className="agent-empty-state">
          {inspection ? 'No MCP prompts were returned.' : 'Use Refresh to load prompts.'}
        </div>
      );
    }

    return (
      <div className="agent-catalog">
        {promptItems.map((item, index) => {
          const selection = buildActionSelection('prompt', item, `Prompt ${index + 1}`);
          const isSelected = selectedAction?.key === selection.key;

          return (
            <button
              key={selection.key}
              type="button"
              className={`agent-catalog-item nodrag ${isSelected ? 'selected' : ''}`}
              onClick={() => selectAction(selection)}
            >
              <div className="agent-catalog-header">
                <span className="agent-catalog-title">{selection.label}</span>
                {item.name && <span className="agent-catalog-id">{item.name}</span>}
              </div>
              {item.description && (
                <div className="agent-catalog-description">{item.description}</div>
              )}
              {item.arguments?.length > 0 && (
                <div className="agent-catalog-meta">
                  Arguments: {item.arguments.map((argument) => argument.name).join(', ')}
                </div>
              )}
            </button>
          );
        })}
      </div>
    );
  };

  const renderExpandedBody = () => {
    if (resolvedActiveTab === 'details') {
      return renderDetails();
    }
    if (resolvedActiveTab === 'capabilities') {
      return renderCapabilities();
    }
    if (resolvedActiveTab === 'skills' || resolvedActiveTab === 'tools') {
      return renderActionCatalog();
    }
    if (resolvedActiveTab === 'resources') {
      return renderResources();
    }
    if (resolvedActiveTab === 'prompts') {
      return renderPrompts();
    }
    if (resolvedActiveTab === 'logs') {
      return (
        <div className="agent-logs nodrag nowheel">
          {data.logs?.length > 0
            ? data.logs.map((entry) => `[${formatDateTime(entry.time)}] ${entry.message}`).join('\n')
            : 'No logs available'}
        </div>
      );
    }
    return renderOverview();
  };

  return (
    <>
      <NodeResizer
        minWidth={260}
        minHeight={140}
        isVisible={selected}
        lineClassName="react-flow__resize-control line"
        handleClassName="react-flow__resize-control handle"
      />
      <Handle type="target" position={Position.Left} className="workspace-handle" />
      <Handle type="source" position={Position.Right} className="workspace-handle" />
      <div className={`workspace-node ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <div className="node-type-icon agent">{icon}</div>
          <div className="node-title nodrag">{data.title}</div>
          {statusClass !== 'idle' && (
            <span className={`node-status-badge ${statusClass}`}>{statusClass}</span>
          )}
          <button type="button" className="node-expand-btn" onClick={onToggle}>
            {expanded ? '\u25B2' : '\u25BC'}
          </button>
        </div>

        <div className="node-body">
          {expanded ? (
            <div className="node-body-expanded">
              <WidgetTabs tabs={tabs} activeTab={resolvedActiveTab} onChange={setActiveTab} />
              {renderExpandedBody()}
            </div>
          ) : (
            <div className="node-body-compact">
              <div className="agent-summary">
                {compactSummary}
              </div>
              {data.updatedAt && (
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)', marginTop: '4px' }}>
                  Updated {formatDateTime(data.updatedAt)}
                </div>
              )}
            </div>
          )}
        </div>

        {expanded && (
          <div className="node-actions">
            <button
              type="button"
              className="node-action-btn primary"
              onClick={onRun}
              disabled={data.isRefreshing}
            >
              Run
            </button>
            <button
              type="button"
              className="node-action-btn secondary"
              onClick={onRefresh}
              disabled={data.isRefreshing}
            >
              {data.isRefreshing ? 'Refreshing...' : 'Refresh'}
            </button>
            <NodeHelpButton
              nodeType="agent"
              widgetKind={widgetKind}
              title={data.title}
              alignRight
            />
          </div>
        )}
      </div>
    </>
  );
}

export default memo(AgentNode);
