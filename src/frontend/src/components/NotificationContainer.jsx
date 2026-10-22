import { useNotifications } from '../context/NotificationContext';
import styles from './NotificationContainer.module.css';

function NotificationContainer() {
  const { notifications, dismiss } = useNotifications();

  return (
    <div className={styles['notification-container']}>
      {notifications.map((notification) => (
        <div
          key={notification.id}
          className={`${styles.notification} ${styles[notification.type] || ''}`}
          onClick={() => dismiss(notification.id)}
          role={notification.type === 'error' ? 'alert' : 'status'}
          aria-atomic="true"
        >
          <div className={styles['notification-icon']}>
            {notification.type === 'success' && '✓'}
            {notification.type === 'error' && '✕'}
            {notification.type === 'warning' && '⚠'}
            {notification.type === 'info' && 'ℹ'}
          </div>
          <div className={styles['notification-content']}>
            <div className={styles['notification-title']}>{notification.title}</div>
            {notification.message ? <div className={styles['notification-message']}>{notification.message}</div> : null}
          </div>
          <button
            type="button"
            className={styles['notification-close']}
            aria-label={`Dismiss ${notification.title || 'notification'}`}
            onClick={(event) => {
              event.stopPropagation();
              dismiss(notification.id);
            }}
          >×</button>
        </div>
      ))}
    </div>
  );
}

export default NotificationContainer;
