/**
 * Small `.tool__status`-styled label (chrome-frame.css), shared by every
 * delegation card's status indicator -- the card-level header status
 * (SubagentCard / A2ACallCard) and each item's own per-call/per-update row
 * (SubagentChild / A2ACallUpdate) all render through this, paired with
 * `statusPillProps`/`a2aStatePillProps` (messageRenderUtils.js), instead of
 * four near-identical inline spans each computing their own label/class.
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
