/**
 * One framed box of a tool call's detail -- Input, Output or Error -- with
 * the same chrome for both sides: a label and meta header, then a body
 * shaped by toolViewModel.js (fields, code, command, diff, list, text).
 */

function CodeBody({ text, startLine }) {
  if (!Number.isFinite(startLine)) {
    return <pre className="tool-io__code">{text}</pre>;
  }
  const lines = text.split('\n');
  return (
    <pre className="tool-io__code tool-io__code--numbered">
      {lines.map((line, index) => (
        // Line numbers are the line's identity here; the list never reorders.
        <span key={index} className="tool-io__line">
          <span className="tool-io__line-number" aria-hidden="true">{startLine + index}</span>
          <span className="tool-io__line-text">{line}</span>
        </span>
      ))}
    </pre>
  );
}

function DiffBody({ removed, added }) {
  const side = (text, sign, tone) => text.split('\n').map((line, index) => (
    <span key={`${tone}-${index}`} className={`tool-io__line tool-io__line--${tone}`}>
      <span className="tool-io__line-number" aria-hidden="true">{sign}</span>
      <span className="tool-io__line-text">{line}</span>
    </span>
  ));
  return (
    <pre className="tool-io__code tool-io__code--numbered">
      {side(removed, '−', 'removed')}
      {side(added, '+', 'added')}
    </pre>
  );
}

function Body({ body }) {
  switch (body.kind) {
    case 'fields':
      return (
        <>
          {body.rows.length > 0 && (
            <dl className="tool-io__fields">
              {body.rows.map((row) => (
                <div key={row.name} className="tool-io__field">
                  <dt>{row.name}</dt>
                  <dd>{row.value}</dd>
                </div>
              ))}
            </dl>
          )}
          {body.block && <Body body={body.block} />}
        </>
      );
    case 'code':
      return (
        <>
          {body.name && <div className="tool-io__block-name">{body.name}</div>}
          <CodeBody text={body.text} startLine={body.startLine} />
        </>
      );
    case 'command':
      return <pre className="tool-io__code"><span className="prompt">$ </span>{body.text}</pre>;
    case 'diff':
      return <DiffBody removed={body.removed} added={body.added} />;
    case 'list':
      return (
        <ul className="tool-io__list">
          {body.items.map((entry) => <li key={entry}>{entry}</li>)}
        </ul>
      );
    default:
      return <pre className="tool-io__code">{body.text}</pre>;
  }
}

export default function ToolIOBox({ view }) {
  if (!view) return null;
  return (
    <section className={`tool-io${view.tone ? ` tool-io--${view.tone}` : ''}`}>
      <header className="tool-io__header">
        <span className="tool-io__label">{view.label}</span>
        {view.meta && <span className="tool-io__meta">{view.meta}</span>}
      </header>
      {view.body && <div className="tool-io__body"><Body body={view.body} /></div>}
    </section>
  );
}
