import { memo, useEffect, useRef } from 'react';

function NodeContextMenu({ x, y, nodeId, onClose, onAction }) {
  const menuRef = useRef(null);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        onClose();
      }
    };
    const handleEscape = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [onClose]);

  // Adjust position to stay within viewport
  useEffect(() => {
    if (menuRef.current) {
      const rect = menuRef.current.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      if (rect.right > vw) {
        menuRef.current.style.left = `${x - rect.width}px`;
      }
      if (rect.bottom > vh) {
        menuRef.current.style.top = `${y - rect.height}px`;
      }
    }
  }, [x, y]);

  const handleAction = (action) => () => {
    onAction(action, nodeId);
    onClose();
  };

  if (nodeId) {
    return (
      <div className="node-context-menu" ref={menuRef} style={{ left: x, top: y }}>
        <button className="context-menu-item" onClick={handleAction('expand')}>
          <span>Toggle Expand</span>
          <span className="context-menu-shortcut">Dbl-click</span>
        </button>
        <button className="context-menu-item" onClick={handleAction('duplicate')}>
          <span>Duplicate</span>
          <span className="context-menu-shortcut">Ctrl+D</span>
        </button>
        <div className="context-menu-separator" />
        <button className="context-menu-item" onClick={handleAction('bringToFront')}>
          <span>Bring to Front</span>
        </button>
        <button className="context-menu-item" onClick={handleAction('sendToBack')}>
          <span>Send to Back</span>
        </button>
        <div className="context-menu-separator" />
        <button className="context-menu-item danger" onClick={handleAction('delete')}>
          <span>Delete</span>
          <span className="context-menu-shortcut">Del</span>
        </button>
      </div>
    );
  }

  // Canvas context menu (no node selected)
  return (
    <div className="node-context-menu" ref={menuRef} style={{ left: x, top: y }}>
      <button className="context-menu-item" onClick={handleAction('fitView')}>
        <span>Fit View</span>
      </button>
      <button className="context-menu-item" onClick={handleAction('resetView')}>
        <span>Reset View</span>
      </button>
    </div>
  );
}

export default memo(NodeContextMenu);
