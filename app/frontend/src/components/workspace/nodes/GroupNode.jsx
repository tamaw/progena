import { memo, useCallback } from 'react';
import { NodeResizer } from '@xyflow/react';
import { useWorkspaceContext } from '../../../hooks/useWorkspaceContext';

function GroupNode({ id, data, selected }) {
  const { updateNodeData } = useWorkspaceContext();

  const onTitleChange = useCallback((e) => {
    updateNodeData(id, { title: e.target.value });
  }, [id, updateNodeData]);

  const onDescriptionChange = useCallback((e) => {
    updateNodeData(id, { description: e.target.value });
  }, [id, updateNodeData]);

  return (
    <>
      <NodeResizer
        minWidth={300}
        minHeight={200}
        isVisible={selected}
        lineClassName="react-flow__resize-control line"
        handleClassName="react-flow__resize-control handle"
      />
      <div className={`workspace-node group-node ${selected ? 'selected' : ''}`}>
        <div className="node-title-bar">
          <div className="node-type-icon group">G</div>
          <input
            className="node-title nodrag"
            value={data.title}
            onChange={onTitleChange}
          />
        </div>
        <div className="node-body">
          <textarea
            className="group-description nodrag nowheel"
            value={data.description || ''}
            onChange={onDescriptionChange}
            placeholder="Group description..."
          />
        </div>
      </div>
    </>
  );
}

export default memo(GroupNode);
