import { memo, useCallback, useRef } from 'react';
import { NodeResizer } from '@xyflow/react';
import { useWorkspaceContext } from '../../../hooks/useWorkspaceContext';

function PromptNode({ id, data, selected }) {
  const { updateNodeData, toggleExpanded } = useWorkspaceContext();
  const titleRef = useRef(null);
  const { expanded } = data;

  const onTitleChange = useCallback((e) => {
    updateNodeData(id, { title: e.target.value });
  }, [id, updateNodeData]);

  const onPromptChange = useCallback((e) => {
    updateNodeData(id, { prompt: e.target.value });
  }, [id, updateNodeData]);

  const onRoleChange = useCallback((e) => {
    updateNodeData(id, { role: e.target.value });
  }, [id, updateNodeData]);

  const onToggle = useCallback(() => {
    toggleExpanded(id);
  }, [id, toggleExpanded]);

  const promptPreview = data.prompt
    ? data.prompt.substring(0, 120) + (data.prompt.length > 120 ? '...' : '')
    : 'Empty prompt';

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
          <div className="node-type-icon prompt">P</div>
          <input
            className="node-title nodrag"
            value={data.title}
            onChange={onTitleChange}
            ref={titleRef}
          />
          {data.status !== 'idle' && (
            <span className={`node-status-badge ${data.status}`}>{data.status}</span>
          )}
          <button className="node-expand-btn" onClick={onToggle} title={expanded ? 'Collapse' : 'Expand'}>
            {expanded ? '\u25B2' : '\u25BC'}
          </button>
        </div>

        <div className="node-body">
          {expanded ? (
            <div className="node-body-expanded">
              <div className="prompt-meta">
                <input
                  className="nodrag"
                  placeholder="Role (e.g. system, user)"
                  value={data.role || ''}
                  onChange={onRoleChange}
                />
              </div>
              <textarea
                className="prompt-editor nodrag nowheel"
                value={data.prompt || ''}
                onChange={onPromptChange}
                placeholder="Enter your prompt..."
              />
              {data.tags?.length > 0 && (
                <div className="prompt-tags">
                  {data.tags.map((tag, i) => (
                    <span key={i} className="prompt-tag">{tag}</span>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="node-body-compact">
              <div className="prompt-preview">{promptPreview}</div>
              {data.tags?.length > 0 && (
                <div className="prompt-tags">
                  {data.tags.map((tag, i) => (
                    <span key={i} className="prompt-tag">{tag}</span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {expanded && (
          <div className="node-actions">
            <button className="node-action-btn primary">Execute</button>
            <button className="node-action-btn">Send to Agent</button>
          </div>
        )}
      </div>
    </>
  );
}

export default memo(PromptNode);
