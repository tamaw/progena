import { memo, useCallback } from 'react';
import { NodeResizer } from '@xyflow/react';
import { useWorkspaceContext } from '../../../context/WorkspaceContext';

function AgentNode({ id, data, selected }) {
  const { toggleExpanded, refreshWorkspace } = useWorkspaceContext();
  const { expanded } = data;
  const widgetKind = data.widgetKind || 'agent';
  const icon = widgetKind === 'mcp' ? 'M' : 'A';
  const titleLabel = widgetKind === 'mcp' ? 'MCP' : 'Agent';

  const onToggle = useCallback(() => {
    toggleExpanded(id);
  }, [id, toggleExpanded]);

  const statusClass = data.agentStatus || 'idle';

  const formatTime = (iso) => {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleTimeString();
    } catch {
      return iso;
    }
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
      <div className={`workspace-node ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar">
          <div className="node-type-icon agent">{icon}</div>
          <div className="node-title nodrag">{data.title}</div>
          <div className={`agent-status-indicator ${statusClass}`} title={statusClass} />
          <button className="node-expand-btn" onClick={onToggle}>
            {expanded ? '\u25B2' : '\u25BC'}
          </button>
        </div>

        <div className="node-body">
          {expanded ? (
            <div className="node-body-expanded">
              <div className="schedule-info" style={{ marginBottom: 'var(--spacing-sm)' }}>
                <div className="schedule-row">
                  <span className="schedule-row-label">Status</span>
                  <span className={`node-status-badge ${statusClass}`}>{statusClass}</span>
                </div>
                {data.currentRunId && (
                  <div className="schedule-row">
                    <span className="schedule-row-label">Run ID</span>
                    <span className="schedule-row-value">{data.currentRunId}</span>
                  </div>
                )}
              </div>

              {data.summaryState && (
                <div className="agent-summary" style={{ marginBottom: 'var(--spacing-sm)' }}>
                  {data.summaryState}
                </div>
              )}

              {data.logs?.length > 0 && (
                <>
                  <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)', marginBottom: '4px' }}>
                    Recent Events
                  </div>
                  <div className="agent-events">
                    {data.logs.slice(-10).map((log, i) => (
                      <div key={i} className="agent-event-item">
                        <span className="agent-event-time">{formatTime(log.time)}</span>
                        <span>{log.message}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}

              <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)', marginBottom: '4px' }}>
                Logs
              </div>
              <div className="agent-logs nodrag nowheel">
                {data.logs?.length > 0
                  ? data.logs.map((l) => `[${formatTime(l.time)}] ${l.message}`).join('\n')
                  : 'No logs available'}
              </div>
            </div>
          ) : (
            <div className="node-body-compact">
              <div className="agent-summary">
                {data.summaryState || `${titleLabel} ${statusClass}`}
              </div>
              {data.updatedAt && (
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)', marginTop: '4px' }}>
                  Updated {formatTime(data.updatedAt)}
                </div>
              )}
            </div>
          )}
        </div>

        {expanded && (
          <div className="node-actions">
            <button className="node-action-btn primary" onClick={refreshWorkspace}>Refresh</button>
            {data.currentRunId && (
              <button className="node-action-btn">Inspect Run</button>
            )}
          </div>
        )}
      </div>
    </>
  );
}

export default memo(AgentNode);
