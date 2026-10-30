import { UI_EVENT } from '../realtime/eventVocabulary';
import { memo, useState } from 'react';
import { useSelector } from 'react-redux';
import { selectUiEventQueue } from '../realtime/realtimeSlice';
import './EventDebugPanel.css';

const EventRow = memo(function EventRow({ event, sequence }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <li className="event-debug__row">
      <details onToggle={(event) => setExpanded(event.currentTarget.open)}>
        <summary>
          <span className="event-debug__type">{event.type}</span>
          <span className="event-debug__meta">#{sequence} · {event.id}</span>
          {event.timestamp ? <time className="event-debug__meta">{event.timestamp}</time> : null}
          {event.payload?.request_id ? (
            <span className="event-debug__meta">request: {event.payload.request_id}</span>
          ) : null}
        </summary>
        {expanded ? <pre>{JSON.stringify(event, null, 2)}</pre> : null}
      </details>
    </li>
  );
});

/** Read-only view of the canonical events already retained by Redux. */
export default function EventDebugPanel() {
  const queue = useSelector(selectUiEventQueue);
  const [filter, setFilter] = useState('');
  const [hideStatusUpdates, setHideStatusUpdates] = useState(true);
  const [clearedThrough, setClearedThrough] = useState(0);
  const query = filter.trim().toLowerCase();
  const retained = queue.filter(({ sequence }) => sequence > clearedThrough);
  const events = retained.filter(({ event }) => (
    (!hideStatusUpdates || event.type !== UI_EVENT.STATUS_UPDATED)
    && (!query || [event.type, event.id, event.payload?.request_id]
      .some((value) => String(value ?? '').toLowerCase().includes(query)))
  )).reverse();

  return (
    <div className="event-debug">
      <div className="event-debug__toolbar">
        <input
          type="search"
          className="set-text-input"
          aria-label="Filter events"
          placeholder="Filter type, event ID, request ID…"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <button
          type="button"
          className="topnav-pane__text-btn"
          onClick={() => setClearedThrough(queue.at(-1)?.sequence ?? 0)}
        >Clear view</button>
      </div>
      <div className="event-debug__filters">
        <span>Hide status updates</span>
        <label className={`set-switch ${hideStatusUpdates ? 'is-on' : ''}`}>
          <input
            type="checkbox"
            aria-label="Hide status updates"
            checked={hideStatusUpdates}
            onChange={(event) => setHideStatusUpdates(event.target.checked)}
          />
          <span className="set-switch__knob" aria-hidden="true" />
        </label>
      </div>
      <p className="event-debug__status">
        {events.length} of {retained.length} events · newest first · latest 500 retained
      </p>
      <ol className="event-debug__list" aria-label="Redux UI events">
        {events.map(({ event, sequence }) => <EventRow key={sequence} event={event} sequence={sequence} />)}
      </ol>
      {events.length === 0 ? (
        <p className="event-debug__empty">{query || retained.length > 0 ? 'No matching events.' : 'Waiting for UI events…'}</p>
      ) : null}
    </div>
  );
}
