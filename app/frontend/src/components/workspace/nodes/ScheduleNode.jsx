import { memo, useCallback, useMemo, useState } from 'react';
import { Handle, NodeResizer, Position } from '@xyflow/react';
import { useWorkspaceActions, useWorkspaceMeta } from '../../../context/WorkspaceContext';
import WidgetTabs from './WidgetTabs';
import NodeHelpButton from './NodeHelpButton';
import { resolveWidgetDefaultTab } from './widgetTabUtils';
import { DetailHeaderActions, DetailHeaderAction, PlayActionIcon, SaveActionIcon, DeleteActionIcon } from '../../layout/DetailHeader';

function ScheduleNode({ id, data, selected }) {
  const { updateNodeData, toggleExpanded, runScheduleNode, saveScheduleNode, removeNodes } = useWorkspaceActions();
  const { getWidgetTabs, getWidgetDefaultTab, getWidgetHelpText } = useWorkspaceMeta();
  const { expanded } = data;
  const isUnsaved = Boolean(data.isUnsaved && !data.serverManaged);
  const isSaving = Boolean(data.isSaving && !data.serverManaged);
  const tabs = getWidgetTabs('schedule');
  const helpText = getWidgetHelpText('schedule');
  const defaultTab = useMemo(
    () => resolveWidgetDefaultTab(tabs, getWidgetDefaultTab('schedule'), 'details'),
    [getWidgetDefaultTab, tabs]
  );
  const [activeTab, setActiveTab] = useState(defaultTab);
  const resolvedActiveTab = useMemo(
    () => resolveWidgetDefaultTab(tabs, activeTab, defaultTab),
    [activeTab, defaultTab, tabs]
  );

  const onToggle = useCallback(() => {
    toggleExpanded(id);
  }, [id, toggleExpanded]);

  const onFieldChange = useCallback((field) => (e) => {
    updateNodeData(id, { [field]: e.target.value });
  }, [id, updateNodeData]);

  const onToggleEnabled = useCallback(() => {
    updateNodeData(id, { enabled: !data.enabled });
  }, [id, data.enabled, updateNodeData]);

  const enabled = Boolean(data.enabled);

  const onToggleEnabledKeyDown = useCallback((e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onToggleEnabled();
    }
  }, [onToggleEnabled]);

  const onDelete = useCallback(() => {
    removeNodes([id]);
  }, [id, removeNodes]);

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
      <Handle type="target" position={Position.Left} className="workspace-handle" />
      <Handle type="source" position={Position.Right} className="workspace-handle" />
      <div className={`workspace-node ${selected ? 'selected' : ''} ${isUnsaved ? 'schedule-node-unsaved' : ''} ${enabled ? '' : 'is-disabled'}`}>
        <div className="node-title-bar drag-handle">
          <div className="node-type-icon schedule">S</div>
          <div className="node-title">{data.title}</div>
          {isUnsaved && (
            <span className={`node-status-badge ${isSaving ? 'running' : 'editing'}`}>
              {isSaving ? 'saving' : 'unsaved'}
            </span>
          )}
          {!enabled && (
            <span className="node-status-badge disabled">disabled</span>
          )}
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
              <WidgetTabs tabs={tabs} activeTab={resolvedActiveTab} onChange={setActiveTab} />
              {resolvedActiveTab === 'output' ? (
                <div className="node-output-stack">
                  <div className="node-output-panel">{data.lastOutput || data.summaryState || 'No output yet.'}</div>
                  <div className="schedule-info">
                    <div className="schedule-row">
                      <span className="schedule-row-label">Last run</span>
                      <span className="schedule-row-value">{formatTime(data.lastRunAt)}</span>
                    </div>
                    <div className="schedule-row">
                      <span className="schedule-row-label">Status</span>
                      <span className="schedule-row-value">{data.lastRunStatus || 'idle'}</span>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="schedule-form">
                  <div className="schedule-form-field">
                    <label>Task</label>
                    <textarea
                      className="nodrag schedule-task-input"
                      value={data.instruction || ''}
                      onChange={onFieldChange('instruction')}
                      placeholder="What should Progenitor do when this schedule fires?"
                      rows={4}
                    />
                  </div>

                  <div className="schedule-form-field">
                    <label>Type</label>
                    <select
                      className="nodrag app-select"
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

                  <div className="schedule-info">
                    {data.saveError && (
                      <div className="schedule-save-error">{data.saveError}</div>
                    )}
                    {isUnsaved && (
                      <div className="schedule-unsaved-note">
                        This schedule stays local to the workspace until you save it.
                      </div>
                    )}
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
              )}
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
          <div className="node-actions node-actions--split">
            <div
              className="node-toggle nodrag"
              role="switch"
              aria-checked={enabled}
              tabIndex={0}
              onClick={onToggleEnabled}
              onKeyDown={onToggleEnabledKeyDown}
              title={enabled ? 'Disable schedule' : 'Enable schedule'}
            >
              <span className={`node-toggle-switch ${enabled ? 'enabled' : ''}`} />
              <span className="node-toggle-label">{enabled ? 'Enabled' : 'Disabled'}</span>
            </div>
            <DetailHeaderActions>
              {isUnsaved ? (
                <DetailHeaderAction
                  icon={<SaveActionIcon />}
                  ariaLabel="Save schedule"
                  tooltip={isSaving ? 'Saving…' : 'Save'}
                  onClick={() => saveScheduleNode(id)}
                  disabled={isSaving}
                />
              ) : (
                <DetailHeaderAction
                  icon={<PlayActionIcon />}
                  ariaLabel="Run schedule now"
                  tooltip="Run now"
                  onClick={() => runScheduleNode(id)}
                  disabled={!enabled}
                />
              )}
              <DetailHeaderAction
                icon={<DeleteActionIcon />}
                ariaLabel="Delete schedule"
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

export default memo(ScheduleNode);
