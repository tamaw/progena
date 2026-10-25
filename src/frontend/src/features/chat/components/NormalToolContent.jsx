export default function NormalToolContent({ tool }) {
  const hasPayload = Boolean(tool?.command || tool?.body);
  const payload = (
    <div className="tool chat-event-tool">
      {tool.command && <div className="tool__cmd"><span className="prompt">$</span>{tool.command}</div>}
      {tool.body && <pre className="tool__body tool__body--out">{tool.body}</pre>}
    </div>
  );
  const summary = <span className={`tool__summary${hasPayload ? ' tool-event-disclosure__summary' : ''}`}>{tool.summary}</span>;

  if (!hasPayload) return summary;
  return (
    <details className="tool-event-disclosure">
      <summary>
        {summary}
        <span className="tool-event-disclosure__chevron" aria-hidden="true" />
      </summary>
      {payload}
    </details>
  );
}
