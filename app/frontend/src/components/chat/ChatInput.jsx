import { useEffect, useMemo, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import { selectAgentProfiles } from '../../store/uiData/agents';

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

export function getAgentMentionContext(value, cursor) {
  const text = typeof value === 'string' ? value : '';
  const position = Number.isFinite(cursor) ? cursor : text.length;
  const uptoCursor = text.slice(0, position);
  const match = uptoCursor.match(/(^|\s)@([A-Za-z0-9_.-]*)$/);
  if (!match) {
    return null;
  }
  const tokenStart = uptoCursor.length - match[0].length + match[1].length;
  return {
    query: match[2].toLowerCase(),
    tokenStart,
    tokenEnd: position,
  };
}

function ChatInput({
  onSend,
  onStop,
  onCommand,
  isGenerating,
  disabled,
  placeholder = 'Type your message here',
  showHints = true,
  enableSlashCommands = true,
  allowStop = true,
  autoFocus = false,
  focusToken = null,
  keepFocusAfterSend = false,
  initialMessage = '',
  className = '',
}) {
  const [message, setMessage] = useState(initialMessage);
  const [activeSlashIndex, setActiveSlashIndex] = useState(0);
  const [activeMentionIndex, setActiveMentionIndex] = useState(0);
  const [selectionStart, setSelectionStart] = useState(initialMessage.length);
  const textareaRef = useRef(null);
  const agentProfiles = useSelector(selectAgentProfiles);
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

  const handleSend = () => {
    const trimmedMessage = message.trim();
    if (!trimmedMessage || isGenerating || disabled) {
      return;
    }

    const slashCommand = enableSlashCommands
      ? SLASH_COMMANDS.find((command) => command.command === trimmedMessage)
      : null;
    if (slashCommand) {
      executeSlashCommand(slashCommand);
      return;
    }

    if (trimmedMessage) {
      onSend(trimmedMessage);
      setMessage('');
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
        <div className="input-wrapper">
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
            rows={1}
            disabled={disabled || isGenerating}
          />
          <div className="input-actions">
            {isGenerating && allowStop ? (
              <button type="button" className="btn-stop" title="Stop Generation (Esc)" onClick={onStop}>
                <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                  <rect x="4" y="4" width="8" height="8" rx="1.5" fill="currentColor" />
                </svg>
              </button>
            ) : (
              <button
                className="btn-send"
                title="Send Message (Enter)"
                onClick={handleSend}
                disabled={!message.trim() || disabled || isGenerating}
              >
                {isGenerating ? (
                  <span aria-hidden="true">...</span>
                ) : (
                  <svg className="send-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                    <path d="M2.5 8L13.5 3l-2 5 2 5z" fill="currentColor" />
                  </svg>
                )}
              </button>
            )}
          </div>
        </div>
      </div>
      {showHints ? (
        <div className="input-hints">
          <span className="hint">Type <kbd>/</kbd> for commands</span>
          <span className="hint">Type <kbd>@</kbd> for agents</span>
          <span className="hint">Press <kbd>Enter</kbd> to send</span>
          <span className="hint">Press <kbd>Shift+Enter</kbd> for new line</span>
          <span className="hint">Press <kbd>Esc</kbd> to stop generation</span>
        </div>
      ) : null}
    </div>
  );
}

export default ChatInput;
