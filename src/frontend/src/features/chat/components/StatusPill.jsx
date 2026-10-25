/**
 * Small `.tool__status`-styled status label (chrome-frame.css), catalogued
 * in the design library. The chat transcript itself shows no status labels:
 * entry dot colours carry status there.
 */
export default function StatusPill({ label, tone }) {
  const toneClass = tone === 'ok'
    ? 'tool__status--ok'
    : tone === 'err'
      ? 'tool__status--err'
      : tone === 'pending'
        ? 'tool__status--pending'
        : '';
  return <span className={`tool__status ${toneClass}`}>{label}</span>;
}
