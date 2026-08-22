// Numbered spec-sheet card: "01 · SOURCE AUDIO" on the left, optional meta
// on the right, content below a hairline divider.
export default function Section({ num, title, meta, children }) {
  return (
    <section className="card">
      <div className="card-head">
        <span className="num">
          {num} · {title}
        </span>
        {meta && <span className="meta">{meta}</span>}
      </div>
      <div className="card-body">{children}</div>
    </section>
  );
}
