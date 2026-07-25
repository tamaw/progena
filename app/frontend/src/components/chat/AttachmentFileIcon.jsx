import { describeAttachmentFileType } from './attachmentFileTypes';

// A document-shaped icon with a colored, mime-type-derived badge (PDF / DOC /
// XLS / PPT / IMG / ZIP / CODE / TXT / FILE) -- the file-attachment analog of
// how images get their own thumbnail in MessageImageStrip. Pure/presentational:
// callers resolve mime_type + filename via describeAttachmentFileType.
function AttachmentFileIcon({ mimeType, filename, size = 22 }) {
  const { label, color } = describeAttachmentFileType(mimeType, filename);
  return (
    <svg
      className="attachment-file-icon"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
    >
      <path
        d="M6 2h8l6 6v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"
        fill={color}
        fillOpacity="0.16"
        stroke={color}
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M14 2v5a1 1 0 0 0 1 1h5" fill="none" stroke={color} strokeWidth="1.4" strokeLinejoin="round" />
      <text
        x="12"
        y="17.5"
        textAnchor="middle"
        fontSize={label.length > 3 ? '5.5' : '6.5'}
        fontWeight="700"
        fill={color}
        fontFamily="var(--font-family-mono, monospace)"
      >
        {label}
      </text>
    </svg>
  );
}

export default AttachmentFileIcon;
