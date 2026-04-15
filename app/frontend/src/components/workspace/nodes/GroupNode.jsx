import { memo, useCallback } from 'react';
import { NodeResizer } from '@xyflow/react';
import { useWorkspaceActions } from '../../../context/WorkspaceContext';

function GroupNode({ id, data, selected }) {
  const { updateNodeData } = useWorkspaceActions();

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
        <div className="node-title-bar drag-handle">
          <div className="node-type-icon group">G</div>
          <div className="node-title">{data.title}</div>
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
