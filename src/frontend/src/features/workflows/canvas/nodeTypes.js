import { PromptNode, DocumentEventNode, CronNode, ManualNode, WorkflowDataNode, WorkflowControlNode, UnpackNode } from './WorkflowNodes';

export const NODE_TYPES = {
  event: DocumentEventNode,
  cron: CronNode,
  manual: ManualNode,
  agent: PromptNode,
  extract: PromptNode,
  format: PromptNode,
  data: WorkflowDataNode,
  condition: WorkflowControlNode,
  approval: WorkflowControlNode,
  document_read: PromptNode,
  document_query: PromptNode,
  document_write: PromptNode,
  routine: PromptNode,
  document_create: PromptNode,
  document_delete: PromptNode,
  unpack: UnpackNode,
};
