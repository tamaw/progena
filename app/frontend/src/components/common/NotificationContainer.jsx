import { useNotifications } from '../../hooks/useNotifications';

function NotificationContainer() {
  const { notifications, dismiss } = useNotifications();

  return (
    <div className="notification-container">
      {notifications.map((notification) => (
        <div 
          key={notification.id} 
          className={`notification ${notification.type}`}
          onClick={() => dismiss(notification.id)}
        >
          <div className="notification-icon">
            {notification.type === 'success' && '✓'}
            {notification.type === 'error' && '✕'}
            {notification.type === 'warning' && '⚠'}
            {notification.type === 'info' && 'ℹ'}
          </div>
          <div className="notification-content">
            <div className="notification-title">{notification.title}</div>
            <div className="notification-message">{notification.message}</div>
          </div>
          <button className="notification-close" onClick={() => dismiss(notification.id)}>×</button>
        </div>
      ))}
    </div>
  );
}

export default NotificationContainer;
