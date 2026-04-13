import { memo } from 'react';

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
  const description = getNodeHelpText(nodeType, widgetKind);

  return (
    <div
      className={`node-help-control nodrag${alignRight ? ' align-right' : ''}`}
      tabIndex={0}
      aria-label={`About ${title || 'this'} card`}
    >
      <span className="node-help-btn" aria-hidden="true">
        ?
      </span>
      <div className="node-help-tooltip" role="tooltip">
        {description}
      </div>
    </div>
  );
}

export default memo(NodeHelpButton);
