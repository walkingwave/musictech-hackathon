import { useCallback, useEffect, useRef, useState } from 'react';
import ClipView from './ClipView.jsx';
import MidiEditor from './MidiEditor.jsx';
import InstrumentSlot from './InstrumentSlot.jsx';
import StudioRecorder from './StudioRecorder.jsx';
import { parseRequest, describePlan } from '../parseRequest.js';
import * as apiClient from '../api.js';

// Timeline studio (light, on-brand) with the controls a basic DAW needs:
// grid, zoom, adjustable snap (incl. off-grid), a move / split / range tool,
// clip trim + split + duplicate, section highlight, loop, keyboard shortcuts.

const LANE_H = 76;
const HEADER_W = 168;
const RULER_H = 26;
const MIN_CLIP = 0.05;

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

const SNAPS = [
  { id: 'bar', label: 'Bar', div: 1 },
  { id: 'half', label: '1/2', div: 2 },
  { id: 'beat', label: 'Beat', div: 4 },
  { id: '8th', label: '1/8', div: 8 },
  { id: 'off', label: 'Off', div: 0 },
];

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

export default function Studio({
  engine, bpm, keyName, mode, onBpm, onKey, onMode, detected,
  onGenerateStem, onGenerateFromReference, onRenderMidi, sessionId,
  instruments = [], onCreateInstrument, sampler, backend,
}) {
  const {
    tracks,
    playing,
    position,
    duration,
    context,
    getBuffer,
    addTrackWithClip,
    moveClip,
    updateClip,
    splitClip,
    extractRegion,
    duplicateClip,
    removeClip,
    removeTrack,
    replaceRegion,
    setTrackProp,
    addTrack,
    addMidiTrack,
    setClipNotes,
    attachBuffer,
    beginGesture,
    undo,
    redo,
    canUndo,
    canRedo,
    play,
    pause,
    stop,
    seek,
    loop,
    setLoop,
  } = engine;

  const secondsPerBar = (60 / (bpm || 120)) * 4;
  const [pps, setPps] = useState(34);
  const [tool, setTool] = useState('move');
  const [snapId, setSnapId] = useState('bar');
  const [selected, setSelected] = useState(null);
  const [region, setRegion] = useState(null); // {clipId, a, b}
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const scrollRef = useRef(null);

  const snapDiv = SNAPS.find((s) => s.id === snapId)?.div ?? 1;
  const snapSec = snapDiv > 0 ? secondsPerBar / snapDiv : 0;
  const snap = (t) => (snapSec > 0 ? Math.round(t / snapSec) * snapSec : t);

  const laneW = Math.max(1200, (duration + 8) * pps);
  const timeToX = (t) => t * pps;
  const xToTime = (x) => x / pps;

  const zoom = (dir) => setPps((p) => Math.max(8, Math.min(220, dir > 0 ? p * 1.35 : p / 1.35)));

  const selTrack = selected ? tracks.find((t) => t.id === selected.trackId) : null;
  const selClip = selTrack?.clips.find((c) => c.id === selected.clipId) || null;

  // --- keyboard shortcuts ------------------------------------------------
  useEffect(() => {
    const onKey = (e) => {
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

      // Undo/redo first: these carry modifiers and must not fall through to
      // the single-letter shortcuts below.
      if ((e.metaKey || e.ctrlKey) && (e.key === 'z' || e.key === 'Z')) {
        e.preventDefault();
        e.shiftKey ? redo() : undo();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && (e.key === 'y' || e.key === 'Y')) {
        e.preventDefault();
        redo();
        return;
      }
      if (e.metaKey || e.ctrlKey) return;

      if (e.code === 'Space') {
        e.preventDefault();
        playing ? pause() : play();
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selClip && selTrack) {
          removeClip(selTrack.id, selClip.id);
          setSelected(null);
          setRegion(null);
        }
      } else if (e.key === 's' || e.key === 'S') {
        if (selClip && selTrack) splitClip(selTrack.id, selClip.id, position);
      } else if (e.key === 'd' || e.key === 'D') {
        if (selClip && selTrack) duplicateClip(selTrack.id, selClip.id);
      } else if (e.key === 'Escape') {
        setRegion(null);
      } else if (e.key === '+' || e.key === '=') {
        zoom(1);
      } else if (e.key === '-' || e.key === '_') {
        zoom(-1);
      } else if (e.key === '1') setTool('move');
      else if (e.key === '2') setTool('range');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [
    playing, pause, play, selClip, selTrack,
    removeClip, splitClip, duplicateClip, position, undo, redo,
  ]);

  // Ctrl/Cmd + wheel to zoom around the cursor.
  const onWheel = (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    zoom(e.deltaY < 0 ? 1 : -1);
  };

  const onRulerDown = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    seek(xToTime(x));
  };

  const decodeResult = useCallback(
    async (result) => context().decodeAudioData(await (await fetch(result.audio_url)).arrayBuffer()),
    [context],
  );

  const regenClip = async ({ style, noise, bars }) => {
    if (!selClip || !selTrack) return;
    setBusy(true);
    setStatus(`Regenerating ${selTrack.name}…`);
    try {
      const result = await onGenerateStem({
        part: selTrack.kind,
        style,
        noise,
        bars,
        start_bar: selClip.startBar || 0,
        seed: Math.floor(Math.random() * 1e9),
      });
      const buffer = await decodeResult(result);
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

  const onRecorded = async (blob) => {
    const buffer = await context().decodeAudioData(await blob.arrayBuffer());
    const n = tracks.filter((t) => t.kind === 'audio').length + 1;
    addTrackWithClip(`Audio ${n}`, 'audio', buffer, { start: snap(position) });
  };

  // Import an audio file straight onto the timeline as a new clip.
  const onImport = async (file) => {
    if (!file) return;
    try {
      const buffer = await context().decodeAudioData(await file.arrayBuffer());
      const name = file.name.replace(/\.[^.]+$/, '');
      addTrackWithClip(name || 'Import', 'audio', buffer, { start: snap(position) });
    } catch (e) {
      setStatus(`Import failed — ${e.message}`);
    }
  };

  // Trim a clip edge; clamp to buffer bounds and a minimum length.
  const trimClip = (trackId, clip, side, t) => {
    const buf = getBuffer(clip.id);
    const bufDur = buf ? buf.duration : clip.offset + clip.duration;
    if (side === 'left') {
      const minStart = clip.start - clip.offset; // offset can't go below 0
      const maxStart = clip.start + clip.duration - MIN_CLIP;
      const newStart = Math.max(minStart, Math.min(t, maxStart));
      const delta = newStart - clip.start;
      updateClip(trackId, clip.id, {
        start: newStart,
        offset: clip.offset + delta,
        duration: clip.duration - delta,
      });
    } else {
      const maxEnd = clip.start + (bufDur - clip.offset);
      const newEnd = Math.max(clip.start + MIN_CLIP, Math.min(t, maxEnd));
      updateClip(trackId, clip.id, { duration: newEnd - clip.start });
    }
  };

  // Render a track's audio to a WAV blob, to post as a generation reference.
  const trackToWav = async (track) => {
    const { audioBufferToWav } = await import('../wav.js');
    const clip = track.clips[0];
    const buffer = clip && getBuffer(clip.id);
    if (!buffer) throw new Error(`${track.name} has no audio`);
    return audioBufferToWav(buffer, clip.offset, clip.duration);
  };

  // "New AI track": generate guided by another track instead of by a guide
  // synthesized from the chord grid.
  const generateReferenceTrack = async ({ referenceTrackId, prompt, noise, name }) => {
    const reference = tracks.find((t) => t.id === referenceTrackId);
    if (!reference) return;
    setBusy(true);
    setStatus(`Generating ${name} from ${reference.name}…`);
    try {
      const wav = await trackToWav(reference);
      const result = await onGenerateFromReference({
        referenceWav: wav,
        prompt,
        noise,
        name,
        seed: Math.floor(Math.random() * 1e9),
      });
      const buffer = await decodeResult(result);
      addTrackWithClip(name, 'audio', buffer, {
        start: reference.clips[0]?.start ?? 0,
        prompt,
        seed: result.seed,
        backendUsed: result.backend_used,
        duration: result.duration || buffer.duration,
        audioUrl: result.audio_url,
      });
      setStatus('');
    } catch (e) {
      setStatus(`Failed — ${e.message}`);
    }
    setBusy(false);
  };

  // Agentic bar: interpret the request, then generate each part in turn.
  const runRequest = async (text) => {
    setBusy(true);
    setStatus('Interpreting…');

    // Server-side interpretation understands phrasing the keyword matcher
    // cannot, and can also infer tempo and key. Fall back to the local
    // parser only if the request itself fails.
    let plan;
    try {
      plan = await apiClient.interpret(text, sessionId);
    } catch {
      const { parts, style } = parseRequest(text);
      plan = { tracks: parts.map((part) => ({ part, style: '' })), style, notes: '' };
    }

    if (!plan.tracks.length) {
      setStatus(plan.notes || 'No instruments recognised — try "bass, drums and piano".');
      setBusy(false);
      return;
    }

    // Tempo and key are part of the request too ("90 BPM in D minor"), and
    // they have to be applied before generating or the guides use the wrong grid.
    if (plan.bpm) onBpm(plan.bpm);
    if (plan.key) onKey(plan.key);
    if (plan.mode) onMode(plan.mode);

    try {
      for (const [i, spec] of plan.tracks.entries()) {
        const part = spec.part;
        const label = spec.name || part;
        // Send the style only when this request actually carries one. Sending
        // an empty string would re-pin the arrangement to "no style" and
        // reset the groove for every part added afterwards.
        const style = [plan.style, spec.style].filter(Boolean).join(', ') || undefined;
        setStatus(`Generating ${label} (${i + 1}/${plan.tracks.length})…`);
        // Sequential on purpose: the local model is a single instance, so
        // parallel requests would only contend for it.
        const result = await onGenerateStem({
          part,
          style,
          name: spec.name,
          instrument: spec.instrument,
          seed: Math.floor(Math.random() * 1e9),
        });
        const buffer = await decodeResult(result);
        addTrackWithClip(label[0].toUpperCase() + label.slice(1), part, buffer, {
          audioUrl: result.audio_url,
          start: 0,
          part,
          prompt: style,
          seed: result.seed,
          backendUsed: result.backend_used,
          duration: result.duration || buffer.duration,
        });
      }
      setStatus(plan.notes || '');
    } catch (e) {
      setStatus(`Failed — ${e.message}`);
    }
    setBusy(false);
  };

  // Loading an instrument into a slot generates its one-shots, after which
  // the part plays through the sampler. Notes are never regenerated, so
  // editing after this is instant.
  const loadInstrument = async (track, instrument) => {
    setTrackProp(track.id, 'instrument', instrument);
    if (!instrument) return;
    if (sampler.isLoaded(instrument)) return;
    setBusy(true);
    setStatus(`Sampling ${instrument.name}…`);
    try {
      await sampler.load(instrument, { backend });
      setStatus('');
    } catch (e) {
      setStatus(`Could not sample ${instrument.name} — ${e.message}`);
    }
    setBusy(false);
  };

  // Render a MIDI clip: its notes become the guide, the track's instrument
  // prompt becomes the sound. The notes stay on the clip afterwards, so it
  // can be edited and re-rendered.
  const renderMidiClip = async () => {
    if (!selClip || !selTrack) return;
    const instrument = selTrack.instrument;
    if (!instrument) {
      setStatus('Load an instrument into this track first.');
      return;
    }
    setBusy(true);
    setStatus(`Rendering ${selTrack.name}…`);
    try {
      const result = await onRenderMidi({
        notes: (selClip.notes || []).map(({ pitch, start, length, velocity }) => ({
          pitch, start, length, velocity,
        })),
        prompt: instrument.prompt,
        name: selTrack.name,
        bars: Math.max(1, Math.round((selClip.durationBeats || 8) / 4)),
      });
      const buffer = await decodeResult(result);
      beginGesture();
      attachBuffer(selTrack.id, selClip.id, buffer, {
        duration: result.duration || buffer.duration,
        seed: result.seed,
        backendUsed: result.backend_used,
        prompt: instrument.prompt,
      });
      setStatus('');
    } catch (e) {
      setStatus(`Failed — ${e.message}`);
    }
    setBusy(false);
  };

  const loopActive = !!loop;
  const toggleLoop = () => {
    if (loopActive) setLoop(null);
    else if (region) setLoop({ a: region.a, b: region.b });
  };

  return (
    <div className="studio">
      {/* Toolbar, grouped: transport | history | edit | ... | session | add.
          Separators mark the groups so the eye can skip to the one it wants
          instead of scanning one long undifferentiated row. */}
      <div className="studio-bar">
        <div className="bar-group">
          <button className="t-btn" onClick={playing ? pause : () => play()} title="Play/pause (Space)">
            {playing ? '❚❚' : '▶'}
          </button>
          <button className="t-btn" onClick={stop} title="Stop">
            ■
          </button>
          <button
            className={`t-btn${loopActive ? ' on' : ''}`}
            onClick={toggleLoop}
            disabled={!region && !loopActive}
            title="Loop the highlighted section"
          >
            ⟳
          </button>
          <span className="t-time">
            {fmt(position)} <span className="t-dim">/ {fmt(duration)}</span>
          </span>
        </div>

        <span className="bar-sep" />

        <div className="bar-group">
          <button className="t-btn" onClick={undo} disabled={!canUndo} title="Undo (⌘Z)">
            ↶
          </button>
          <button className="t-btn" onClick={redo} disabled={!canRedo} title="Redo (⇧⌘Z)">
            ↷
          </button>
        </div>

        <span className="bar-sep" />

        <div className="bar-group">
          <div className="tools">
            {[
              { id: 'move', label: 'Move', hint: 'Drag clips, drag edges to trim (1)' },
              { id: 'range', label: 'Select', hint: 'Drag to highlight, drag the highlight out to split (2)' },
            ].map((t) => (
              <button
                key={t.id}
                className={`tool-btn${tool === t.id ? ' on' : ''}`}
                onClick={() => setTool(t.id)}
                title={t.hint}
              >
                {t.label}
              </button>
            ))}
          </div>

          <label className="snap-ctl" title="Snap clips to this grid division">
            snap
            <select value={snapId} onChange={(e) => setSnapId(e.target.value)}>
              {SNAPS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>

          <div className="zoom-ctl">
            <button className="t-btn" onClick={() => zoom(-1)} title="Zoom out (−)">
              −
            </button>
            <button className="t-btn" onClick={() => zoom(1)} title="Zoom in (+)">
              +
            </button>
          </div>
        </div>

        <div className="bar-spacer" />

        <div className="bar-group">
          <label className="studio-field" title={detected ? 'detected from your recording' : ''}>
            bpm
            <input
              type="number"
              min="20"
              max="300"
              step="0.1"
              value={Math.round((bpm || 120) * 10) / 10}
              onChange={(e) => onBpm(Number(e.target.value))}
            />
          </label>
          <label className="studio-field">
            key
            <select value={keyName || 'C'} onChange={(e) => onKey(e.target.value)}>
              {NOTE_NAMES.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            <select value={mode || 'major'} onChange={(e) => onMode(e.target.value)}>
              <option value="major">maj</option>
              <option value="minor">min</option>
            </select>
          </label>
        </div>

        <span className="bar-sep" />

        <div className="bar-group">
          <AddTrackMenu
            tracks={tracks}
            busy={busy}
            onEmpty={() => addTrack(`Audio ${tracks.length + 1}`, 'audio')}
            onRecord={onRecorded}
            onImport={onImport}
            onAiTrack={generateReferenceTrack}
            onMidi={() => {
              const n = tracks.filter((t) => t.kind === 'midi').length + 1;
              const { trackId, clipId } = addMidiTrack(`MIDI ${n}`, null, {
                start: 0,
                duration: secondsPerBar * 4,
                notes: [],
              });
              setSelected({ trackId, clipId });
            }}
          />
        </div>
      </div>


      <div className="arrangement">
        <div className="headers" style={{ width: HEADER_W }}>
          <div className="corner" style={{ height: RULER_H }} />
          {tracks.map((t) => (
            <TrackHeader
              key={t.id}
              track={t}
              height={LANE_H}
              instruments={instruments}
              sampler={sampler}
              onLoadInstrument={(i) => loadInstrument(t, i)}
              onProp={(p, v) => setTrackProp(t.id, p, v)}
              onRemove={() => removeTrack(t.id)}
            />
          ))}
          {tracks.length === 0 && <div className="empty-h">no tracks</div>}
        </div>

        {tracks.length === 0 && (
          <div className="studio-empty">
            Empty timeline — Import an audio file, Record a clip, or generate
            backing from the Input view.
          </div>
        )}

        <div className="lanes-scroll" ref={scrollRef} onWheel={onWheel}>
          <div style={{ width: laneW }}>
            <Ruler
              width={laneW}
              height={RULER_H}
              secondsPerBar={secondsPerBar}
              pps={pps}
              loop={loop}
              onDown={onRulerDown}
            />
            <div className="lanes" style={{ position: 'relative' }}>
              {tracks.map((t) => (
                <Lane
                  key={t.id}
                  track={t}
                  height={LANE_H}
                  pps={pps}
                  secondsPerBar={secondsPerBar}
                  snapDiv={snapDiv}
                  tool={tool}
                  snapSec={snapSec}
                  selectedClipId={selected?.clipId}
                  region={region}
                  getBuffer={getBuffer}
                  onSeek={(x) => seek(xToTime(x))}
                  onSelect={(clipId) => {
                    setSelected({ trackId: t.id, clipId });
                    setRegion(null);
                  }}
                  onMove={(clipId, s) => moveClip(t.id, clipId, s)}
                  onTrim={(clip, side, tt) => trimClip(t.id, clip, side, tt)}
                  onBeginGesture={beginGesture}
                  onExtract={(clipId, a, b) => {
                    const newId = extractRegion(t.id, clipId, a, b);
                    setSelected({ trackId: t.id, clipId: newId });
                    setRegion(null);
                    return newId;
                  }}
                  onMoveById={(clipId, s) => moveClip(t.id, clipId, s)}
                  onRange={(clipId, a, b) => setRegion({ clipId, a, b })}
                />
              ))}
              <div
                className="playhead-line"
                style={{ left: timeToX(position), height: tracks.length * LANE_H }}
              />
            </div>
          </div>
        </div>
      </div>

      {selClip && selTrack && selTrack.kind === 'midi' && (
        <MidiEditor
          track={selTrack}
          clip={selClip}
          bpm={bpm}
          busy={busy}
          onBeginEdit={beginGesture}
          onNotesChange={(notes) => setClipNotes(selTrack.id, selClip.id, notes)}
          instruments={instruments}
          sampler={sampler}
          onLoadInstrument={(i) => loadInstrument(selTrack, i)}
          onLengthChange={(beats) => {
            const secondsPerBeat = 60 / (bpm || 100);
            updateClip(selTrack.id, selClip.id, {
              durationBeats: beats,
              duration: beats * secondsPerBeat,
            });
          }}
          onCreateInstrument={onCreateInstrument}
          onRender={renderMidiClip}
        />
      )}

      {selClip && selTrack && selTrack.kind !== 'midi' && (
        <ClipInspector
          track={selTrack}
          clip={selClip}
          region={region?.clipId === selClip.id ? region : null}
          secondsPerBar={secondsPerBar}
          busy={busy}
          buffer={getBuffer(selClip.id)}
          onRegenClip={regenClip}
          onRegenSection={regenSection}
          onSplit={() => splitClip(selTrack.id, selClip.id, position)}
          onDuplicate={() => duplicateClip(selTrack.id, selClip.id)}
          onDelete={() => {
            removeClip(selTrack.id, selClip.id);
            setSelected(null);
            setRegion(null);
          }}
        />
      )}

      {/* Pinned to the bottom of the window, below the clip inspector.
          Full width because describing an arrangement in words is the
          primary way in for someone starting from nothing. */}
      <AskBar busy={busy} onRun={runRequest} status={status} />
    </div>
  );
}

// Describe an arrangement in words; parse it into parts + style and
// generate each one. The plan is shown before you commit, because a
// misread request costs a minute of generation.
function AskBar({ busy, onRun, status }) {
  const [text, setText] = useState('');
  const plan = text.trim() ? parseRequest(text) : null;

  const submit = (e) => {
    e.preventDefault();
    if (!busy && text.trim()) onRun(text);
  };

  return (
    <form className="askbar" onSubmit={submit}>
      <input
        className="ask-input"
        placeholder="Describe what you want — “bass, drums and piano, bossa nova”"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={busy}
      />
      <button className="ask-go" disabled={busy || !plan?.parts.length}>
        {busy ? '…' : 'Generate'}
      </button>
      <span className="ask-plan">{status || (plan ? describePlan(plan) : '')}</span>
    </form>
  );
}

// "+ Track" with the four ways to get audio onto the timeline. The AI
// option needs a reference track, so it is disabled until one exists.
function AddTrackMenu({ tracks, busy, onEmpty, onRecord, onImport, onAiTrack, onMidi }) {
  const [open, setOpen] = useState(false);
  const [ai, setAi] = useState(false);
  const withAudio = tracks.filter((t) => t.clips.length > 0);

  return (
    <div className="add-menu">
      <button className="t-btn add-btn" onClick={() => setOpen((v) => !v)} disabled={busy}>
        + Track
      </button>

      {open && !ai && (
        <div className="menu-pop">
          <button
            onClick={() => {
              onEmpty();
              setOpen(false);
            }}
          >
            Empty audio track
          </button>
          <label className="menu-file">
            Import audio file…
            <input
              type="file"
              accept="audio/*"
              hidden
              onChange={(e) => {
                onImport(e.target.files[0]);
                setOpen(false);
              }}
            />
          </label>
          <button
            onClick={() => {
              onMidi();
              setOpen(false);
            }}
          >
            MIDI instrument track…
          </button>
          <button
            disabled={!withAudio.length}
            title={withAudio.length ? '' : 'needs an existing track to reference'}
            onClick={() => setAi(true)}
          >
            AI track from a reference…
          </button>
        </div>
      )}

      {open && ai && (
        <AiTrackForm
          tracks={withAudio}
          onCancel={() => {
            setAi(false);
            setOpen(false);
          }}
          onSubmit={(spec) => {
            onAiTrack(spec);
            setAi(false);
            setOpen(false);
          }}
        />
      )}
    </div>
  );
}

function AiTrackForm({ tracks, onCancel, onSubmit }) {
  const [referenceTrackId, setReference] = useState(tracks[0]?.id ?? '');
  const [prompt, setPrompt] = useState('');
  const [noise, setNoise] = useState(0.8);
  const [name, setName] = useState('AI track');

  return (
    <form
      className="menu-pop ai-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (referenceTrackId && prompt.trim()) onSubmit({ referenceTrackId, prompt, noise, name });
      }}
    >
      <label>
        follow
        <select value={referenceTrackId} onChange={(e) => setReference(e.target.value)}>
          {tracks.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        name
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <textarea
        rows={2}
        placeholder="what should it sound like? e.g. “gritty analog synth bass”"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
      />
      <label className="ai-noise">
        divergence <b>{noise.toFixed(2)}</b>
        <input
          type="range"
          min="0.6"
          max="0.95"
          step="0.05"
          value={noise}
          onChange={(e) => setNoise(Number(e.target.value))}
        />
      </label>
      <div className="ai-actions">
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        <button className="accent" disabled={!prompt.trim()}>
          Generate
        </button>
      </div>
    </form>
  );
}

function Lane({
  track,
  height,
  pps,
  secondsPerBar,
  snapDiv,
  tool,
  snapSec,
  selectedClipId,
  region,
  getBuffer,
  onSeek,
  onSelect,
  onMove,
  onTrim,
  onBeginGesture,
  onExtract,
  onMoveById,
  onRange,
}) {
  const barPx = secondsPerBar * pps;
  const beatPx = barPx / (snapDiv >= 4 ? snapDiv : 4);
  // Grid: strong bar lines + lighter beat lines, drawn as a background.
  const grid = {
    backgroundImage: `repeating-linear-gradient(90deg, var(--line-strong) 0 1px, transparent 1px ${barPx}px), repeating-linear-gradient(90deg, rgba(0,0,0,0.06) 0 1px, transparent 1px ${beatPx}px)`,
  };
  return (
    <div
      className="lane"
      style={{ height, ...grid }}
      onMouseDown={(e) => {
        if (e.target.classList.contains('lane')) {
          const rect = e.currentTarget.getBoundingClientRect();
          onSeek(e.clientX - rect.left);
        }
      }}
    >
      {track.clips.map((c) => (
        <ClipView
          key={c.id}
          clip={c}
          buffer={getBuffer(c.id)}
          color={KIND_COLOR[track.kind] || '#c4d4d6'}
          pps={pps}
          height={height}
          tool={tool}
          snapSec={snapSec}
          selected={selectedClipId === c.id}
          region={region?.clipId === c.id ? region : null}
          onSelect={() => onSelect(c.id)}
          onMove={(s) => {
            onBeginGesture();
            onMove(c.id, s);
          }}
          onMoveById={onMoveById}
          onTrim={(side, tt) => onTrim(c, side, tt)}
          onExtract={(a, b) => onExtract(c.id, a, b)}
          onRange={(a, b) => onRange(c.id, a, b)}
        />
      ))}
    </div>
  );
}

function Ruler({ width, height, secondsPerBar, pps, loop, onDown }) {
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
    <div className="ruler" style={{ width, height }} onMouseDown={onDown}>
      {marks}
      {loop && (
        <div
          className="loop-band"
          style={{ left: loop.a * pps, width: (loop.b - loop.a) * pps }}
        />
      )}
    </div>
  );
}

function TrackHeader({ track, height, instruments, sampler, onLoadInstrument, onProp, onRemove }) {
  return (
    <div className="track-header" style={{ height }}>
      <span className="th-color" style={{ background: KIND_COLOR[track.kind] || '#c4d4d6' }} />
      <div className="th-body">
        <div className="th-top">
          <span className="th-name">{track.name}</span>
          <button className="th-x" title="delete track" onClick={onRemove}>
            ×
          </button>
        </div>
        {track.kind === 'midi' && (
          <InstrumentSlot
            compact
            instrument={track.instrument}
            instruments={instruments}
            loading={sampler?.loading}
            ready={sampler?.isLoaded(track.instrument)}
            onLoad={onLoadInstrument}
            onClear={() => onLoadInstrument(null)}
          />
        )}
        <div className="th-controls">
          <button className={`th-btn${track.muted ? ' on' : ''}`} onClick={() => onProp('muted', !track.muted)}>
            M
          </button>
          <button className={`th-btn solo${track.soloed ? ' on' : ''}`} onClick={() => onProp('soloed', !track.soloed)}>
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
  onSplit,
  onDuplicate,
  onDelete,
}) {
  const [prompt, setPrompt] = useState(clip.prompt || '');
  const [noise, setNoise] = useState(0.8);
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
  const regionBars = region ? Math.max(1, Math.round((region.b - region.a) / secondsPerBar)) : 0;

  return (
    <div className="inspector">
      <div className="insp-title">
        <span className="dot" style={{ background: KIND_COLOR[track.kind] || '#c4d4d6' }} />
        {track.name}
        {clip.seed != null && <span className="insp-meta">seed {clip.seed}</span>}
        {clip.backendUsed && <span className="insp-meta">{clip.backendUsed}</span>}
      </div>

      {isPart && (
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
            title={region ? `bars ${regionBars}` : 'range tool: drag on the clip to select a section'}
            onClick={() => onRegenSection({ style: prompt, noise })}
          >
            {region ? `Regen section (${regionBars})` : 'Regen section'}
          </button>
        </div>
      )}

      <div className="insp-row">
        <button className="i-btn" onClick={onSplit} title="S">
          Split at playhead
        </button>
        <button className="i-btn" onClick={onDuplicate} title="D">
          Duplicate
        </button>
        <button className="i-btn" onClick={download}>
          Download WAV
        </button>
        <button className="i-btn danger" onClick={onDelete} title="Delete/Backspace">
          Delete clip
        </button>
      </div>
    </div>
  );
}
