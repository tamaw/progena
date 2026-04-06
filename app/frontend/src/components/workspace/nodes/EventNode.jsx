import { memo, useCallback, useMemo, useState } from 'react';
import { NodeResizer } from '@xyflow/react';
import { useWorkspaceContext } from '../../../context/WorkspaceContext';
import WidgetTabs from './WidgetTabs';
import { resolveWidgetDefaultTab } from './widgetTabUtils';

const eventTypeOptions = [
  { value: 'kanban.card.created', label: 'Card created' },
  { value: 'kanban.card.updated', label: 'Card updated' },
  { value: 'kanban.card.moved', label: 'Card moved' },
  { value: 'kanban.card.deleted', label: 'Card deleted' },
  { value: 'kanban.card.status_changed', label: 'Status changed' },
];

function formatTime(iso) {
  if (!iso) return '--';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function formatPayload(payload) {
  if (!payload) {
    return 'No payload captured yet.';
  }
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}

function formatEventLabel(eventType) {
  return eventTypeOptions.find((option) => option.value === eventType)?.label || eventType || 'Unknown event';
}

function EventNode({ id, data, selected }) {
  const { updateNodeData, toggleExpanded, saveEventNode, getWidgetTabs, getWidgetDefaultTab } = useWorkspaceContext();
  const { expanded } = data;
  const isUnsaved = Boolean(data.isUnsaved && !data.serverManaged);
  const isSaving = Boolean(data.isSaving && !data.serverManaged);
  const tabs = getWidgetTabs('event');
  const defaultTab = useMemo(
    () => resolveWidgetDefaultTab(tabs, getWidgetDefaultTab('event'), 'details'),
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

  const onEventTypeChange = useCallback((event) => {
    updateNodeData(id, { eventType: event.target.value });
  }, [id, updateNodeData]);

  const onToggleEnabled = useCallback(() => {
    updateNodeData(id, { enabled: !data.enabled });
  }, [data.enabled, id, updateNodeData]);

  const payloadText = useMemo(() => formatPayload(data.lastPayload), [data.lastPayload]);
  const recentLogs = data.logs?.slice(0, 5) || [];

  return (
    <>
      <NodeResizer
        minWidth={260}
        minHeight={140}
        isVisible={selected}
        lineClassName="react-flow__resize-control line"
        handleClassName="react-flow__resize-control handle"
      />
      <div className={`workspace-node ${selected ? 'selected' : ''} ${isUnsaved ? 'schedule-node-unsaved' : ''}`}>
        <div className="node-title-bar drag-handle">
          <div className="node-type-icon event">E</div>
          <div className="node-title">{data.title}</div>
          {isUnsaved && (
            <span className={`node-status-badge ${isSaving ? 'running' : 'editing'}`}>
              {isSaving ? 'saving' : 'unsaved'}
            </span>
          )}
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
              <WidgetTabs tabs={tabs} activeTab={resolvedActiveTab} onChange={setActiveTab} />
              {resolvedActiveTab === 'output' ? (
                <div className="node-output-stack">
                  <div className="node-output-panel">{data.summaryState || 'No event has fired yet.'}</div>
                  <div className="node-section-label">Last Payload</div>
                  <pre className="event-payload-panel">{payloadText}</pre>
                  {recentLogs.length > 0 && (
                    <>
                      <div className="node-section-label">Recent Triggers</div>
                      <div className="event-log-list">
                        {recentLogs.map((entry, index) => (
                          <div key={`${entry.time || index}-${index}`} className="event-log-item">
                            <span className="event-log-time">{formatTime(entry.time)}</span>
                            <span>{entry.message}</span>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              ) : (
                <div className="schedule-form">
                  <div className="schedule-form-field">
                    <label>Event Type</label>
                    <select className="nodrag" value={data.eventType || ''} onChange={onEventTypeChange}>
                      {eventTypeOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="schedule-toggle" onClick={onToggleEnabled}>
                    <div className={`schedule-toggle-switch ${data.enabled ? 'enabled' : ''}`} />
                    <span style={{ fontSize: 'var(--font-size-sm)' }}>
                      {data.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                  </div>

                  <div className="event-summary-note">
                    Listening for <strong>{formatEventLabel(data.eventType)}</strong> from the kanban board.
                  </div>

                  <div className="schedule-info">
                    {data.saveError && (
                      <div className="schedule-save-error">{data.saveError}</div>
                    )}
                    {isUnsaved && (
                      <div className="schedule-unsaved-note">
                        This event trigger stays local to the workspace until you save it.
                      </div>
                    )}
                    <div className="schedule-row">
                      <span className="schedule-row-label">Trigger count</span>
                      <span className="schedule-row-value">{data.triggerCount || 0}</span>
                    </div>
                    <div className="schedule-row">
                      <span className="schedule-row-label">Last triggered</span>
                      <span className="schedule-row-value">{formatTime(data.lastTriggeredAt)}</span>
                    </div>
                    <div className="schedule-row">
                      <span className="schedule-row-label">Last status</span>
                      <span className="schedule-row-value">{data.lastRunStatus || 'idle'}</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="node-body-compact">
              <div className="schedule-info">
                <div className="schedule-row">
                  <span className="schedule-row-label">Event</span>
                  <span className="schedule-row-value">{formatEventLabel(data.eventType)}</span>
                </div>
                <div className="schedule-row">
                  <span className="schedule-row-label">Triggers</span>
                  <span className="schedule-row-value">{data.triggerCount || 0}</span>
                </div>
                <div className="schedule-row">
                  <span className="schedule-row-label">Last</span>
                  <span className="schedule-row-value">{formatTime(data.lastTriggeredAt)}</span>
                </div>
              </div>
            </div>
          )}
        </div>

        {expanded && isUnsaved && (
          <div className="node-actions">
            <button
              className="node-action-btn primary"
              onClick={() => saveEventNode(id)}
              disabled={isSaving}
            >
              {isSaving ? 'Saving...' : 'Save Event'}
            </button>
          </div>
        )}
      </div>
    </>
  );
}

export default memo(EventNode);
