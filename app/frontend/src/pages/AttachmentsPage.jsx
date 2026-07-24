import { useCallback, useEffect, useRef, useState } from 'react';
import { useConfig } from '../context/ConfigContext';
import { subscribeUiEvent } from '../store/realtimeEvents';
import PageShell from '../components/layout/PageShell';

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTimestamp(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString();
}

function AttachmentRow({ attachment, downloadUrl, onDelete, deleting }) {
  return (
    <tr className="attachments-row">
      <td className="attachments-row__name">
        <a href={downloadUrl} target="_blank" rel="noreferrer">{attachment.name}</a>
        {attachment.error ? (
          <span className="attachments-row__error" title={attachment.error}>extraction failed</span>
        ) : null}
      </td>
      <td>{attachment.mime_type}</td>
      <td>{formatBytes(attachment.size_bytes)}</td>
      <td>{attachment.thread_id || <span className="attachments-row__muted">unattached</span>}</td>
      <td>{formatTimestamp(attachment.created_at)}</td>
      <td>{attachment.has_text ? 'yes' : 'no'}</td>
      <td>
        <button
          type="button"
          className="attachments-row__delete"
          onClick={() => onDelete(attachment.id)}
          disabled={deleting}
          aria-label={`Delete ${attachment.name}`}
        >
          Delete
        </button>
      </td>
    </tr>
  );
}

export default function AttachmentsPage() {
  const { getApiUrl } = useConfig();
  const [attachments, setAttachments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const fileInputRef = useRef(null);
  const dragDepthRef = useRef(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch(getApiUrl('attachments'));
      if (!response.ok) {
        throw new Error(`Failed to load attachments (${response.status})`);
      }
      const data = await response.json();
      setAttachments(Array.isArray(data.attachments) ? data.attachments : []);
    } catch (err) {
      setError(err.message || 'Failed to load attachments');
    } finally {
      setLoading(false);
    }
  }, [getApiUrl]);

  useEffect(() => {
    void load();
  }, [load]);

  // Uploads (from this page or another tab/the chat composer) and deletes
  // both broadcast on the canonical /api/ui/stream -- refresh the list
  // live instead of polling.
  useEffect(() => subscribeUiEvent((event) => {
    if (event?.type === 'attachment.uploaded' || event?.type === 'attachment.deleted') {
      void load();
    }
  }), [load]);

  const uploadFiles = useCallback(async (fileList) => {
    const files = Array.from(fileList || []);
    if (files.length === 0) return;
    setUploading(true);
    setError('');
    try {
      await Promise.all(files.map(async (file) => {
        const formData = new FormData();
        formData.append('file', file);
        const response = await fetch(getApiUrl('attachments'), { method: 'POST', body: formData });
        if (!response.ok) {
          const payload = await response.json().catch(() => null);
          throw new Error(payload?.detail || `Upload of ${file.name} failed (${response.status})`);
        }
      }));
      await load();
    } catch (err) {
      setError(err.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
  }, [getApiUrl, load]);

  const handleDelete = useCallback(async (id) => {
    setDeletingId(id);
    setError('');
    try {
      const response = await fetch(`${getApiUrl('attachments')}/${id}`, { method: 'DELETE' });
      if (!response.ok && response.status !== 404) {
        throw new Error(`Delete failed (${response.status})`);
      }
      setAttachments((prev) => prev.filter((attachment) => attachment.id !== id));
    } catch (err) {
      setError(err.message || 'Delete failed');
    } finally {
      setDeletingId(null);
    }
  }, [getApiUrl]);

  const handleDragEnter = (e) => {
    if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
    e.preventDefault();
    dragDepthRef.current += 1;
    setIsDraggingFiles(true);
  };

  const handleDragOver = (e) => {
    if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
    e.preventDefault();
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDraggingFiles(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    dragDepthRef.current = 0;
    setIsDraggingFiles(false);
    void uploadFiles(e.dataTransfer?.files);
  };

  return (
    <PageShell className="attachments-page" label="Attachments">
      <div
        className={`attachments-page__body${isDraggingFiles ? ' is-drag-active' : ''}`}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <header className="attachments-page__head">
          <div>
            <h1>Attachments</h1>
            <p className="attachments-page__sub">
              Files uploaded from chat, stored as blobs in Postgres. Progenitor reads
              them directly at <code>/attachments/&lt;filename&gt;</code>.
            </p>
          </div>
          <div className="attachments-page__actions">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="attachments-page__file-picker"
              onChange={(e) => {
                void uploadFiles(e.target.files);
                e.target.value = '';
              }}
            />
            <button
              type="button"
              className="attachments-page__upload-btn"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
            >
              {uploading ? 'Uploading...' : 'Upload files'}
            </button>
          </div>
        </header>

        {isDraggingFiles && (
          <div className="attachments-page__drop-overlay" aria-hidden="true">Drop files to upload</div>
        )}

        {error && <div className="attachments-page__error">{error}</div>}

        {loading ? (
          <div className="attachments-page__empty">Loading...</div>
        ) : attachments.length === 0 ? (
          <div className="attachments-page__empty">
            No attachments yet. Drag files here, use Upload files, or attach a file from chat.
          </div>
        ) : (
          <table className="attachments-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Size</th>
                <th>Chat</th>
                <th>Uploaded</th>
                <th>Readable</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {attachments.map((attachment) => (
                <AttachmentRow
                  key={attachment.id}
                  attachment={attachment}
                  downloadUrl={`${getApiUrl('attachments')}/${attachment.id}`}
                  onDelete={handleDelete}
                  deleting={deletingId === attachment.id}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </PageShell>
  );
}
