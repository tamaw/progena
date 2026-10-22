export function canvasToolSettings(tool) {
  return {
    panOnDrag: tool === 'grab' ? [0, 2] : [2],
    selectionOnDrag: tool === 'pointer',
    nodesDraggable: tool === 'pointer',
    nodesConnectable: tool === 'pointer',
  };
}
