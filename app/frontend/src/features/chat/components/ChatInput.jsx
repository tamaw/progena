import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../../../context/ConfigContext';
import { fetchAgentProfiles } from '../../agents/state/agents';
import { selectAgentProfiles, selectAgentProfilesState } from '../../agents/state/agents';
import { selectRealtimeStreamReady } from '../../../realtime/realtimeSlice';
import { getAgentMentionContext } from '../models/chatInputModel';
import AttachmentFileIcon from './AttachmentFileIcon';
import { describeAttachmentFileType, formatAttachmentSize } from '../models/attachmentFileTypes';

const SLASH_COMMANDS = [
  {
    name: 'new',
    title: 'New conversation',
    command: '/new',
    syntax: '/new',
    description: 'Start a fresh chat session.',
    requiresArguments: false,
  },
];

const DIRECT_AGENT_NAME_RE = /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/;

function getSlashQuery(value) {
  const trimmedValue = value.trimStart();
  if (!trimmedValue.startsWith('/')) {
    return null;
  }

  if (/\s/.test(trimmedValue.slice(1))) {
    return null;
  }

  const firstToken = trimmedValue.split(/\s+/, 1)[0] ?? '';
  if (!firstToken.startsWith('/')) {
    return null;
  }

  return firstToken.slice(1).toLowerCase();
}

