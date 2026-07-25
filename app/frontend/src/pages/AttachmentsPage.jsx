import { useCallback, useEffect, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import { useConfig } from '../context/ConfigContext';
import { subscribeUiEvent } from '../store/realtimeEvents';
import { selectChatSessions } from '../store/uiDataSlice';
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
  const chatSessions = useSelector(selectChatSessions);
  const [threadId, setThreadId] = useState('');
  const [attachments, setAttachments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const fileInputRef = useRef(null);
  const dragDepthRef = useRef(0);

  // Default to the most recently active chat once the (already app-wide
  // loaded, see RealtimeBridge.jsx) session list arrives; leaves the
  // selector on "no chat selected" if there are none yet.
  useEffect(() => {
    if (!threadId && chatSessions.length > 0) {
      setThreadId(chatSessions[0].thread_id);
    }
  }, [chatSessions, threadId]);

  // Attachments are scoped per conversation thread (see attachment_routes.py):
  // there is no "list everything" view, so nothing loads until a thread is picked.
  const load = useCallback(async () => {
    if (!threadId) {
      setAttachments([]);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const response = await fetch(`${getApiUrl('attachments')}?thread_id=${encodeURIComponent(threadId)}`);
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
  }, [getApiUrl, threadId]);

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
    if (files.length === 0 || !threadId) return;
    setUploading(true);
    setError('');
    try {
      await Promise.all(files.map(async (file) => {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('thread_id', threadId);
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
  }, [getApiUrl, load, threadId]);

  const handleDelete = useCallback(async (id) => {
    if (!threadId) return;
    setDeletingId(id);
    setError('');
    try {
      const response = await fetch(
        `${getApiUrl('attachments')}/${id}?thread_id=${encodeURIComponent(threadId)}`,
        { method: 'DELETE' },
      );
      if (!response.ok && response.status !== 404) {
        throw new Error(`Delete failed (${response.status})`);
      }
      setAttachments((prev) => prev.filter((attachment) => attachment.id !== id));
    } catch (err) {
      setError(err.message || 'Delete failed');
    } finally {
      setDeletingId(null);
    }
  }, [getApiUrl, threadId]);

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
            <select
              className="attachments-page__thread-select"
              value={threadId}
              onChange={(e) => setThreadId(e.target.value)}
              aria-label="Chat"
            >
              {chatSessions.length === 0 && <option value="">No chats yet</option>}
              {chatSessions.map((session) => (
                <option key={session.thread_id} value={session.thread_id}>
                  {session.title || 'Untitled chat'}
                </option>
              ))}
            </select>
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
              disabled={uploading || !threadId}
            >
              {uploading ? 'Uploading...' : 'Upload files'}
            </button>
          </div>
        </header>

        {isDraggingFiles && (
          <div className="attachments-page__drop-overlay" aria-hidden="true">Drop files to upload</div>
        )}

        {error && <div className="attachments-page__error">{error}</div>}

        {!threadId ? (
          <div className="attachments-page__empty">Select a chat above to see its attachments.</div>
        ) : loading ? (
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
                  downloadUrl={`${getApiUrl('attachments')}/${attachment.id}?thread_id=${encodeURIComponent(threadId)}`}
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
