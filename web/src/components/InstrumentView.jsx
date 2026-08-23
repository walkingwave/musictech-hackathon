import { useState } from 'react';
import Section from './Section.jsx';

// Define an instrument by describing it, then go and play it.
//
// This view owns only the *sound*. The notes are written in the studio, on
// the MIDI track this creates — a piano roll belongs next to the rest of
// the arrangement, not on a page of its own where you cannot hear what it
// has to fit against.

const PRESETS = [
  { name: 'Synth lead', prompt: 'warm analog synth lead, slight detune, round attack' },
  { name: 'Nylon guitar', prompt: 'plucked nylon-string guitar, close-miked, intimate' },
  { name: 'Electric piano', prompt: 'glassy electric piano, light chorus, soft velocity' },
  { name: 'Cello', prompt: 'bowed cello, expressive vibrato, rosin and body' },
  { name: 'Flute', prompt: 'breathy wooden flute, airy tone' },
  { name: 'Sub bass', prompt: 'gritty distorted synth bass, analog filter' },
  { name: 'Vibraphone', prompt: 'vibraphone, motor vibrato, soft mallets' },
  { name: 'Choir', prompt: 'warm choir pad, sustained aahs, cathedral space' },
];

export default function InstrumentView({ onCreate, existing = [] }) {
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');

  const create = (e) => {
    e.preventDefault();
    if (!prompt.trim()) return;
    onCreate({ name: name.trim() || prompt.trim().split(/[ ,]/)[0], prompt: prompt.trim() });
  };

  return (
    <main className="instrument">
      <Section num="01" title="DESCRIBE THE INSTRUMENT">
        <p className="hint">
          Describe a sound and it becomes a MIDI track in the studio. Play it
          from your controller or draw it in the piano roll there, then render
          it — Stable Audio 3 keeps your performance and supplies the timbre.
        </p>

        <form className="row" onSubmit={create}>
          <input
            className="insp-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="e.g. hammered dulcimer, bright and metallic"
            autoFocus
          />
          <label className="studio-field">
            name
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="optional"
            />
          </label>
          <button className="primary" disabled={!prompt.trim()}>
            Create instrument
          </button>
        </form>

        <div className="preset-row">
          {PRESETS.map((p) => (
            <button
              key={p.name}
              className="preset"
              type="button"
              onClick={() => {
                setName(p.name);
                setPrompt(p.prompt);
              }}
            >
              {p.name}
            </button>
          ))}
        </div>
      </Section>

      {existing.length > 0 && (
        <Section num="02" title="IN THIS PROJECT">
          <div className="instrument-list">
            {existing.map((t) => (
              <div key={t.id} className="instrument-row">
                <span className="instrument-name">{t.name}</span>
                <span className="instrument-prompt">
                  {t.instrument || <em>no sound described yet</em>}
                </span>
                <span className="instrument-notes">
                  {(t.clips?.[0]?.notes?.length ?? 0)} notes
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </main>
  );
}