const ChatInput = forwardRef(function ChatInput({
  onSend,
  onStop,
  onCommand,
  isGenerating,
  disabled,
  placeholder = 'What should we work on next?',
  enableSlashCommands = true,
  allowStop = true,
  autoFocus = false,
  focusToken = null,
  keepFocusAfterSend = false,
  initialMessage = '',
  className = '',
  enableAttachments = true,
}, ref) {
  const [message, setMessage] = useState(initialMessage);
  const [activeSlashIndex, setActiveSlashIndex] = useState(0);
  const [activeMentionIndex, setActiveMentionIndex] = useState(0);
  const [selectionStart, setSelectionStart] = useState(initialMessage.length);
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const dragDepthRef = useRef(0);
  const uploadControllersRef = useRef(new Map());
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const agentProfiles = useSelector(selectAgentProfiles);
  const agentProfilesState = useSelector(selectAgentProfilesState);
  const streamReady = useSelector(selectRealtimeStreamReady);
  const agentProfilesRequestUrlRef = useRef(null);
  const slashQuery = enableSlashCommands ? getSlashQuery(message) : null;
  const mentionContext = getAgentMentionContext(message, selectionStart);
  const filteredSlashCommands = useMemo(() => {
    if (slashQuery === null) {
      return [];
    }

    return SLASH_COMMANDS.filter((command) => (
      command.name.includes(slashQuery)
      || command.command.includes(slashQuery)
      || command.title.toLowerCase().includes(slashQuery)
    ));
  }, [slashQuery]);
  const mentionCandidates = useMemo(() => {
    if (!mentionContext) {
      return [];
    }
    const names = agentProfiles
      .filter((agent) => (
        agent?.kind === 'a2a'
        && typeof agent?.url === 'string'
        && agent.url.trim()
        && typeof agent?.name === 'string'
        && DIRECT_AGENT_NAME_RE.test(agent.name)
      ))
      .map((agent) => (typeof agent?.name === 'string' ? agent.name : ''))
      .filter(Boolean);
    return names.filter((name) => name.toLowerCase().includes(mentionContext.query));
  }, [agentProfiles, mentionContext]);
  const isSlashMenuOpen = !disabled && !isGenerating && filteredSlashCommands.length > 0;
  const isMentionMenuOpen = !disabled && !isGenerating && mentionCandidates.length > 0;

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + 'px';
    }
  }, [message]);

  useEffect(() => {
    if (autoFocus && !disabled && !isGenerating) {
      requestAnimationFrame(() => textareaRef.current?.focus());
    }
  }, [autoFocus, disabled, focusToken, isGenerating]);

  // Request the roster once per stream session (the snapshot arrives on the
  // stream). A reconnect starts a new session, so a failed request is retried
  // then rather than suppressing @mentions until reload.
  useEffect(() => {
    if (!streamReady) {
      agentProfilesRequestUrlRef.current = null;
      return;
    }
    const agentsUrl = getApiUrl('agentProfiles');
    if (
      agentProfilesState.loaded
      || agentProfilesState.loading
      || agentProfilesRequestUrlRef.current === agentsUrl
    ) {
      return;
    }
    agentProfilesRequestUrlRef.current = agentsUrl;
    dispatch(fetchAgentProfiles({ agentsUrl }));
  }, [
    agentProfilesState.loaded,
    agentProfilesState.loading,
    dispatch,
    getApiUrl,
    streamReady,
  ]);

  const executeSlashCommand = (command) => {
    if (command.requiresArguments) {
      setMessage(command.syntax);
      requestAnimationFrame(() => textareaRef.current?.focus());
      return;
    }

    setMessage('');
    onCommand?.(command.name);
  };

  const applyMention = (agentName) => {
    if (!mentionContext) {
      return;
    }
    const nextMessage = `${message.slice(0, mentionContext.tokenStart)}@${agentName} ${message.slice(mentionContext.tokenEnd)}`;
    setMessage(nextMessage);
    setActiveMentionIndex(0);
    requestAnimationFrame(() => {
      if (textareaRef.current) {
        const cursor = mentionContext.tokenStart + agentName.length + 2;
        textareaRef.current.focus();
        textareaRef.current.setSelectionRange(cursor, cursor);
        setSelectionStart(cursor);
      }
    });
  };

  const handleKeyDown = (e) => {
    if (isMentionMenuOpen && e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveMentionIndex((prev) => (prev + 1) % mentionCandidates.length);
      return;
    }

    if (isMentionMenuOpen && e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveMentionIndex((prev) => (prev - 1 + mentionCandidates.length) % mentionCandidates.length);
      return;
    }

    if (isMentionMenuOpen && (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey))) {
      e.preventDefault();
      applyMention(mentionCandidates[activeMentionIndex] ?? mentionCandidates[0]);
      return;
    }

    if (isSlashMenuOpen && e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveSlashIndex((prev) => (prev + 1) % filteredSlashCommands.length);
      return;
    }

    if (isSlashMenuOpen && e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveSlashIndex((prev) => (
        (prev - 1 + filteredSlashCommands.length) % filteredSlashCommands.length
      ));
      return;
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (isSlashMenuOpen) {
        executeSlashCommand(filteredSlashCommands[activeSlashIndex] ?? filteredSlashCommands[0]);
        return;
      }
      handleSend();
    } else if (e.key === 'Escape' && isMentionMenuOpen) {
      e.preventDefault();
      setActiveMentionIndex(0);
    } else if (e.key === 'Escape' && isGenerating) {
      if (allowStop) {
        onStop?.();
      }
    }
  };

  const hasUploadingAttachments = pendingAttachments.some((attachment) => attachment.uploading);

  const removeAttachment = (localId) => {
    uploadControllersRef.current.get(localId)?.abort();
    uploadControllersRef.current.delete(localId);
    setPendingAttachments((prev) => prev.filter((attachment) => attachment.localId !== localId));
  };

  const handleFilesSelected = useCallback(async (fileList) => {
    const files = Array.from(fileList || []);
    if (files.length === 0) {
      return;
    }
    const staged = files.map((file) => ({
      localId: `pending_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      name: file.name,
      size_bytes: file.size,
      // Stamped from the File object so the chip's mime-type icon shows
      // immediately, before the server round-trip resolves the
      // authoritative mime_type below.
      mime_type: file.type || '',
      uploading: true,
      error: null,
      id: null,
    }));
    setPendingAttachments((prev) => [...prev, ...staged]);

    await Promise.all(staged.map(async (entry, index) => {
      const file = files[index];
      const controller = new AbortController();
      uploadControllersRef.current.set(entry.localId, controller);
      const formData = new FormData();
      formData.append('file', file);
      try {
        const response = await fetch(getApiUrl('attachments'), { method: 'POST', body: formData, signal: controller.signal });
        const payload = await response.json().catch(() => null);
        if (!response.ok || !payload) {
          throw new Error(payload?.detail || `Upload failed (${response.status})`);
        }
        setPendingAttachments((prev) => prev.map((attachment) => (
          attachment.localId === entry.localId
            ? {
              ...attachment,
              uploading: false,
              id: payload.id,
              name: payload.original_filename || attachment.name,
              mime_type: payload.mime_type,
              size_bytes: payload.size_bytes,
            }
            : attachment
        )));
      } catch (error) {
        // Cancelled via removeAttachment -- the chip is already gone from
        // pendingAttachments, so there's nothing left to mark as errored.
        if (error.name !== 'AbortError') {
          setPendingAttachments((prev) => prev.map((attachment) => (
            attachment.localId === entry.localId
              ? { ...attachment, uploading: false, error: error.message || 'Upload failed' }
              : attachment
          )));
        }
      } finally {
        uploadControllersRef.current.delete(entry.localId);
      }
    }));
  }, [getApiUrl]);

  const handlePaste = (e) => {
    if (!enableAttachments || disabled || isGenerating) return;
    const items = Array.from(e.clipboardData?.items || []);
    const files = items
      .filter((item) => item.kind === 'file')
      .map((item) => item.getAsFile())
      .filter(Boolean);
    if (files.length === 0) {
      return;
    }
    // Only intercept when the clipboard actually carries file data --
    // plain text paste (the overwhelming common case) must fall through
    // to the textarea's default paste behavior untouched.
    e.preventDefault();
    handleFilesSelected(files);
  };

  // Lets an ancestor (e.g. a page-wide drop zone) forward files into this
  // composer's upload pipeline without duplicating the staging/AbortController
  // logic above.
  useImperativeHandle(ref, () => ({
    addFiles: handleFilesSelected,
  }), [handleFilesSelected]);

  const handleDragEnter = (e) => {
    if (!enableAttachments || disabled || isGenerating) return;
    if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
    e.preventDefault();
    // Stop here so a page-level drop zone wrapping the whole chat window
    // doesn't also see this drag and show its own overlay underneath --
    // dragging directly over the composer should only ever show this
    // local one.
    e.stopPropagation();
    dragDepthRef.current += 1;
    setIsDraggingFiles(true);
  };

  const handleDragOver = (e) => {
    if (!enableAttachments || disabled || isGenerating) return;
    if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDragLeave = (e) => {
    if (!enableAttachments) return;
    e.preventDefault();
    e.stopPropagation();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) {
      setIsDraggingFiles(false);
    }
  };

  const handleDrop = (e) => {
    if (!enableAttachments || disabled || isGenerating) return;
    e.preventDefault();
    e.stopPropagation();
    dragDepthRef.current = 0;
    setIsDraggingFiles(false);
    handleFilesSelected(e.dataTransfer?.files);
  };

  const handleSend = () => {
    const trimmedMessage = message.trim();
    if ((!trimmedMessage && pendingAttachments.length === 0) || isGenerating || disabled || hasUploadingAttachments) {
      return;
    }

    const slashCommand = enableSlashCommands
      ? SLASH_COMMANDS.find((command) => command.command === trimmedMessage)
      : null;
    if (slashCommand) {
      executeSlashCommand(slashCommand);
      return;
    }

    const attachments = pendingAttachments.filter((attachment) => attachment.id && !attachment.error);
    if (trimmedMessage || attachments.length > 0) {
      onSend(trimmedMessage, attachments);
      setMessage('');
      setPendingAttachments([]);
      if (keepFocusAfterSend) {
        requestAnimationFrame(() => textareaRef.current?.focus());
      }
    }
  };

  return (
    <div className={`chat-input-container ${className}`.trim()}>
      <div className="chat-input-stack">
        {isMentionMenuOpen && (
          <div className="slash-command-menu" role="listbox" aria-label="Agent mentions">
            {mentionCandidates.map((agentName, index) => (
              <button
                key={agentName}
                type="button"
                className={`slash-command-item${index === activeMentionIndex ? ' active' : ''}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  setActiveMentionIndex(index);
                  applyMention(agentName);
                }}
              >
                <div className="slash-command-item-header">
                  <span className="slash-command-item-title">{agentName}</span>
                  <code className="slash-command-item-syntax">@{agentName}</code>
                </div>
                <span className="slash-command-item-description">Direct-call this registered agent.</span>
              </button>
            ))}
          </div>
        )}
        {isSlashMenuOpen && (
          <div className="slash-command-menu" role="listbox" aria-label="Slash commands">
            {filteredSlashCommands.map((command, index) => (
              <button
                key={command.name}
                type="button"
                className={`slash-command-item${index === activeSlashIndex ? ' active' : ''}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  setActiveSlashIndex(index);
                  executeSlashCommand(command);
                }}
              >
                <div className="slash-command-item-header">
                  <span className="slash-command-item-title">{command.title}</span>
                  <code className="slash-command-item-syntax">{command.syntax}</code>
                </div>
                <span className="slash-command-item-description">{command.description}</span>
              </button>
            ))}
          </div>
        )}
        {enableAttachments && pendingAttachments.length > 0 && (
          <div className="chat-input-attachments">
            {pendingAttachments.map((attachment) => {
              const sizeLabel = formatAttachmentSize(attachment.size_bytes);
              const { label: fileTypeLabel } = describeAttachmentFileType(attachment.mime_type, attachment.name);
              const metaLabel = attachment.error
                ? attachment.error
                : (attachment.uploading ? 'Uploading…' : [fileTypeLabel, sizeLabel].filter(Boolean).join(' · '));
              const tooltip = attachment.error
                ? `${attachment.name} — ${attachment.error}`
                : [attachment.name, sizeLabel].filter(Boolean).join(' — ');
              return (
                <span
                  key={attachment.localId}
                  className={`chat-input-attachment-chip${attachment.error ? ' chat-input-attachment-chip--error' : ''}${attachment.uploading ? ' chat-input-attachment-chip--uploading' : ''}`}
                  title={tooltip}
                >
                  <AttachmentFileIcon mimeType={attachment.mime_type} filename={attachment.name} size={26} />
                  <span className="chat-input-attachment-chip__body">
                    <span className="chat-input-attachment-chip__name">{attachment.name}</span>
                    <span className="chat-input-attachment-chip__meta">{metaLabel}</span>
                  </span>
                  <button
                    type="button"
                    className="chat-input-attachment-chip__remove"
                    aria-label={attachment.uploading ? `Cancel upload of ${attachment.name}` : `Remove ${attachment.name}`}
                    onClick={() => removeAttachment(attachment.localId)}
                  >
                    ×
                  </button>
                  {!attachment.error && (
                    <span
                      className={`chat-input-attachment-chip__progress${attachment.uploading ? ' chat-input-attachment-chip__progress--active' : ' chat-input-attachment-chip__progress--complete'}`}
                      aria-hidden="true"
                    />
                  )}
                </span>
              );
            })}
          </div>
        )}
        <div
          className={`input-wrapper${isDraggingFiles ? ' input-wrapper--drag-active' : ''}`}
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {isDraggingFiles && (
            <div className="chat-input-drop-overlay" aria-hidden="true">Drop files to attach</div>
          )}
          <textarea
            ref={textareaRef}
            className="chat-input"
            placeholder={disabled || isGenerating ? '' : placeholder}
            value={message}
            onChange={(e) => {
              setMessage(e.target.value);
              setActiveSlashIndex(0);
              setActiveMentionIndex(0);
              setSelectionStart(e.target.selectionStart ?? e.target.value.length);
            }}
            onClick={(e) => setSelectionStart(e.currentTarget.selectionStart ?? message.length)}
            onKeyUp={(e) => setSelectionStart(e.currentTarget.selectionStart ?? message.length)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            rows={1}
            disabled={disabled || isGenerating}
          />
          <div className="input-toolbar">
            <div className="input-toolbar__left">
              {enableAttachments ? (
                <>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    className="chat-input-file-picker"
                    onChange={(e) => {
                      handleFilesSelected(e.target.files);
                      e.target.value = '';
                    }}
                  />
                  <button
                    type="button"
                    className="btn-icon"
                    title="Attach file"
                    aria-label="Attach file"
                    disabled={disabled || isGenerating}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                    </svg>
                  </button>
                </>
              ) : (
                <button type="button" className="btn-icon" title="Attach file (unavailable here)" aria-label="Attach file" disabled>
                  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                  </svg>
                </button>
              )}
              <div className="chat-info-control" tabIndex={0} aria-label="Keyboard shortcuts">
                <span className="btn-icon chat-info-btn" aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="16" x2="12" y2="12" />
                    <line x1="12" y1="8" x2="12.01" y2="8" />
                  </svg>
                </span>
                <div className="chat-info-tooltip" role="tooltip">
                  <div className="chat-info-tooltip__row">Type <kbd>/</kbd> for commands</div>
                  <div className="chat-info-tooltip__row">Type <kbd>@</kbd> for agents</div>
                  <div className="chat-info-tooltip__row">Press <kbd>Enter</kbd> to send</div>
                  <div className="chat-info-tooltip__row">Press <kbd>Shift+Enter</kbd> for new line</div>
                  <div className="chat-info-tooltip__row">Press <kbd>Esc</kbd> to stop generation</div>
                </div>
              </div>
            </div>
            <div className="input-toolbar__right">
              <button type="button" className="btn-icon" title="Voice input (coming soon)" aria-label="Voice input" disabled>
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <line x1="12" y1="19" x2="12" y2="23" />
                  <line x1="8" y1="23" x2="16" y2="23" />
                </svg>
              </button>
              {isGenerating && allowStop ? (
                <button type="button" className="btn-send btn-send--stop" aria-label="Stop generation" onClick={onStop}>
                  <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                    <rect x="4" y="4" width="8" height="8" rx="1.5" fill="currentColor" />
                  </svg>
                </button>
              ) : (
                <button
                  type="button"
                  className="btn-send"
                  aria-label="Send message"
                  onClick={handleSend}
                  disabled={(!message.trim() && pendingAttachments.length === 0) || disabled || isGenerating || hasUploadingAttachments}
                >
                  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <line x1="12" y1="19" x2="12" y2="5" />
                    <polyline points="5 12 12 5 19 12" />
                  </svg>
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
});

export default ChatInput;
