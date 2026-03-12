import { createContext, useState, useCallback } from 'react';

const NotificationContext = createContext(null);

let notificationId = 0;

export function NotificationProvider({ children }) {
  const [notifications, setNotifications] = useState([]);

  const show = useCallback((type, title, message, duration = 5000) => {
    const id = ++notificationId;
    const notification = { id, type, title, message, timestamp: Date.now() };
    
    setNotifications(prev => [...prev.slice(-4), notification]);

    if (duration > 0) {
      setTimeout(() => {
        setNotifications(prev => prev.filter(n => n.id !== id));
      }, duration);
    }

    return id;
  }, []);

  const dismiss = useCallback((id) => {
    setNotifications(prev => prev.filter(n => n.id !== id));
  }, []);

  const clear = useCallback(() => {
    setNotifications([]);
  }, []);

  return (
    <NotificationContext.Provider value={{ notifications, show, dismiss, clear }}>
      {children}
    </NotificationContext.Provider>
  );
}

export { NotificationContext };
