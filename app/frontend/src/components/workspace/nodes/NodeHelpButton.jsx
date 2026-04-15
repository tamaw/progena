import { memo } from 'react';

function NodeHelpButton({ description, title, alignRight = false }) {
  const helpText = description || 'Shows what this workspace card does and how it participates in the current run.';

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
        {helpText}
      </div>
    </div>
  );
}

export default memo(NodeHelpButton);
