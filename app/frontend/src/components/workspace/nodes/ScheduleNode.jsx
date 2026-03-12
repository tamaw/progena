import { memo, useCallback } from 'react';
import { NodeResizer } from '@xyflow/react';
import { useWorkspaceContext } from '../../../hooks/useWorkspaceContext';

function ScheduleNode({ id, data, selected }) {
  const { updateNodeData, toggleExpanded } = useWorkspaceContext();
  const { expanded } = data;

  const onTitleChange = useCallback((e) => {
    updateNodeData(id, { title: e.target.value });
  }, [id, updateNodeData]);

  const onToggle = useCallback(() => {
    toggleExpanded(id);
  }, [id, toggleExpanded]);

  const onFieldChange = useCallback((field) => (e) => {
    updateNodeData(id, { [field]: e.target.value });
  }, [id, updateNodeData]);

  const onToggleEnabled = useCallback(() => {
    updateNodeData(id, { enabled: !data.enabled });
  }, [id, data.enabled, updateNodeData]);

  const formatTime = (iso) => {
    if (!iso) return '--';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  return (
    <>
      <NodeResizer
        minWidth={240}
        minHeight={120}
        isVisible={selected}
        lineClassName="react-flow__resize-control line"
        handleClassName="react-flow__resize-control handle"
      />
      <div className={`workspace-node ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar">
          <div className="node-type-icon schedule">S</div>
          <input
            className="node-title nodrag"
            value={data.title}
            onChange={onTitleChange}
          />
          <span className={`schedule-enabled-badge ${data.enabled ? 'enabled' : 'disabled'}`}>
            {data.enabled ? 'ON' : 'OFF'}
          </span>
          {data.lastRunStatus && data.lastRunStatus !== 'idle' && (
            <span className={`node-status-badge ${data.lastRunStatus}`}>
              {data.lastRunStatus}
            </span>
          )}
          <button className="node-expand-btn" onClick={onToggle}>
            {expanded ? '\u25B2' : '\u25BC'}
          </button>
        </div>

        <div className="node-body">
          {expanded ? (
            <div className="node-body-expanded">
              <div className="schedule-form">
                <div className="schedule-form-field">
                  <label>Type</label>
                  <select
                    className="nodrag"
                    value={data.scheduleType || 'cron'}
                    onChange={onFieldChange('scheduleType')}
                  >
                    <option value="once">One-off</option>
                    <option value="cron">Cron</option>
                    <option value="interval">Interval</option>
                  </select>
                </div>

                <div className="schedule-form-field">
                  <label>Expression</label>
                  <input
                    className="nodrag"
                    value={data.expression || ''}
                    onChange={onFieldChange('expression')}
                    placeholder={data.scheduleType === 'cron' ? '*/5 * * * *' : data.scheduleType === 'interval' ? '300s' : '2025-01-01T00:00:00Z'}
                  />
                </div>

                <div className="schedule-form-field">
                  <label>Timezone</label>
                  <input
                    className="nodrag"
                    value={data.timezone || ''}
                    onChange={onFieldChange('timezone')}
                    placeholder="UTC"
                  />
                </div>

                <div className="schedule-toggle" onClick={onToggleEnabled}>
                  <div className={`schedule-toggle-switch ${data.enabled ? 'enabled' : ''}`} />
                  <span style={{ fontSize: 'var(--font-size-sm)' }}>
                    {data.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </div>

                <div className="schedule-info">
                  <div className="schedule-row">
                    <span className="schedule-row-label">Next run</span>
                    <span className="schedule-row-value">{formatTime(data.nextRunAt)}</span>
                  </div>
                  <div className="schedule-row">
                    <span className="schedule-row-label">Last run</span>
                    <span className="schedule-row-value">{formatTime(data.lastRunAt)}</span>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div className="node-body-compact">
              <div className="schedule-info">
                <div className="schedule-row">
                  <span className="schedule-row-label">Next</span>
                  <span className="schedule-row-value">{formatTime(data.nextRunAt)}</span>
                </div>
                <div className="schedule-row">
                  <span className="schedule-row-label">Type</span>
                  <span className="schedule-row-value">{data.scheduleType || 'cron'}</span>
                </div>
                {data.expression && (
                  <div className="schedule-row">
                    <span className="schedule-row-label">Expr</span>
                    <span className="schedule-row-value">{data.expression}</span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {expanded && (
          <div className="node-actions">
            <button className="node-action-btn primary">Run Now</button>
          </div>
        )}
      </div>
    </>
  );
}

export default memo(ScheduleNode);
