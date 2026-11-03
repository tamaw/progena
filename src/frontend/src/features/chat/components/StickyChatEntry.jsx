import styles from './StickyChatEntry.module.css';

/** Same entry/gutter as the transcript, detached from its connected turn chain. */
export default function StickyChatEntry({ children, badgeClassName = 'entry__badge--tool', active = false, className = '', ...props }) {
  return <article className={`entry entry--standalone ${styles.row} ${className}`} {...props}>
    <div className="entry__gutter" aria-hidden="true">
      <span className={`entry__badge ${badgeClassName}${active ? ' activity-dot' : ''}`} />
    </div>
    <div className="entry__main">{children}</div>
  </article>;
}
