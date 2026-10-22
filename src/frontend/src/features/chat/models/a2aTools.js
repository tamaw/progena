export const A2A_PRIMARY_TOOL_NAME = 'a2a_send';

export const A2A_TOOL_NAMES = new Set([
  'a2a_send',
  'a2a_send_async',
  'a2a_status',
  'a2a_cancel',
  'a2a_list',
]);

export function isA2AToolName(name) {
  return typeof name === 'string' && A2A_TOOL_NAMES.has(name);
}
