// Maps an attachment's mime type / filename to a short badge label + accent
// color for AttachmentFileIcon, and formats byte counts for display. Kept as
// pure data/logic (no JSX) so it's trivially unit-testable and reusable
// anywhere an attachment chip renders (chat history, composer preview, the
// /attachments browser page).

const EXTENSION_BY_LABEL = {
  PDF: ['pdf'],
  DOC: ['doc', 'docx', 'rtf', 'odt'],
  XLS: ['xls', 'xlsx', 'csv', 'ods'],
  PPT: ['ppt', 'pptx', 'odp'],
  IMG: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'heic'],
  ZIP: ['zip', 'tar', 'gz', 'tgz', 'rar', '7z'],
  CODE: ['js', 'jsx', 'ts', 'tsx', 'py', 'rb', 'go', 'rs', 'java', 'c', 'cpp', 'h', 'json', 'yaml', 'yml', 'html', 'css', 'sh'],
  TXT: ['txt', 'md', 'log'],
};

const MIME_PREFIX_LABEL = [
  [/^application\/pdf$/, 'PDF'],
  [/^application\/vnd\.openxmlformats-officedocument\.wordprocessingml/, 'DOC'],
  [/^application\/msword$/, 'DOC'],
  [/^application\/vnd\.openxmlformats-officedocument\.spreadsheetml/, 'XLS'],
  [/^application\/vnd\.ms-excel$/, 'XLS'],
  [/^text\/csv$/, 'XLS'],
  [/^application\/vnd\.openxmlformats-officedocument\.presentationml/, 'PPT'],
  [/^application\/vnd\.ms-powerpoint$/, 'PPT'],
  [/^image\//, 'IMG'],
  [/^application\/(zip|x-tar|gzip|x-gzip|x-7z-compressed|x-rar-compressed)$/, 'ZIP'],
  [/^application\/json$/, 'CODE'],
  [/^text\/(x-python|javascript|x-java|html|css)$/, 'CODE'],
  [/^text\//, 'TXT'],
];

// Accent colors intentionally distinct from the app's semantic status colors
// (success/error/warning) so a file chip never reads as a status indicator.
const COLOR_BY_LABEL = {
  PDF: '#e35d5d',
  DOC: '#3b82f6',
  XLS: '#22a06b',
  PPT: '#e8792f',
  IMG: '#a855f7',
  ZIP: '#8a8f98',
  CODE: '#0ea5b3',
  TXT: '#6b7280',
  FILE: '#6b7280',
};

function extensionOf(filename) {
  const match = /\.([a-z0-9]+)$/i.exec(filename || '');
  return match ? match[1].toLowerCase() : '';
}

/** Returns `{ label, color }` describing the file-type badge for an attachment. */
export function describeAttachmentFileType(mimeType, filename) {
  const normalizedMime = (mimeType || '').toLowerCase().split(';')[0].trim();
  for (const [pattern, label] of MIME_PREFIX_LABEL) {
    if (pattern.test(normalizedMime)) {
      return { label, color: COLOR_BY_LABEL[label] };
    }
  }
  const ext = extensionOf(filename);
  for (const [label, extensions] of Object.entries(EXTENSION_BY_LABEL)) {
    if (extensions.includes(ext)) {
      return { label, color: COLOR_BY_LABEL[label] };
    }
  }
  return { label: 'FILE', color: COLOR_BY_LABEL.FILE };
}

/** Formats a byte count as a compact human-readable size, e.g. `287.4 KB`. */
export function formatAttachmentSize(sizeBytes) {
  if (!Number.isFinite(sizeBytes) || sizeBytes < 0) return '';
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = sizeBytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const precision = value < 10 ? 1 : 0;
  return `${value.toFixed(precision)} ${units[unitIndex]}`;
}
