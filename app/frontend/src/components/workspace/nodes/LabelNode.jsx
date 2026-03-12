import { memo, useCallback } from 'react';
import { NodeResizer } from '@xyflow/react';
import { useWorkspaceContext } from '../../../context/WorkspaceContext';

const VARIANTS = ['title', 'note', 'caption', 'warning'];

function LabelNode({ id, data, selected }) {
  const { updateNodeData } = useWorkspaceContext();

  const onTextChange = useCallback((e) => {
    updateNodeData(id, { text: e.target.value });
  }, [id, updateNodeData]);

  const cycleVariant = useCallback(() => {
    const currentIdx = VARIANTS.indexOf(data.variant || 'note');
    const nextIdx = (currentIdx + 1) % VARIANTS.length;
    updateNodeData(id, { variant: VARIANTS[nextIdx] });
  }, [id, data.variant, updateNodeData]);

  const variant = data.variant || 'note';

  return (
    <>
      <NodeResizer
        minWidth={100}
        minHeight={30}
        isVisible={selected}
        lineClassName="react-flow__resize-control line"
        handleClassName="react-flow__resize-control handle"
      />
      <div className={`workspace-node label-node ${selected ? 'selected' : ''}`} style={{ position: 'relative' }}>
        <textarea
          className={`label-text variant-${variant} nodrag nowheel`}
          value={data.text || ''}
          onChange={onTextChange}
          placeholder="Label..."
        />
        <button
          className="label-variant-btn nodrag"
          onClick={cycleVariant}
          title={`Style: ${variant}`}
        >
          {variant}
        </button>
      </div>
    </>
  );
}

export default memo(LabelNode);
