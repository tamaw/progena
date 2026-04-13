import { memo, useCallback, useState } from 'react';

function getNodeHelpText(nodeType, widgetKind) {
  if (nodeType === 'agent' && widgetKind === 'mcp') {
    return 'Shows an MCP integration, its recent activity, and the latest thread-linked output for this card.';
  }

  switch (nodeType) {
    case 'user':
      return 'Configures a user-facing tool call so the current run can send a message, notification, or question.';
    case 'prompt':
      return 'Stores prompt text, targets an agent, and runs or generates code from the latest prompt.';
    case 'code':
      return 'Keeps editable code in the workspace and runs it so linked cards can use the latest output.';
    case 'schedule':
      return 'Creates a timed automation that runs Progenitor once, on an interval, or on a cron schedule.';
    case 'event':
      return 'Listens for a workspace event and triggers linked cards when that event fires.';
    case 'webframe':
      return 'Renders HTML or a URL inside the canvas so you can preview generated UI without leaving the workspace.';
    case 'agent':
      return 'Shows a linked agent, its recent activity, and the latest thread-linked output for this card.';
    default:
      return 'Shows what this workspace card does and how it participates in the current run.';
  }
}

function NodeHelpButton({ nodeType, widgetKind, title, alignRight = false }) {
  const [isOpen, setIsOpen] = useState(false);
  const description = getNodeHelpText(nodeType, widgetKind);

  const openModal = useCallback(() => {
    setIsOpen(true);
  }, []);

  const closeModal = useCallback(() => {
    setIsOpen(false);
  }, []);

  return (
    <>
      <div className={`node-help-control nodrag${alignRight ? ' align-right' : ''}`}>
        <button
          type="button"
          className="node-help-btn"
          onClick={openModal}
          aria-label={`About ${title || 'this'} card`}
          aria-haspopup="dialog"
        >
          ?
        </button>
        <div className="node-help-tooltip" role="tooltip">
          {description}
        </div>
      </div>

      {isOpen && (
        <div className="sidebar-modal-backdrop" onClick={closeModal} role="presentation">
          <div
            className="sidebar-modal"
            onClick={(event) => event.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={`About ${title || 'this'} card`}
          >
            <div className="sidebar-modal-header">
              <div>
                <div className="sidebar-details-title">About this card</div>
                <div className="sidebar-modal-title">{title || 'Workspace card'}</div>
              </div>
              <button
                className="sidebar-toggle"
                type="button"
                onClick={closeModal}
                aria-label="Close help"
              >
                &times;
              </button>
            </div>
            <textarea
              className="sidebar-modal-textarea node-help-modal-textarea"
              value={description}
              readOnly
              spellCheck={false}
              autoFocus
            />
          </div>
        </div>
      )}
    </>
  );
}

export default memo(NodeHelpButton);
