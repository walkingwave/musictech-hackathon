import { useEffect, useState } from 'react';
import Section from './Section.jsx';
import Recorder from './Recorder.jsx';

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
// Backend parts, with display labels matching the mockup's chip language.
const STEMS = [
  { id: 'bass', label: 'Bass' },
  { id: 'drums', label: 'Drums' },
  { id: 'piano', label: 'Piano' },
  { id: 'harmony', label: 'Harmony' },
];

export default function InputView({
  analysis,
  pitchTracking,
  fileName,
  backends,
  backend,
  onBackend,
  prompt,
  onPrompt,
  target,
  onTarget,
  bars,
  onBars,
  onSubmitVocal,
  onGenerate,
  generating,
}) {
  return (
    <div className="view">
      <Section num="01" title="Hum Input" meta="ONE CLEAR VOICE">
        <Recorder onSubmit={onSubmitVocal} fileName={fileName} />
      </Section>

      <Section num="02" title="MIDI Transformation" meta="EDITABLE NOTES">
        <p className="help">Hum one unaccompanied line; Melody preserves detected notes while Bassline intentionally rearranges them.</p>
        {pitchTracking && <p className="help">{pitchTracking.note_count} detected notes · {pitchTracking.tracker_id}{pitchTracking.diagnostics?.warnings?.length ? ` · ${pitchTracking.diagnostics.warnings[0]}` : ''}</p>}
      </Section>

      {analysis && (
        <Settings
          analysis={analysis}
          backends={backends}
          backend={backend}
          onBackend={onBackend}
          target={target}
          onTarget={onTarget}
          bars={bars}
          onBars={onBars}
          onGenerate={onGenerate}
          generating={generating}
        />
      )}
    </div>
  );
}

function Settings({
  analysis,
  backends,
  backend,
  onBackend,
  target,
  onTarget,
  bars,
  onBars,
  onGenerate,
  generating,
}) {
  const [bpm, setBpm] = useState(analysis.bpm);
  const [key, setKey] = useState(analysis.key);
  const [mode, setMode] = useState(analysis.mode);
  const [chords, setChords] = useState(analysis.bars.map((b) => b.chord));
  const [snapToKey, setSnapToKey] = useState(false);
  const [quantize, setQuantize] = useState(false);

  useEffect(() => {
    setBpm(analysis.bpm);
    setKey(analysis.key);
    setMode(analysis.mode);
    setChords(analysis.bars.map((b) => b.chord));
  }, [analysis]);

  const generate = () => {
    // Chords are sized to the current bar grid; the server rejects a stale
    // list when the tempo changes, so only send them when tempo is unchanged.
    const tempoChanged = Math.abs(Number(bpm) - analysis.bpm) > 0.05;
    const edit = { bpm: Number(bpm), key, mode };
    if (!tempoChanged) edit.chords = chords;
    onGenerate(edit, { faithful: target === 'melody', snap_to_key: snapToKey, quantize });
  };

  return (
    <>
      <Section num="03" title="Settings" meta="Detected From Source">
        <div className="settings">
          <div className="field">
            <span className="field-label">
              Tempo <span className="badge">Detected</span>
            </span>
            <div className="row">
              <input
                type="number"
                step="0.1"
                value={bpm}
                onChange={(e) => setBpm(e.target.value)}
              />
              <span className="unit">BPM</span>
            </div>
          </div>

          <div className="field divide">
            <span className="field-label">
              Key <span className="badge">Detected</span>
            </span>
            <div className="row">
              <select value={key} onChange={(e) => setKey(e.target.value)}>
                {NOTE_NAMES.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="major">major</option>
                <option value="minor">minor</option>
              </select>
            </div>
          </div>


          <div className="field divide">
            <span className="field-label">Transform Hum Into</span>
            <div className="chips">
              {[{ id: 'melody', label: 'Melody' }, { id: 'bass', label: 'Bassline' }].map((option) => (
                <button
                  key={option.id}
                  type="button"
                  className={`chip${target === option.id ? ' on' : ''}`}
                  onClick={() => onTarget(option.id)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="field" style={{ marginTop: '1.4rem' }}>
          {target === 'melody' && (
            <div className="row" style={{ marginBottom: '0.8rem' }}>
              <label><input type="checkbox" checked={snapToKey} onChange={(e) => setSnapToKey(e.target.checked)} /> Snap pitches to key</label>
              <label><input type="checkbox" checked={quantize} onChange={(e) => setQuantize(e.target.checked)} /> Quantize timing</label>
            </div>
          )}
          <span className="field-label">Chords · One Per Bar</span>
          <div className="chord-grid">
            {chords.map((chord, i) => (
              <input
                key={i}
                value={chord}
                title={`Bar ${i + 1}`}
                onChange={(e) => {
                  const next = [...chords];
                  next[i] = e.target.value;
                  setChords(next);
                }}
              />
            ))}
          </div>
        </div>
      </Section>

      <div className="generate-bar">
        <button
          type="button"
          className="solid"
          disabled={generating}
          onClick={generate}
        >
          {generating ? 'Transforming…' : 'Transform Hum to MIDI'}
        </button>
        <p>
          Melody defaults to faithful detected pitch and timing. Key snapping and
          quantization are optional; Bassline intentionally reinterprets the hum.
        </p>
      </div>
    </>
  );
}
