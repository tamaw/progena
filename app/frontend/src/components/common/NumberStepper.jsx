// Themed numeric stepper. Native number-input spinners don't honour the dark
// theme (the arrows render white), so we hide them and provide our own −/+
// controls. `step` may be fractional; values are clamped and de-floated.
function NumberStepper({ value, min, max, step, onChange, ariaLabel }) {
  const decimals = String(step).includes('.') ? String(step).split('.')[1].length : 0;
  const normalise = (raw) => {
    const clamped = Math.max(min, Math.min(max, raw));
    return Number(clamped.toFixed(decimals));
  };
  const nudge = (direction) => onChange(normalise(value + direction * step));
  return (
    <div className="app-stepper">
      <button
        type="button"
        className="app-stepper__btn"
        aria-label={`Decrease ${ariaLabel}`}
        disabled={value <= min}
        onClick={() => nudge(-1)}
      >
        −
      </button>
      <input
        type="number"
        className="app-stepper__input"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-label={ariaLabel}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (Number.isFinite(next)) {
            onChange(normalise(next));
          }
        }}
      />
      <button
        type="button"
        className="app-stepper__btn"
        aria-label={`Increase ${ariaLabel}`}
        disabled={value >= max}
        onClick={() => nudge(1)}
      >
        +
      </button>
    </div>
  );
}

export default NumberStepper;
