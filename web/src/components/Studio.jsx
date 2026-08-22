import { useCallback, useEffect, useRef, useState } from 'react';
import ClipView from './ClipView.jsx';
import StudioRecorder from './StudioRecorder.jsx';

// Ableton-style arrangement view: dark canvas, a bar ruler, track headers on
// the left, and colored clips laid out along a shared timeline. Clips can be
// dragged, regenerated (whole or by section), recorded live, and exported.

const PPS = 34; // pixels per second (horizontal zoom)
const LANE_H = 76;
const HEADER_W = 168;
const RULER_H = 26;

// Muted tints, on-brand with the light editorial palette — used only as a
// small clip title strip / header accent so tracks stay distinguishable.
const KIND_COLOR = {
  vocal: '#e6c3b3',
  bass: '#c3cae6',
  drums: '#e8dcb4',
  piano: '#c4dcc0',
  harmony: '#d7c6df',
  audio: '#c4d4d6',
};
const colorFor = (kind) => KIND_COLOR[kind] || KIND_COLOR.audio;

const fmt = (s) => {
  if (!s || !isFinite(s)) return '0:00';
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
};

export default function Studio({ engine, bpm, onGenerateStem, sessionReady }) {
  const {
    tracks,
    playing,
    position,
    duration,
    context,
    getBuffer,
    addTrackWithClip,
    moveClip,
    removeClip,
    removeTrack,
    replaceRegion,
    setTrackProp,
    play,
    pause,
    stop,
    seek,
  } = engine;

  const secondsPerBar = (60 / (bpm || 120)) * 4;
  const [selected, setSelected] = useState(null); // {trackId, clipId}
  const [region, setRegion] = useState(null); // {clipId, a, b} timeline secs
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');

  const laneW = Math.max(1200, (duration + 8) * PPS);
  const scrollRef = useRef(null);

  const timeToX = (t) => t * PPS;
  const xToTime = (x) => x / PPS;
  const snap = (t) => Math.round(t / secondsPerBar) * secondsPerBar;

  // Seek by clicking the ruler or empty lane space.
  const onRulerClick = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left + (scrollRef.current?.scrollLeft || 0);
    seek(xToTime(x));
  };

  // Decode a generate result (audio_url) into an AudioBuffer with our ctx.
  const decodeResult = useCallback(
    async (result) => {
      const bytes = await (await fetch(result.audio_url)).arrayBuffer();
      return context().decodeAudioData(bytes);
    },
    [context],
  );

  const selClip = selected
    ? tracks.find((t) => t.id === selected.trackId)?.clips.find((c) => c.id === selected.clipId)
    : null;
  const selTrack = selected ? tracks.find((t) => t.id === selected.trackId) : null;

  // Regenerate the whole selected clip, optionally at a new bar length.
  const regenClip = async ({ style, noise, bars }) => {
    if (!selClip || !selTrack) return;
    setBusy(true);
    setStatus(`Regenerating ${selTrack.name}…`);
    try {
      const result = await onGenerateStem({
        part: selTrack.kind,
        style,
        noise,
        bars: bars || selClip.startBar != null ? bars : undefined,
        start_bar: selClip.startBar || 0,
        seed: Math.floor(Math.random() * 1e9),
      });
      const buffer = await decodeResult(result);
      // Replace the whole clip in place (keep its start position).
      replaceRegion(selTrack.id, selClip.id, selClip.start, selClip.start + selClip.duration, buffer, {
        prompt: style,
        seed: result.seed,
        backendUsed: result.backend_used,
        duration: result.duration || buffer.duration,
        startBar: selClip.startBar || 0,
      });
    } catch (e) {
      setStatus(`Failed — ${e.message}`);
      setBusy(false);
      return;
    }
    setBusy(false);
    setStatus('');
  };

  // Regenerate only the highlighted region of the selected clip.
  const regenSection = async ({ style, noise }) => {
    if (!selClip || !selTrack || !region || region.clipId !== selClip.id) return;
    const { a, b } = region;
    const barsInSection = Math.max(1, Math.round((b - a) / secondsPerBar));
    const startBar = (selClip.startBar || 0) + Math.round((a - selClip.start) / secondsPerBar);
    setBusy(true);
    setStatus(`Regenerating bars ${startBar + 1}–${startBar + barsInSection}…`);
    try {
      const result = await onGenerateStem({
        part: selTrack.kind,
        style,
        noise,
        bars: barsInSection,
        start_bar: startBar,
        seed: Math.floor(Math.random() * 1e9),
      });
      const buffer = await decodeResult(result);
      replaceRegion(selTrack.id, selClip.id, a, b, buffer, {
        prompt: style,
        seed: result.seed,
        backendUsed: result.backend_used,
        duration: result.duration || buffer.duration,
        startBar,
      });
      setRegion(null);
    } catch (e) {
      setStatus(`Failed — ${e.message}`);
      setBusy(false);
      return;
    }
    setBusy(false);
    setStatus('');
  };

  // Drop a live recording as a clip at the playhead on a new audio track.
  const onRecorded = async (blob) => {
    const buffer = await context().decodeAudioData(await blob.arrayBuffer());
    const n = tracks.filter((t) => t.kind === 'audio').length + 1;
    addTrackWithClip(`Audio ${n}`, 'audio', buffer, { start: snap(position) });
  };

  return (
    <div className="studio">
      <div className="studio-bar">
        <div className="transport">
          <button className="t-btn" onClick={playing ? pause : () => play()}>
            {playing ? '❚❚' : '▶'}
          </button>
          <button className="t-btn" onClick={stop}>
            ■
          </button>
          <span className="t-time">
            {fmt(position)} / {fmt(duration)}
          </span>
          <span className="t-bpm">{Math.round(bpm)} BPM</span>
        </div>
        <StudioRecorder onRecorded={onRecorded} />
        <div className="studio-status">{status}</div>
      </div>

      <div className="arrangement">
        {/* left: track headers. right: scrollable lanes + ruler */}
        <div className="headers" style={{ width: HEADER_W }}>
          <div className="corner" style={{ height: RULER_H }} />
          {tracks.map((t) => (
            <TrackHeader
              key={t.id}
              track={t}
              height={LANE_H}
              onProp={(p, v) => setTrackProp(t.id, p, v)}
              onRemove={() => removeTrack(t.id)}
            />
          ))}
          {tracks.length === 0 && <div className="empty-h">no tracks</div>}
        </div>

        <div className="lanes-scroll" ref={scrollRef}>
          <div style={{ width: laneW }}>
            <Ruler
              width={laneW}
              height={RULER_H}
              secondsPerBar={secondsPerBar}
              pps={PPS}
              onClick={onRulerClick}
            />
            <div className="lanes" style={{ position: 'relative' }}>
              {tracks.map((t) => (
                <div
                  className="lane"
                  key={t.id}
                  style={{ height: LANE_H }}
                  onMouseDown={(e) => {
                    // click empty lane to seek
                    if (e.target.classList.contains('lane')) {
                      const rect = e.currentTarget.getBoundingClientRect();
                      seek(xToTime(e.clientX - rect.left));
                    }
                  }}
                >
                  {t.clips.map((c) => (
                    <ClipView
                      key={c.id}
                      clip={c}
                      buffer={getBuffer(c.id)}
                      color={colorFor(t.kind)}
                      pps={PPS}
                      height={LANE_H}
                      selected={selected?.clipId === c.id}
                      region={region?.clipId === c.id ? region : null}
                      secondsPerBar={secondsPerBar}
                      onSelect={() => {
                        setSelected({ trackId: t.id, clipId: c.id });
                        setRegion(null);
                      }}
                      onMove={(newStart) => moveClip(t.id, c.id, snap(newStart))}
                      onRegion={(a, b) => setRegion({ clipId: c.id, a, b })}
                    />
                  ))}
                </div>
              ))}
              {/* playhead spanning all lanes */}
              <div
                className="playhead-line"
                style={{ left: timeToX(position), height: tracks.length * LANE_H }}
              />
            </div>
          </div>
        </div>
      </div>

      {selClip && selTrack && (
        <ClipInspector
          track={selTrack}
          clip={selClip}
          region={region?.clipId === selClip.id ? region : null}
          secondsPerBar={secondsPerBar}
          busy={busy}
          buffer={getBuffer(selClip.id)}
          onRegenClip={regenClip}
          onRegenSection={regenSection}
          onDelete={() => {
            removeClip(selTrack.id, selClip.id);
            setSelected(null);
            setRegion(null);
          }}
        />
      )}
    </div>
  );
}

