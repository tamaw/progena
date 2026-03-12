import { memo } from 'react';

const paletteItems = [
  {
    type: 'prompt',
    name: 'Prompt',
    description: 'Agent instruction or task',
    icon: 'P',
  },
  {
    type: 'code',
    name: 'Code',
    description: 'Executable source code',
    icon: '<>',
  },
  {
    type: 'webframe',
    name: 'Web Frame',
    description: 'Embedded web content',
    icon: 'W',
  },
  {
    type: 'label',
    name: 'Label',
    description: 'Text annotation',
    icon: 'T',
  },
  {
    type: 'schedule',
    name: 'Schedule',
    description: 'Timed task or cron job',
    icon: 'S',
  },
  {
    type: 'group',
    name: 'Group',
    description: 'Container for blocks',
    icon: 'G',
  },
  {
    type: 'agent',
    name: 'Agent',
    description: 'Progenitor Agent status',
    icon: 'A',
  },
];

function WorkspaceSidebar({ collapsed, onToggle }) {
  const onDragStart = (event, nodeType) => {
    event.dataTransfer.setData('application/reactflow', nodeType);
    event.dataTransfer.effectAllowed = 'move';
  };

  return (
    <aside className={`workspace-sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="sidebar-header">
        <h3>Elements</h3>
        <button
          className="sidebar-toggle"
          onClick={onToggle}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? '\u25B6' : '\u25C0'}
        </button>
      </div>
      <div className="sidebar-palette">
        {paletteItems.map((item) => (
          <div
            key={item.type}
            className="palette-item"
            draggable
            onDragStart={(e) => onDragStart(e, item.type)}
            title={item.description}
          >
            <div className={`palette-item-icon ${item.type}`}>
              {item.icon}
            </div>
            <div className="palette-item-info">
              <span className="palette-item-name">{item.name}</span>
              <span className="palette-item-desc">{item.description}</span>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}

export default memo(WorkspaceSidebar);
