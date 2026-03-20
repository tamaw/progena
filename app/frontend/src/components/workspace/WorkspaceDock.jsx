import { memo } from 'react';

const dockItems = [
  { type: 'prompt', name: 'Prompt', icon: 'P' },
  { type: 'code', name: 'Code', icon: '<>' },
  { type: 'webframe', name: 'Web', icon: 'W' },
  { type: 'label', name: 'Label', icon: 'T' },
  { type: 'schedule', name: 'Schedule', icon: 'S' },
  { type: 'group', name: 'Group', icon: 'G' },
  { type: 'agent', name: 'Agent', icon: 'A' },
];

function WorkspaceDock({ disabled = false }) {
  const onDragStart = (event, nodeType) => {
    if (disabled) {
      event.preventDefault();
      return;
    }

    event.dataTransfer.setData('application/reactflow', nodeType);
    event.dataTransfer.effectAllowed = 'move';
  };

  return (
    <div className={`workspace-dock ${disabled ? 'disabled' : ''}`}>
      <div className="workspace-dock-shell">
        {dockItems.map((item) => (
          <div
            key={item.type}
            className={`workspace-dock-item ${disabled ? 'disabled' : ''}`}
            draggable={!disabled}
            onDragStart={(event) => onDragStart(event, item.type)}
          >
            <div className={`workspace-dock-icon ${item.type}`}>{item.icon}</div>
            <div className="workspace-dock-tooltip">{item.name}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default memo(WorkspaceDock);
