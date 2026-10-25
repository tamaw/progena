/** "N earlier" / "Show less" control for cards trimmed to their latest lines. */
export default function EarlierLinesToggle({ hiddenCount, showAll, onToggle }) {
  if (!showAll && hiddenCount === 0) return null;
  return (
    <button type="button" className="earlier-lines-toggle" aria-expanded={showAll} onClick={onToggle}>
      {showAll ? 'Show less' : `${hiddenCount} earlier`}
    </button>
  );
}
