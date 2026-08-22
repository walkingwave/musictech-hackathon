import Section from './Section.jsx';

// Multitrack mixer plus per-stem regenerate and the export deliverable.
// All playback state lives in the useMultitrack engine.
export default function TracksView({ engine, stems, onRegenerate, busyPart, exportHref }) {
  const {
    tracks,
    playing,
    muted,
    soloed,
    volumes,
    play,
    stop,
    toggleMute,
    toggleSolo,
    setVolume,
  } = engine;

  return (
    <div className="view">
      <Section
        num="04"
        title="Tracks"
        meta={
          <span className="transport">
            <button type="button" className="solid" onClick={playing ? stop : play}>
              {playing ? 'Stop' : 'Play'}
            </button>
            {exportHref && (
              <a className="export" href={exportHref}>
                Export
              </a>
            )}
          </span>
        }
      >
        {tracks.length === 0 ? (
          <div className="empty">No stems yet — generate from the Input view</div>
        ) : (
          tracks.map((name) => {
            const stem = stems[name];
            const isPart = name !== 'vocal';
            return (
              <div className="track" key={name}>
                <span className="track-name">{name}</span>
                <button
                  type="button"
                  className={`toggle${muted.has(name) ? ' on' : ''}`}
                  onClick={() => toggleMute(name)}
                >
                  M
                </button>
                <button
                  type="button"
                  className={`toggle${soloed.has(name) ? ' on' : ''}`}
                  onClick={() => toggleSolo(name)}
                >
                  S
                </button>
                <input
                  type="range"
                  className="volume"
                  min="0"
                  max="1.5"
                  step="0.05"
                  value={volumes[name] ?? 1}
                  onChange={(e) => setVolume(name, Number(e.target.value))}
                />
                {isPart && stem ? (
                  <button
                    type="button"
                    disabled={busyPart === name}
                    onClick={() => onRegenerate(name)}
                    title={`backend: ${stem.backend_used}`}
                  >
                    {busyPart === name ? '…' : 'Redo'}
                  </button>
                ) : (
                  <span className="stem-badge">{isPart ? '' : 'source'}</span>
                )}
              </div>
            );
          })
        )}
      </Section>
    </div>
  );
}
