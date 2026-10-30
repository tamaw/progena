export const CANVAS_PROTOCOL = Object.freeze({
  channel: 'progena-canvas',
  message: Object.freeze({
    MOUNTED: 'mounted',
    ERROR: 'error',
    REQUEST: 'request',
    REPLY: 'reply',
    EVENT: 'event',
    THEME: 'theme',
  }),
  method: Object.freeze({
    DOCUMENTS: 'documents',
    DATA: 'data',
    DOCUMENT_TYPES: 'documentTypes',
    SUBSCRIBE: 'subscribe',
  }),
});
