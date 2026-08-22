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
  fileName,
  backends,
  backend,
  onBackend,
  prompt,
  onPrompt,
  selected,
  onToggleStem,
  onSubmitVocal,
  onGenerate,
  generating,
}) {
  return (
    <div className="view">
      <Section num="01" title="Source Audio" meta="WAV · MP3 · M4A">
        <Recorder onSubmit={onSubmitVocal} fileName={fileName} />
      </Section>

      <Section num="02" title="Backing Track Prompt" meta="Free Text">
        <textarea
          placeholder="Warm neo-soul backing: brushed drums, upright-ish bass, mellow Rhodes comping…"
          value={prompt}
          onChange={(e) => onPrompt(e.target.value)}
        />
        <p className="help">One description applies to all generated stems.</p>
      </Section>

      {analysis && (
        <Settings
          analysis={analysis}
          backends={backends}
          backend={backend}
          onBackend={onBackend}
          selected={selected}
          onToggleStem={onToggleStem}
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
  selected,
  onToggleStem,
  onGenerate,
  generating,
}) {
  const [bpm, setBpm] = useState(analysis.bpm);
  const [key, setKey] = useState(analysis.key);
  const [mode, setMode] = useState(analysis.mode);
  const [chords, setChords] = useState(analysis.bars.map((b) => b.chord));

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
    onGenerate(edit);
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

          <div className="field">
            <span className="field-label">Backend</span>
            <select value={backend} onChange={(e) => onBackend(e.target.value)}>
              {backends.map((b) => (
                <option key={b.id} value={b.id} disabled={!b.available}>
                  {b.label}
                  {b.available ? '' : ' — unavailable'}
                </option>
              ))}
            </select>
          </div>

          <div className="field divide">
            <span className="field-label">Stems</span>
            <div className="chips">
              {STEMS.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  className={`chip${selected.has(s.id) ? ' on' : ''}`}
                  onClick={() => onToggleStem(s.id)}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="field" style={{ marginTop: '1.4rem' }}>
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
          disabled={generating || selected.size === 0}
          onClick={generate}
        >
          {generating ? 'Generating…' : 'Generate'}
        </button>
        <p>
          BPM, key, and chords can be edited before generating. Stems appear in
          the Tracks view as they finish.
        </p>
      </div>
    </>
  );
}
