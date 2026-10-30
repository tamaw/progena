import vocabulary from '../../../shared/ui_event_vocabulary.json';

export const UI_SSE_EVENT = vocabulary.sse_events.UI_EVENT;
export const UI_EVENT = Object.freeze(vocabulary.ui_events);
export const CHAT_CONTENT_EVENT = Object.freeze(vocabulary.chat_content_events);
export const ROUTINE_RUN_STATUS = Object.freeze(vocabulary.routine_run_statuses);

export function routineRunStatusEvent(status) {
  return vocabulary.templates.ROUTINE_RUN_STATUS.replace('{status}', status);
}
