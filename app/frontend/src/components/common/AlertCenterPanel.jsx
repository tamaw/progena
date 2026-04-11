import { useMemo } from 'react';
import { useAlertCenter } from '../../context/AlertCenterContext';
import Modal from './Modal';

function formatTimestamp(timestamp) {
  if (!timestamp) {
    return '';
  }

  try {
    return new Date(timestamp * 1000).toLocaleString();
  } catch {
    return '';
  }
}

function alertLevelLabel(type) {
  return (type || 'info').replace(/_/g, ' ');
}

export default function AlertCenterPanel() {
  const {
    alerts,
    unreadCount,
    isPanelOpen,
    selectedAlert,
    isLoading,
    error,
    closePanel,
    openAlert,
    closeAlert,
  } = useAlertCenter();

  const title = useMemo(() => {
    if (unreadCount > 0) {
      return `Alerts (${unreadCount} new)`;
    }
    return 'Alerts';
  }, [unreadCount]);

  return (
    <>
      {isPanelOpen ? (
        <button
          type="button"
          className="panel-backdrop visible"
          aria-label="Close alerts"
          onClick={closePanel}
        />
      ) : null}

      <aside className={`notifications-panel ${isPanelOpen ? 'visible' : ''}`} aria-hidden={!isPanelOpen}>
        <div className="panel-header">
          <h3>{title}</h3>
          <button className="btn-close-panel" aria-label="Close alerts" onClick={closePanel}>
            ×
          </button>
        </div>

        <div className="panel-body notifications-list">
          {isLoading ? <div className="notification-empty">Loading alerts…</div> : null}
          {!isLoading && error ? <div className="notification-empty">Failed to load alerts: {error}</div> : null}
          {!isLoading && !error && alerts.length === 0 ? (
            <div className="notification-empty">No alerts yet.</div>
          ) : null}

          {!isLoading && !error && alerts.map((alert) => (
            <button
              key={alert.id}
              type="button"
              className={`notification-item ${alert.type || 'info'} ${alert.viewed ? '' : 'unread'}`}
              onClick={() => openAlert(alert.id)}
            >
              <div className="notification-content">
                <div className="notification-title-row">
                  <strong>{alert.title}</strong>
                  {!alert.viewed ? <span className="notification-unread-dot" aria-hidden="true" /> : null}
                </div>
                <div className="notification-meta">
                  <span className={`status-indicator ${alert.type || 'info'}`}>
                    {alertLevelLabel(alert.type)}
                  </span>
                  <span>{formatTimestamp(alert.timestamp)}</span>
                </div>
                <div className="notification-message-preview">{alert.message}</div>
              </div>
            </button>
          ))}
        </div>
      </aside>

      <Modal isOpen={Boolean(selectedAlert)} onClose={closeAlert} title={selectedAlert?.title || 'Alert'}>
        {selectedAlert ? (
          <div className="alert-detail">
            <div className="notification-meta">
              <span className={`status-indicator ${selectedAlert.type || 'info'}`}>
                {alertLevelLabel(selectedAlert.type)}
              </span>
              <span>{formatTimestamp(selectedAlert.timestamp)}</span>
            </div>
            <pre className="alert-detail-message">{selectedAlert.message}</pre>
          </div>
        ) : null}
      </Modal>
    </>
  );
}
