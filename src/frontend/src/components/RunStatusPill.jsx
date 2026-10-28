export default function RunStatusPill({ status }) {
  const label = String(status || 'queued').replaceAll('_', ' ');
  const tone = label.toLowerCase().replaceAll(' ', '-');
  return <span className={`run-status-pill run-status-pill--${tone}`}>{label}</span>;
}
