import { describe, it, expect } from 'vitest';
import { describeAttachmentFileType, formatAttachmentSize } from './attachmentFileTypes';

describe('describeAttachmentFileType', () => {
  it('labels common office/document mime types', () => {
    expect(describeAttachmentFileType('application/pdf', 'report.pdf').label).toBe('PDF');
    expect(
      describeAttachmentFileType(
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'notes.docx',
      ).label,
    ).toBe('DOC');
    expect(
      describeAttachmentFileType(
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'data.xlsx',
      ).label,
    ).toBe('XLS');
    expect(describeAttachmentFileType('image/png', 'photo.png').label).toBe('IMG');
  });

  it('falls back to the filename extension when the mime type is generic', () => {
    expect(describeAttachmentFileType('application/octet-stream', 'archive.zip').label).toBe('ZIP');
    expect(describeAttachmentFileType('application/octet-stream', 'script.py').label).toBe('CODE');
  });

  it('falls back to FILE when nothing matches', () => {
    expect(describeAttachmentFileType('application/octet-stream', 'mystery').label).toBe('FILE');
    expect(describeAttachmentFileType('', '').label).toBe('FILE');
  });

  it('returns a distinct color per label', () => {
    const pdf = describeAttachmentFileType('application/pdf', 'x.pdf');
    const doc = describeAttachmentFileType(
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'x.docx',
    );
    expect(pdf.color).not.toBe(doc.color);
  });
});

describe('formatAttachmentSize', () => {
  it('formats bytes below 1024 verbatim', () => {
    expect(formatAttachmentSize(512)).toBe('512 B');
  });

  it('formats kilobytes with one decimal below 10, none at or above', () => {
    expect(formatAttachmentSize(294275)).toBe('287 KB');
    expect(formatAttachmentSize(12 * 1024)).toBe('12 KB');
  });

  it('formats megabytes and gigabytes', () => {
    expect(formatAttachmentSize(5 * 1024 * 1024)).toBe('5.0 MB');
    expect(formatAttachmentSize(2.5 * 1024 * 1024 * 1024)).toBe('2.5 GB');
  });

  it('returns an empty string for invalid input', () => {
    expect(formatAttachmentSize(undefined)).toBe('');
    expect(formatAttachmentSize(-1)).toBe('');
    expect(formatAttachmentSize(NaN)).toBe('');
  });
});
