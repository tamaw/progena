import { useEffect, useMemo, useRef, useState } from 'react';

const SLASH_COMMANDS = [
  {
    name: 'clear',
    title: 'Clear conversation',
    command: '/clear',
    syntax: '/clear',
    description: 'Clear the current chat and start a fresh web session.',
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

function ChatInput({ onSend, onStop, onCommand, isGenerating, disabled }) {
  const [message, setMessage] = useState('');
  const [activeSlashIndex, setActiveSlashIndex] = useState(0);
  const textareaRef = useRef(null);
  const slashQuery = getSlashQuery(message);
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
      onStop?.();
    }
  };

  const handleSend = () => {
    const trimmedMessage = message.trim();
    if (!trimmedMessage || isGenerating || disabled) {
      return;
    }

    const slashCommand = SLASH_COMMANDS.find((command) => command.command === trimmedMessage);
    if (slashCommand) {
      executeSlashCommand(slashCommand);
      return;
    }

    if (trimmedMessage) {
      onSend(trimmedMessage);
      setMessage('');
    }
  };

  return (
    <div className="chat-input-container">
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
            placeholder="Type your message or / for commands..."
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
            {isGenerating ? (
              <button className="btn-stop" title="Stop Generation (Esc)" onClick={onStop}>
                ⏹
              </button>
            ) : (
              <button
                className="btn-send"
                title="Send Message (Enter)"
                onClick={handleSend}
                disabled={!message.trim() || disabled}
              >
                <span className="send-icon">➤</span>
              </button>
            )}
          </div>
        </div>
      </div>
      <div className="input-hints">
        <span className="hint">Type <kbd>/</kbd> for commands</span>
        <span className="hint">Press <kbd>Enter</kbd> to send</span>
        <span className="hint">Press <kbd>Shift+Enter</kbd> for new line</span>
        <span className="hint">Press <kbd>Esc</kbd> to stop generation</span>
      </div>
    </div>
  );
}

export default ChatInput;
