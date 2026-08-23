// Top bar: wordmark, current session name, the global model selector, and the
// view tabs. TRACKS is disabled until a vocal has been analyzed (there is
// nothing to mix yet). The model selector lives here so the choice applies to
// every view, not just the Input page, and stays visible while you work.
export default function Header({
  view,
  onView,
  sessionName,
  tracksReady,
  backends = [],
  backend,
  onBackend,
}) {
  const tabs = [
    { id: 'input', label: 'Input', enabled: true },
    { id: 'studio', label: 'Studio', enabled: true },
    { id: 'instrument', label: 'New Instrument', enabled: true },
  ];
  return (
    <header className="header">
      <div className="wordmark">
        <span className="glyph" />
        Backing Track Generator
      </div>
      <div className="session">
        <span className="label">Session</span>
        <span className="value">{sessionName}</span>
      </div>
      {onBackend && (
        <div className="model-select">
          <span className="label">Model</span>
          <select
            value={backend}
            onChange={(e) => onBackend(e.target.value)}
            title="Which backend generates audio"
          >
            {backends.map((b) => (
              <option key={b.id} value={b.id} disabled={!b.available}>
                {b.label}
                {b.available ? '' : ' — unavailable'}
              </option>
            ))}
          </select>
        </div>
      )}
      <nav className="tabs">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`tab${view === t.id ? ' active' : ''}`}
            disabled={!t.enabled}
            onClick={() => onView(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>
    </header>
  );
}
