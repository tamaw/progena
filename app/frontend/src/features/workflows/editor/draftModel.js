import { cloneValue } from '../models/values';

export function workflowPath(workflowId) {
  return workflowId ? `/workflows/${workflowId}.json` : '';
}

export function workflowEditorDraft(workflow) {
  if (!workflow) return null;
  const cloned = cloneValue(workflow);
  return {
    workflow: cloned,
    jsonDraft: `${JSON.stringify(cloned, null, 2)}\n`,
    jsonError: '',
    jsonDirty: false,
    activeSidePanel: null,
    nodeOptionsId: null,
    canvasTool: 'pointer',
    isDirty: false,
  };
}
