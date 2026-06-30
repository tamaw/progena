import { useNotifications } from '../../context/NotificationContext';
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
        >
          <div className={styles['notification-icon']}>
            {notification.type === 'success' && '✓'}
            {notification.type === 'error' && '✕'}
            {notification.type === 'warning' && '⚠'}
            {notification.type === 'info' && 'ℹ'}
          </div>
          <div className={styles['notification-content']}>
            <div className={styles['notification-title']}>{notification.title}</div>
            <div className={styles['notification-message']}>{notification.message}</div>
          </div>
          <button
            type="button"
            className={styles['notification-close']}
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
