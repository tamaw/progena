function QuickActions({ onAction }) {
  const actions = [
    { message: "What is your current status?", icon: "📊", label: "Check Status" },
    { message: "What tasks are in the backlog?", icon: "📋", label: "View Backlog" },
    { message: "Show me your capabilities", icon: "🎯", label: "Capabilities" }
  ];

  return (
    <div className="quick-actions">
      {actions.map((action, index) => (
        <button
          key={index}
          className="quick-action"
          onClick={() => onAction(action.message)}
        >
          {action.icon} {action.label}
        </button>
      ))}
    </div>
  );
}

export default QuickActions;
