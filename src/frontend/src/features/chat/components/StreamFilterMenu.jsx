import { STREAM_EVENT_CATEGORIES, createDefaultStreamFilters } from '../models/streamFilters';

const DEFAULT_FILTERS = createDefaultStreamFilters();

function StreamFilterMenu({ filters, onChange, eventCounts }) {
  const allActive = Object.entries(DEFAULT_FILTERS).every(([id, enabled]) => filters[id] === enabled);
  const selectAll = () => onChange(createDefaultStreamFilters());

  return (
    <div className="sess-filters chat-events-filters" role="toolbar" aria-label="Activity filters">
      <button type="button" className={`sess-fchip ${allActive ? 'is-active' : ''}`} aria-pressed={allActive} onClick={selectAll}>All</button>
      {STREAM_EVENT_CATEGORIES.map((category) => (
        <button
          key={category.id}
          type="button"
          className={`sess-fchip ${filters[category.id] ? 'is-active' : ''}`}
          aria-pressed={Boolean(filters[category.id])}
          onClick={() => onChange({ ...filters, [category.id]: !filters[category.id] })}
          title={category.description}
        >
          <span className="adot" style={{ background: category.color }} />
          {category.label}
          {eventCounts?.[category.id] > 0 && <span className="count">{eventCounts[category.id]}</span>}
        </button>
      ))}
    </div>
  );
}

export default StreamFilterMenu;
