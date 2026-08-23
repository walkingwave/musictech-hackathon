// Top bar: wordmark, the (editable) session name, project export/share, the
// global model selector, and the view tabs. The model selector lives here so
// the choice applies to every view, not just the Generate page, and stays
// visible while you work.
export default function Header({
  view,
  onView,
  sessionName,
  onRenameSession,
  tracksReady,
  onExportProject,
  onShareProject,
  canExport = false,
  backends = [],
  backend,
  onBackend,
  onOpenSession,
  onCloseSession,
  onDeleteSession,
  sessionActive = false,
  busy = false,
}) {
  const tabs = [
    { id: 'input', label: 'Generate', enabled: true },
    { id: 'studio', label: 'Studio', enabled: true },
    { id: 'instrument', label: 'Instruments', enabled: true },
  ];
  const selected = backends.find((b) => b.id === backend);
  return (
    <header className="header">
      <div className="wordmark">
        Unstable DAW
      </div>
      <div className="session">
        <span className="label">Session</span>
        <input
          className="session-name"
          value={sessionName}
          onChange={(e) => onRenameSession?.(e.target.value)}
          spellCheck={false}
          title="Rename this session"
          aria-label="Session name"
        />
      </div>

      <div className="project-actions">
        <button
          className="proj-btn"
          disabled={!canExport}
          onClick={onExportProject}
          title="Download every stem, its MIDI, the vocal and a manifest as a zip"
        >
          Export
        </button>
        <button
          className="proj-btn"
          disabled={!canExport}
          onClick={onShareProject}
          title="Share a link to this project's export"
        >
          Share
        </button>
      </div>

      {onBackend && (
        <div className="model-select">
          <span className="label">Model</span>
          <select
            value={backend}
            onChange={(e) => onBackend(e.target.value)}
            title={selected?.note || 'Which backend generates audio'}
          >
            {backends.map((b) => (
              <option key={b.id} value={b.id} disabled={!b.available} title={b.note}>
                {b.label}
                {b.available ? '' : ' — unavailable'}
              </option>
            ))}
          </select>
          {selected && !selected.available && (
            <span className="model-note" title={selected.note}>
              {selected.note}
            </span>
          )}
        </div>
      )}

      <div className="session-actions">
        <button className="tab" onClick={onOpenSession} disabled={busy}>Open</button>
        <button className="tab" onClick={onCloseSession} disabled={!sessionActive || busy}>Close</button>
        <button className="tab" onClick={onDeleteSession} disabled={!sessionActive || busy}>Delete</button>
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
