/**
 * Small presentational cards shared by the list+detail pages (Agents, Channels,
 * Models…). These were previously duplicated verbatim in each page. They use the
 * shared `agents-*` styling so any page can drop them in.
 */

export function MetricCard({ label, value, detail }) {
  return (
    <div className="agents-metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

export function CardFact({ label, children }) {
  return (
    <div className="agents-card-fact">
      <span className="agents-card-fact__label">{label}</span>
      <span className="agents-card-fact__value">{children}</span>
    </div>
  );
}

export function CapabilityChip({ label, on }) {
  return (
    <span className={`agents-chip ${on ? 'agents-chip--ok' : 'agents-chip--muted'}`}>
      {label}: {on ? 'yes' : 'no'}
    </span>
  );
}

export function DraftField({ label, hint, children }) {
  return (
    <label className="agents-draft-field">
      <span className="agents-draft-field__label">{label}</span>
      {children}
      {hint ? <span className="agents-draft-field__hint">{hint}</span> : null}
    </label>
  );
}