function Ruler({ width, height, secondsPerBar, pps, onClick }) {
  const bars = Math.ceil(width / (secondsPerBar * pps));
  const marks = [];
  for (let i = 0; i <= bars; i++) {
    const x = i * secondsPerBar * pps;
    marks.push(
      <div key={i} className="bar-mark" style={{ left: x }}>
        <span>{i + 1}</span>
      </div>,
    );
  }
  return (
    <div className="ruler" style={{ width, height }} onMouseDown={onClick}>
      {marks}
    </div>
  );
}

function TrackHeader({ track, height, onProp, onRemove }) {
  return (
    <div className="track-header" style={{ height }}>
      <span className="th-color" style={{ background: KIND_COLOR[track.kind] || '#4bb3c4' }} />
      <div className="th-body">
        <div className="th-top">
          <span className="th-name">{track.name}</span>
          <button className="th-x" title="delete track" onClick={onRemove}>
            ×
          </button>
        </div>
        <div className="th-controls">
          <button
            className={`th-btn${track.muted ? ' on' : ''}`}
            onClick={() => onProp('muted', !track.muted)}
          >
            M
          </button>
          <button
            className={`th-btn solo${track.soloed ? ' on' : ''}`}
            onClick={() => onProp('soloed', !track.soloed)}
          >
            S
          </button>
          <input
            className="th-vol"
            type="range"
            min="0"
            max="1.5"
            step="0.05"
            value={track.volume}
            onChange={(e) => onProp('volume', Number(e.target.value))}
          />
        </div>
      </div>
    </div>
  );
}

