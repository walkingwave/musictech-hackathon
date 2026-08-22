import { useCallback, useEffect, useRef, useState } from 'react';
import PianoRoll from './PianoRoll.jsx';
import Section from './Section.jsx';
import { useMidiInput } from '../useMidiInput.js';

// Play it, then describe it. The notes become the guide track, so the
// performance is preserved exactly and Stable Audio 3 only supplies the
// sound — which is the whole idea, applied to your own playing rather
// than to an arranger's.

let counter = 0;
const uid = () => `p-${++counter}`;

const PRESETS = [
  'warm analog synth lead, slight detune',
  'plucked nylon guitar, close-miked',
  'glassy electric piano, light chorus',
  'bowed cello, expressive vibrato',
  'breathy wooden flute',
  'gritty distorted synth bass',
];

export default function InstrumentView({
  bpm = 100,
  bars = 4,
  onGenerate, // ({notes, prompt, name, bars}) -> result
  busy,
}) {
  const [notes, setNotes] = useState([]);
  const [prompt, setPrompt] = useState(PRESETS[0]);
  const [name, setName] = useState('instrument');
  const [recording, setRecording] = useState(false);
  const [playhead, setPlayhead] = useState(null);
  const [status, setStatus] = useState('');

  const beatsPerBar = 4;
  const secondsPerBeat = 60 / (bpm || 100);
  const recordStartRef = useRef(0);
  const ctxRef = useRef(null);

  const audio = () => {
    if (!ctxRef.current) ctxRef.current = new AudioContext();
    return ctxRef.current;
  };

  // A short blip so you hear the controller immediately. This is only
  // monitoring — the real sound comes from the model.
  const blip = useCallback((pitch, velocity = 90) => {
    const ctx = audio();
    ctx.resume();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'triangle';
    osc.frequency.value = 440 * 2 ** ((pitch - 69) / 12);
    gain.gain.setValueAtTime((velocity / 127) * 0.22, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.36);
  }, []);

  // A played note lands here on release, when its length is finally known.
  const onPlayedNote = useCallback(
    ({ pitch, velocity, startedAt, endedAt }) => {
      blip(pitch, velocity);
      if (!recording) return;

      const startBeat = (startedAt - recordStartRef.current) / 1000 / secondsPerBeat;
      const lengthBeat = Math.max(0.25, (endedAt - startedAt) / 1000 / secondsPerBeat);
      const snap = (b) => Math.max(0, Math.round(b / 0.25) * 0.25);

      setNotes((prev) => [
        ...prev,
        { id: uid(), pitch, velocity, start: snap(startBeat), length: snap(lengthBeat) },
      ]);
    },
    [recording, secondsPerBeat, blip],
  );

  const { devices, active, error, panic } = useMidiInput({ onNote: onPlayedNote });

  // Playhead while recording, so there is something to play against.
  useEffect(() => {
    if (!recording) {
      setPlayhead(null);
      return undefined;
    }
    recordStartRef.current = performance.now();
    let frame;
    const tick = () => {
      const beats = (performance.now() - recordStartRef.current) / 1000 / secondsPerBeat;
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
  }, [recording, secondsPerBeat, bars, panic]);

  // Preview what is on the roll with the monitoring blip.
  const preview = () => {
    const ctx = audio();
    ctx.resume();
    notes.forEach((note) => {
      window.setTimeout(
        () => blip(note.pitch, note.velocity),
        note.start * secondsPerBeat * 1000,
      );
    });
  };

  const generate = async () => {
    if (!notes.length) {
      setStatus('Play or draw some notes first.');
      return;
    }
    setStatus('Generating…');
    try {
      await onGenerate({
        notes: notes.map(({ pitch, start, length, velocity }) => ({
          pitch,
          start,
          length,
          velocity,
        })),
        prompt,
        name,
        bars,
      });
      setStatus('Added to the timeline.');
    } catch (e) {
      setStatus(`Failed — ${e.message}`);
    }
  };

  return (
    <main className="instrument">
      <Section num="01" title="PLAY OR DRAW THE PART">
        <div className="row midi-row">
          <button
            className={`primary${recording ? ' recording' : ''}`}
            onClick={() => {
              if (recording) panic();
              setRecording((v) => !v);
            }}
          >
            {recording ? 'Stop' : 'Record from controller'}
          </button>
          <button onClick={preview} disabled={!notes.length}>
            Preview
          </button>
          <button onClick={() => setNotes([])} disabled={!notes.length}>
            Clear
          </button>
          <span className="midi-status">
            {error
              ? error
              : devices.length
                ? `${devices.map((d) => d.name).join(', ')} connected`
                : 'No MIDI controller detected — you can still draw notes'}
          </span>
        </div>
        <p className="hint">
          Click the grid to add a note, drag to move it, drag its right edge to
          resize, alt-click to delete.
        </p>
        <PianoRoll
          notes={notes}
          onChange={setNotes}
          bars={bars}
          beatsPerBar={beatsPerBar}
          activePitches={active}
          playhead={playhead}
        />
      </Section>

      <Section num="02" title="DESCRIBE THE INSTRUMENT">
        <div className="row">
          <input
            className="insp-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="what should these notes sound like?"
          />
          <label className="studio-field">
            name
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <button className="primary" onClick={generate} disabled={busy || !notes.length}>
            {busy ? 'Generating…' : 'Create instrument'}
          </button>
        </div>
        <div className="preset-row">
          {PRESETS.map((p) => (
            <button key={p} className="preset" onClick={() => setPrompt(p)}>
              {p.split(',')[0]}
            </button>
          ))}
        </div>
        <div className="studio-status">{status}</div>
      </Section>
    </main>
  );
}
