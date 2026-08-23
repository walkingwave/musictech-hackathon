import { useEffect, useRef, useState } from 'react';

// Top bar: wordmark, the project menu, the (editable) session name, the global
// model selector, and the view tabs. Everything that acts on the project as a
// whole (new/open/close/delete/export/share) lives in the one menu so the bar
// stays short — the tabs and the model selector are the only things that have
// to be visible at a glance while you work.
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
  onNewProject,
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

  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  // Click-outside and Escape both close it. Without the pointerdown listener a
  // menu left open swallows the next click anywhere in the app.
  useEffect(() => {
    if (!menuOpen) return undefined;
    const onPointerDown = (e) => {
      if (!menuRef.current?.contains(e.target)) setMenuOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setMenuOpen(false); };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  const run = (fn) => () => { setMenuOpen(false); fn?.(); };

  const items = [
    { label: 'New project', onClick: onNewProject, enabled: !busy },
    { label: 'Open project…', onClick: onOpenSession, enabled: !busy },
    { separator: true },
    { label: 'Export as zip', onClick: onExportProject, enabled: canExport },
    { label: 'Share link', onClick: onShareProject, enabled: canExport },
    { separator: true },
    { label: 'Close project', onClick: onCloseSession, enabled: sessionActive && !busy },
    { label: 'Delete project', onClick: onDeleteSession, enabled: sessionActive && !busy, danger: true },
  ];

  return (
    <header className="header">
      <div className="wordmark">
        Unstable DAW
      </div>

      <div className="project-menu" ref={menuRef}>
        <button
          className={`proj-menu-btn${menuOpen ? ' open' : ''}`}
          onClick={() => setMenuOpen((v) => !v)}
          title="Project actions"
          aria-label="Project menu"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
        >
          <span className="proj-menu-icon" aria-hidden="true"><i /><i /><i /></span>
          <span className="menu-label">Project</span>
        </button>
        {menuOpen && (
          <div className="proj-menu-pop" role="menu">
            {items.map((item, i) => (item.separator ? (
              <div className="proj-menu-sep" key={`sep-${i}`} role="separator" />
            ) : (
              <button
                key={item.label}
                role="menuitem"
                className={`proj-menu-item${item.danger ? ' danger' : ''}`}
                disabled={!item.enabled}
                onClick={run(item.onClick)}
              >
                {item.label}
              </button>
            )))}
          </div>
        )}
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
