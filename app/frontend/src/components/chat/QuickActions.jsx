function QuickActions({ onAction }) {
  const actions = [
    { message: 'What is your current status?', icon: 'status', label: 'Check Status' },
    { message: 'What tasks are in the backlog?', icon: 'backlog', label: 'View Backlog' },
    { message: 'Show me your capabilities', icon: 'capabilities', label: 'Capabilities' },
  ];

  return (
    <div className="quick-actions">
      {actions.map((action, index) => (
        <button
          key={index}
          className="quick-action"
          onClick={() => onAction(action.message)}
        >
          <QuickActionIcon type={action.icon} />
          {action.label}
        </button>
      ))}
    </div>
  );
}

function QuickActionIcon({ type }) {
  if (type === 'status') {
    return (
      <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
        <path d="M3 12V7m5 5V4m5 8V9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    );
  }

  if (type === 'backlog') {
    return (
      <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
        <path d="M4 4h8M4 8h8M4 12h5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="M8 2.5l1.4 3 3.1.5-2.25 2.25.55 3.25L8 10l-2.8 1.5.55-3.25L3.5 6l3.1-.5z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

export default QuickActions;
