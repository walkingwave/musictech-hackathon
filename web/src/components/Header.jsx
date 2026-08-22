// Top bar: wordmark, current session name, and the view tabs. TRACKS is
// disabled until a vocal has been analyzed (there is nothing to mix yet).
export default function Header({ view, onView, sessionName, tracksReady }) {
  const tabs = [
    { id: 'input', label: 'Input', enabled: true },
    { id: 'studio', label: 'Studio', enabled: tracksReady },
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
