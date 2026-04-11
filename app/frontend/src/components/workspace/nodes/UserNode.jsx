import { memo, useCallback, useMemo, useState } from 'react';
import { Handle, NodeResizer, Position } from '@xyflow/react';
import { useWorkspaceActions, useWorkspaceMeta } from '../../../context/WorkspaceContext';
import WidgetTabs from './WidgetTabs';
import { resolveWidgetDefaultTab } from './widgetTabUtils';

const MESSAGE_TYPE_OPTIONS = [
  { value: 'say_to_user', label: 'say_to_user' },
  { value: 'notify_user', label: 'notify_user' },
  { value: 'ask_user', label: 'ask_user' },
];

const NOTIFICATION_LEVELS = [
  { value: 'info', label: 'Info' },
  { value: 'warning', label: 'Warning' },
  { value: 'error', label: 'Error' },
  { value: 'success', label: 'Success' },
];

const MESSAGE_TYPE_HINTS = {
  say_to_user: 'Conversational agent message to the user.',
  notify_user: 'Creates a backend-managed alert and lets Progenitor send the user-facing summary.',
  ask_user: 'One-way question to the user via the ask_user tool.',
};

function formatTime(iso) {
  if (!iso) return '--';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function formatMessageType(value) {
  return MESSAGE_TYPE_OPTIONS.find((option) => option.value === value)?.label || value || 'say_to_user';
}

function UserNode({ id, data, selected }) {
  const { updateNodeData, toggleExpanded, runUserNode } = useWorkspaceActions();
  const { getWidgetTabs, getWidgetDefaultTab } = useWorkspaceMeta();
  const { expanded } = data;
  const tabs = getWidgetTabs('user');
  const defaultTab = useMemo(
    () => resolveWidgetDefaultTab(tabs, getWidgetDefaultTab('user'), 'details'),
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

  const onMessageTypeChange = useCallback((event) => {
    updateNodeData(id, { messageType: event.target.value });
  }, [id, updateNodeData]);

  const onMessageChange = useCallback((event) => {
    updateNodeData(id, { message: event.target.value });
  }, [id, updateNodeData]);

  const onNotificationTitleChange = useCallback((event) => {
    updateNodeData(id, { notificationTitle: event.target.value });
  }, [id, updateNodeData]);

  const onLevelChange = useCallback((event) => {
    updateNodeData(id, { level: event.target.value });
  }, [id, updateNodeData]);

  const previewText = data.message
    ? data.message.substring(0, 120) + (data.message.length > 120 ? '...' : '')
    : 'No message configured';

  return (
    <>
      <NodeResizer
        minWidth={280}
        minHeight={180}
        isVisible={selected}
        lineClassName="react-flow__resize-control line"
        handleClassName="react-flow__resize-control handle"
      />
      <Handle type="target" position={Position.Left} className="workspace-handle" />
      <Handle type="source" position={Position.Right} className="workspace-handle" />
      <div className={`workspace-node ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar drag-handle">
          <div className="node-type-icon user">U</div>
          <div className="node-title">{data.title}</div>
          <span className="node-meta-chip">{formatMessageType(data.messageType)}</span>
          {data.status !== 'idle' && (
            <span className={`node-status-badge ${data.status}`}>{data.status}</span>
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
                  <div className="node-output-panel">{data.lastOutput || data.summaryState || 'No messages sent yet.'}</div>
                  {data.summaryState && data.lastOutput && data.summaryState !== data.lastOutput && (
                    <div className="event-summary-note">{data.summaryState}</div>
                  )}
                  <div className="schedule-info">
                    <div className="schedule-row">
                      <span className="schedule-row-label">Message type</span>
                      <span className="schedule-row-value">{formatMessageType(data.messageType)}</span>
                    </div>
                    <div className="schedule-row">
                      <span className="schedule-row-label">Last sent</span>
                      <span className="schedule-row-value">{formatTime(data.lastSentAt)}</span>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="schedule-form">
                  <div className="schedule-form-field">
                    <label>Message Type</label>
                    <select className="nodrag" value={data.messageType || 'say_to_user'} onChange={onMessageTypeChange}>
                      {MESSAGE_TYPE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  {data.messageType === 'notify_user' && (
                    <>
                      <div className="schedule-form-field">
                        <label>Notification Title</label>
                        <input
                          className="nodrag"
                          value={data.notificationTitle || ''}
                          onChange={onNotificationTitleChange}
                          placeholder="Build finished"
                        />
                      </div>
                      <div className="schedule-form-field">
                        <label>Notification Level</label>
                        <select className="nodrag" value={data.level || 'info'} onChange={onLevelChange}>
                          {NOTIFICATION_LEVELS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </div>
                    </>
                  )}

                  <div className="schedule-form-field">
                    <label>
                      {data.messageType === 'notify_user'
                        ? 'Body'
                        : data.messageType === 'ask_user'
                          ? 'Question'
                          : 'Message'}
                    </label>
                    <textarea
                      className="schedule-task-input nodrag nowheel"
                      value={data.message || ''}
                      onChange={onMessageChange}
                      placeholder={
                        data.messageType === 'notify_user'
                          ? 'Tell the user what changed.'
                          : data.messageType === 'ask_user'
                            ? 'What should the agent ask the user?'
                            : 'What should the user see?'
                      }
                    />
                  </div>

                  <div className="event-summary-note">
                    {MESSAGE_TYPE_HINTS[data.messageType || 'say_to_user']}
                  </div>
                  <div className="event-summary-note">
                    This widget asks Progenitor to call the selected tool.
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="node-body-compact">
              <div className="schedule-info">
                <div className="schedule-row">
                  <span className="schedule-row-label">Type</span>
                  <span className="schedule-row-value">{formatMessageType(data.messageType)}</span>
                </div>
                <div className="schedule-row">
                  <span className="schedule-row-label">Preview</span>
                  <span className="schedule-row-value">{previewText}</span>
                </div>
              </div>
            </div>
          )}
        </div>

        {expanded && (
          <div className="node-actions">
            <button className="node-action-btn primary" onClick={() => runUserNode(id)}>
              Send
            </button>
          </div>
        )}
      </div>
    </>
  );
}

export default memo(UserNode);
