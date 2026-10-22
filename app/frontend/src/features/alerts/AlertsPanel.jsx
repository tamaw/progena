import { useCallback, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { markAlertsViewed, selectUiAlerts } from './state';
import { useConfig } from '../../context/ConfigContext';
import { CloseIcon } from '../../components/navIcons';
import { formatTimestamp, formatRelativeTimestamp } from './alertModel';
import { SegmentedControl, TopNavPane } from '../../components/panels/PanelFrame';

const EMPTY_ALERTS = [];

export function AlertsPanel({ onClose, isPinned, onTogglePin, isOpen }) {
  const alerts = useSelector(selectUiAlerts) || EMPTY_ALERTS;
  const [selectedAlertId, setSelectedAlertId] = useState(null);
  const [filter, setFilter] = useState('all');
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const selectedAlert = alerts.find((alert) => alert.id === selectedAlertId) || null;
  const unreadAlertIds = useMemo(
    () => alerts.filter((alert) => !alert.viewed).map((alert) => alert.id),
    [alerts],
  );
  const acknowledgeAlerts = useCallback((alertIds) => {
    if (!alertIds.length) {
      return;
    }
    // Do not alter Redux state here: only the correlated alerts.updated frame
    // confirms that the server has cleared an alert's unread state.
    void dispatch(markAlertsViewed({
      alertsUrl: getApiUrl('/api/ui/alerts'),
      alertIds,
    })).unwrap().catch(() => undefined);
  }, [dispatch, getApiUrl]);
  const selectAlert = useCallback((alert) => {
    setSelectedAlertId(alert.id);
    if (!alert.viewed) {
      acknowledgeAlerts([alert.id]);
    }
  }, [acknowledgeAlerts]);

  const visibleAlerts = useMemo(() => {
    if (filter === 'unread') return alerts.filter((alert) => !alert.viewed);
    if (filter === 'critical') return alerts.filter((alert) => ['error', 'critical'].includes(alert.type));
    return alerts;
  }, [alerts, filter]);
  const todayAlerts = visibleAlerts.filter((alert) => !alert.viewed);
  const earlierAlerts = visibleAlerts.filter((alert) => alert.viewed);
  const alertGroups = [
    { title: 'Today', alerts: todayAlerts },
    { title: 'Earlier', alerts: earlierAlerts },
  ].filter((group) => group.alerts.length > 0);

  const alertIcon = (alert) => {
    if (['error', 'critical'].includes(alert.type)) return { className: 'alert__ic--warn', label: '!' };
    if (alert.type === 'warning') return { className: 'alert__ic--warn', label: '!' };
    if (alert.type === 'success') return { className: 'alert__ic--ok', label: '✓' };
    return { className: alert.viewed ? 'alert__ic--muted' : 'alert__ic--info', label: alert.viewed ? '●' : '@' };
  };

  const unreadCount = alerts.filter((a) => !a.viewed).length;

  return (
    <TopNavPane
      title="Alerts"
      badge={unreadCount > 0 ? `${unreadCount} new` : 'All caught up'}
      actions={unreadAlertIds.length > 0 ? (
        <button
          type="button"
          className="topnav-pane__text-btn"
          onClick={() => acknowledgeAlerts(unreadAlertIds)}
        >
          Mark all read
        </button>
      ) : null}
      className="topnav-pane--alerts"
      bodyClassName="alerts-pane__body"
      isPinned={isPinned}
      onTogglePin={onTogglePin}
      isOpen={isOpen}
      onClose={onClose}
    >
      <SegmentedControl
        className="alerts-pane__tabs"
        ariaLabel="Alert filter"
        value={filter}
        onChange={setFilter}
        items={[
          { value: 'all', label: 'All' },
          { value: 'unread', label: 'Unread' },
          { value: 'critical', label: 'Critical' },
        ]}
      />
      {!alerts.length && visibleAlerts.length === 0 ? <div className="chrome-empty">No matching alerts.</div> : null}
      <div className="alerts-pane__scroll">
        {alertGroups.map((group) => (
          <div key={group.title} className="alerts-group">
            <div className="alerts-group__head">{group.title}</div>
            {group.alerts.map((alert) => {
              const icon = alertIcon(alert);
              return (
                <button
                  key={alert.id}
                  type="button"
                  className={`alert ${alert.viewed ? 'is-viewed' : 'is-new'}`}
                  onClick={() => selectAlert(alert)}
                >
                  <span className={`alert__ic ${icon.className}`}>{icon.label}</span>
                  <span className="alert__body">
                    <span className="alert__title">{alert.title}</span>
                    <span className="alert__sub">{alert.message}</span>
                  </span>
                  <time className="alert__time" title={formatTimestamp(alert.timestamp)}>{formatRelativeTimestamp(alert.timestamp)}</time>
                </button>
              );
            })}
          </div>
        ))}
      </div>
      {selectedAlert ? (
        <section className="alert-detail-card">
          <button type="button" className="alert-detail-card__close" onClick={() => setSelectedAlertId(null)}><CloseIcon /></button>
          <h3>{selectedAlert.title}</h3>
          <p>{formatTimestamp(selectedAlert.timestamp)}</p>
          <pre>{selectedAlert.message}</pre>
        </section>
      ) : null}
    </TopNavPane>
  );
}
