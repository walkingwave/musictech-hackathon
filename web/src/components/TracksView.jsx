import { useState } from 'react';
import Section from './Section.jsx';
import Waveform from './Waveform.jsx';

const fmt = (s) => {
  if (!s || !isFinite(s)) return '0:00';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
};

// DAW-style multitrack view: transport with a shared playhead, per-track
// waveform / mute / solo / volume, individual WAV download, and per-track
// regenerate with its own prompt. All playback lives in the engine.
export default function TracksView({
  engine,
  stems,
  onRegenerate,
  busyPart,
  defaultPrompt,
  stemUrl,
  vocalUrl,
  exportHref,
}) {
  const {
    tracks,
    playing,
    muted,
    soloed,
    volumes,
    duration,
    position,
    getBuffer,
    play,
    pause,
    stop,
    seek,
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
            <span className="time">
              {fmt(position)} / {fmt(duration)}
            </span>
            <button type="button" className="solid" onClick={playing ? pause : () => play()}>
              {playing ? 'Pause' : 'Play'}
            </button>
            <button type="button" onClick={stop}>
              Stop
            </button>
            {exportHref && (
              <a className="export" href={exportHref}>
                Export All
              </a>
            )}
          </span>
        }
      >
        {tracks.length === 0 ? (
          <div className="empty">No stems yet — generate from the Input view</div>
        ) : (
          tracks.map((name) => (
            <TrackRow
              key={name}
              name={name}
              isPart={name !== 'vocal'}
              stem={stems[name]}
              buffer={getBuffer(name)}
              duration={duration}
              position={position}
              muted={muted.has(name)}
              soloed={soloed.has(name)}
              volume={volumes[name] ?? 1}
              busy={busyPart === name}
              defaultPrompt={defaultPrompt}
              downloadUrl={name === 'vocal' ? vocalUrl : stemUrl(name)}
              onSeek={seek}
              onMute={() => toggleMute(name)}
              onSolo={() => toggleSolo(name)}
              onVolume={(v) => setVolume(name, v)}
              onRegenerate={(opts) => onRegenerate(name, opts)}
            />
          ))
        )}
      </Section>
    </div>
  );
}

function TrackRow({
  name,
  isPart,
  stem,
  buffer,
  duration,
  position,
  muted,
  soloed,
  volume,
  busy,
  defaultPrompt,
  downloadUrl,
  onSeek,
  onMute,
  onSolo,
  onVolume,
  onRegenerate,
}) {
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState(defaultPrompt || '');
  const [noise, setNoise] = useState(0.8);

  return (
    <div className="track-block">
      <div className="track-head">
        <span className="track-name">{name}</span>
        <div className="track-controls">
          <button type="button" className={`toggle${muted ? ' on' : ''}`} onClick={onMute} title="Mute">
            M
          </button>
          <button type="button" className={`toggle${soloed ? ' on' : ''}`} onClick={onSolo} title="Solo">
            S
          </button>
          <input
            type="range"
            className="volume"
            min="0"
            max="1.5"
            step="0.05"
            value={volume}
            onChange={(e) => onVolume(Number(e.target.value))}
            title="Volume"
          />
          <a className="mini" href={downloadUrl} download={`${name}.wav`} title="Download WAV">
            WAV
          </a>
          {isPart && (
            <button
              type="button"
              className={`mini${open ? ' on' : ''}`}
              onClick={() => setOpen((v) => !v)}
            >
              Regen
            </button>
          )}
        </div>
      </div>

      <Waveform
        buffer={buffer}
        duration={duration}
        position={position}
        muted={muted}
        onSeek={onSeek}
      />

      {isPart && open && (
        <div className="regen-panel">
          <input
            className="regen-prompt"
            placeholder={defaultPrompt || 'style, e.g. gritty funk'}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
          <label className="noise">
            divergence <output>{noise.toFixed(2)}</output>
            <input
              type="range"
              min="0.6"
              max="0.95"
              step="0.05"
              value={noise}
              onChange={(e) => setNoise(Number(e.target.value))}
            />
          </label>
          <button
            type="button"
            className="solid"
            disabled={busy}
            onClick={() => onRegenerate({ style: prompt, noise })}
          >
            {busy ? 'Generating…' : 'Regenerate'}
          </button>
          {stem && <span className="stem-badge">{stem.backend_used} · seed {stem.seed}</span>}
        </div>
      )}
    </div>
  );
}
