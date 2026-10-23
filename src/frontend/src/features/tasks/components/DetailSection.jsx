

export function DetailSection({ title, children, meta }) {
  return (
    <section className="task-sec">
      <h3 className="task-sec__h">{title}</h3>
      {meta ? <div className="task-sec__meta">{meta}</div> : null}
      {children}
    </section>
  );
}
