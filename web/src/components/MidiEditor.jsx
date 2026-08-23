import { useCallback, useEffect, useRef, useState } from 'react';
import PianoRoll from './PianoRoll.jsx';
import { useMidiInput } from '../useMidiInput.js';
import InstrumentSlot from './InstrumentSlot.jsx';

// The piano roll, docked at the bottom of the studio for whichever MIDI
// clip is selected. Notes here are the guide track: Stable Audio 3 keeps
// the performance and supplies only the sound described by the track's
// instrument prompt.

let counter = 0;
const uid = () => `n-${++counter}`;

export default function MidiEditor({
  track,
  clip,
  bpm,
  busy,
  onNotesChange,
  onBeginEdit,
  onRender,
  instruments,
  onLoadInstrument,
}) {
  const [recording, setRecording] = useState(false);
  const [playhead, setPlayhead] = useState(null);

  const beatsPerBar = 4;
  const secondsPerBeat = 60 / (bpm || 100);
  const bars = Math.max(1, Math.round(clip.durationBeats || 8) / beatsPerBar);
  const startedAtRef = useRef(0);
  const ctxRef = useRef(null);

  // Monitoring only — a blip so the controller responds immediately. The
  // real sound arrives when the clip is rendered.
  const blip = useCallback((pitch, velocity = 90) => {
    if (!ctxRef.current) ctxRef.current = new AudioContext();
    const ctx = ctxRef.current;
    ctx.resume();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'triangle';
    osc.frequency.value = 440 * 2 ** ((pitch - 69) / 12);
    gain.gain.setValueAtTime((velocity / 127) * 0.2, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.3);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.31);
  }, []);

  // A played note arrives on release, when its length is finally known.
  const onPlayedNote = useCallback(
    ({ pitch, velocity, startedAt, endedAt }) => {
      blip(pitch, velocity);
      if (!recording) return;
      const snap = (b) => Math.max(0, Math.round(b / 0.25) * 0.25);
      const start = snap((startedAt - startedAtRef.current) / 1000 / secondsPerBeat);
      const length = Math.max(0.25, snap((endedAt - startedAt) / 1000 / secondsPerBeat));
      onNotesChange([...(clip.notes || []), { id: uid(), pitch, velocity, start, length }]);
    },
    [recording, secondsPerBeat, blip, clip.notes, onNotesChange],
  );

  const { devices, active, error, panic } = useMidiInput({ onNote: onPlayedNote });

  useEffect(() => {
    if (!recording) {
      setPlayhead(null);
      return undefined;
    }
    onBeginEdit();
    startedAtRef.current = performance.now();
    let frame;
    const tick = () => {
      const beats = (performance.now() - startedAtRef.current) / 1000 / secondsPerBeat;
      if (beats >= bars * beatsPerBar) {
        setRecording(false);
        panic();
        return;
      }
      setPlayhead(beats);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [recording, secondsPerBeat, bars, panic, onBeginEdit]);

  const notes = clip.notes || [];

  return (
    <div className="midi-editor">
      <div className="midi-bar">
        <span className="midi-title">{track.name}</span>

        <button
          className={`t-btn${recording ? ' rec' : ''}`}
          onClick={() => {
            if (recording) panic();
            setRecording((v) => !v);
          }}
          title="Record from your MIDI controller"
        >
          {recording ? '■ Stop' : '● Record'}
        </button>

        <InstrumentSlot
          instrument={track.instrument}
          instruments={instruments}
          onLoad={onLoadInstrument}
          onClear={() => onLoadInstrument(null)}
        />

        <button
          className="t-btn"
          disabled={busy || !notes.length || !track.instrument}
          onClick={onRender}
          title={
            !track.instrument
              ? 'Load an instrument into this track first'
              : !notes.length
                ? 'Play or draw some notes first'
                : ''
          }
        >
          {busy ? '…' : 'Render'}
        </button>

        <button
          className="t-btn"
          disabled={!notes.length}
          onClick={() => {
            onBeginEdit();
            onNotesChange([]);
          }}
        >
          Clear
        </button>

        <span className="midi-devices">
          {error || (devices.length ? devices.map((d) => d.name).join(', ') : 'no controller')}
        </span>
      </div>

      <PianoRoll
        notes={notes}
        onChange={onNotesChange}
        onBeginEdit={onBeginEdit}
        bars={bars}
        beatsPerBar={beatsPerBar}
        activePitches={active}
        playhead={playhead}
      />
    </div>
  );
}
