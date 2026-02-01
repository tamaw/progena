function StatusIndicator({ status }) {
  const getStatusText = () => {
    switch (status) {
      case 'connected': return 'Connected';
      case 'connecting': return 'Connecting...';
      case 'error': return 'Error';
      default: return 'Disconnected';
    }
  };

  return (
    <span className="status-indicator" data-status={status}>
      <span className="status-dot"></span>
      <span className="status-text">{getStatusText()}</span>
    </span>
  );
}

export default StatusIndicator;
