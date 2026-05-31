import { useEffect, useMemo, useRef, useState } from 'react';

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
  const textareaRef = useRef(null);
  const slashQuery = enableSlashCommands ? getSlashQuery(message) : null;
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
  const isSlashMenuOpen = !disabled && !isGenerating && filteredSlashCommands.length > 0;

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + 'px';
    }
  }, [message]);

  useEffect(() => {
    if (autoFocus && !disabled) {
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

  const handleKeyDown = (e) => {
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
            placeholder={placeholder}
            value={message}
            onChange={(e) => {
              setMessage(e.target.value);
              setActiveSlashIndex(0);
            }}
            onKeyDown={handleKeyDown}
            rows={1}
            disabled={disabled}
          />
          <div className="input-actions">
            {isGenerating && allowStop ? (
              <button className="btn-stop" title="Stop Generation (Esc)" onClick={onStop}>
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
          <span className="hint">Press <kbd>Enter</kbd> to send</span>
          <span className="hint">Press <kbd>Shift+Enter</kbd> for new line</span>
          <span className="hint">Press <kbd>Esc</kbd> to stop generation</span>
        </div>
      ) : null}
    </div>
  );
}

export default ChatInput;
