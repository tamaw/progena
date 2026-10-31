import styles from './InterruptedMarker.module.css';

const INTERRUPTION_BODIES = {
  runtime_restart: 'The runtime restarted during this turn. The last step may have partly run. Type continue to carry on.',
  user_cancelled: 'Cancelled by you.',
  stopped_early: 'This turn stopped before it finished. The last step may have partly run.',
};

export default function InterruptedMarker({ reason = 'runtime_restart' }) {
  return (
    <article className={`entry entry--chain-end ${styles.interruption}`} aria-label="Interrupted">
      <div className={`entry__gutter ${styles.gutter}`} aria-hidden="true">
        <span className={`entry__badge ${styles.badge}`} />
      </div>
      <div className="entry__main">
        <details className="tool-event-disclosure">
          <summary className="entry__head">
            <span className="entry__role">Interrupted</span>
            <span className="msg__head-trailing" />
            <span className="tool-event-disclosure__chevron" aria-hidden="true" />
          </summary>
          <div className="message-content">
            <p>{INTERRUPTION_BODIES[reason] || INTERRUPTION_BODIES.runtime_restart}</p>
          </div>
        </details>
      </div>
    </article>
  );
}
