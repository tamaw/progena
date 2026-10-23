/**
 * Small `.tool__status`-styled label (chrome-frame.css), shared by
 * SubagentCard's own header status indicator and each of its per-nested-call
 * rows (SubagentChild), paired with `statusPillProps` (messageRenderUtils.js),
 * instead of near-identical inline spans each computing their own label/
 * class. A2ACallCard doesn't use this at all, at either level -- see
 * statusPillProps' and A2ASubEntry's doc comments for why.
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