function ClipInspector({
  track,
  clip,
  region,
  secondsPerBar,
  busy,
  buffer,
  onRegenClip,
  onRegenSection,
  onDelete,
}) {
  const [prompt, setPrompt] = useState(clip.prompt || '');
  const [noise, setNoise] = useState(clip.noise ?? 0.8);
  const [bars, setBars] = useState(Math.max(1, Math.round(clip.duration / secondsPerBar)));

  useEffect(() => {
    setPrompt(clip.prompt || '');
    setBars(Math.max(1, Math.round(clip.duration / secondsPerBar)));
  }, [clip, secondsPerBar]);

  const isPart = track.kind !== 'audio';
  const download = () => {
    import('../wav.js').then(({ audioBufferToWav }) => {
      if (!buffer) return;
      const wav = audioBufferToWav(buffer, clip.offset, clip.duration);
      const url = URL.createObjectURL(wav);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${track.name}.wav`;
      a.click();
      URL.revokeObjectURL(url);
    });
  };
  const regionBars = region
    ? Math.max(1, Math.round((region.b - region.a) / secondsPerBar))
    : 0;

  return (
    <div className="inspector">
      <div className="insp-title">
        <span className="dot" style={{ background: KIND_COLOR[track.kind] || '#4bb3c4' }} />
        {track.name}
        {clip.seed != null && <span className="insp-meta">seed {clip.seed}</span>}
        {clip.backendUsed && <span className="insp-meta">{clip.backendUsed}</span>}
      </div>

      {isPart ? (
        <div className="insp-row">
          <input
            className="insp-prompt"
            placeholder="prompt — style for this part"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
          <label className="insp-field">
            divergence <b>{noise.toFixed(2)}</b>
            <input type="range" min="0.6" max="0.95" step="0.05" value={noise} onChange={(e) => setNoise(Number(e.target.value))} />
          </label>
          <label className="insp-field">
            bars
            <input className="insp-bars" type="number" min="1" max="128" value={bars} onChange={(e) => setBars(Number(e.target.value))} />
          </label>
          <button className="i-btn" disabled={busy} onClick={() => onRegenClip({ style: prompt, noise, bars })}>
            {busy ? '…' : 'Regen clip'}
          </button>
          <button
            className="i-btn accent"
            disabled={busy || !region}
            title={region ? `bars ${regionBars}` : 'alt+drag on the clip to select a section'}
            onClick={() => onRegenSection({ style: prompt, noise })}
          >
            {region ? `Regen section (${regionBars} bar${regionBars > 1 ? 's' : ''})` : 'Regen section'}
          </button>
        </div>
      ) : (
        <div className="insp-row">
          <span className="insp-note">Recorded audio · drag on the timeline to place it.</span>
        </div>
      )}

      <div className="insp-row">
        <button className="i-btn" onClick={download}>
          Download WAV
        </button>
        <button className="i-btn danger" onClick={onDelete}>
          Delete clip
        </button>
      </div>
    </div>
  );
}
