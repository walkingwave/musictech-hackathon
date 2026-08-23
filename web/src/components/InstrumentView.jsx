import { useState } from 'react';
import Section from './Section.jsx';
import { matchPrompt } from '../soundfonts.js';

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

export default function InstrumentView({ onCreate, onRemove, instruments = [] }) {
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  // Live feedback on where the sound will come from: a named instrument
  // maps to a real multisampled library; anything else is generated.
  const matched = matchPrompt(prompt);

  const create = (e) => {
    e.preventDefault();
    if (!prompt.trim()) return;
    onCreate({ name, prompt });
    setName('');
    setPrompt('');
  };

  return (
    <main className="instrument">
      <Section num="01" title="DESCRIBE THE INSTRUMENT">
        <p className="hint">
          Instruments are sounds you can load into any MIDI track, and swap
          without touching the notes. Name a real instrument and it plays
          through a real sample library; describe something stranger and
          Stable Audio 3 invents it. Either way your notes play back exactly
          as written.
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
            Add to library
          </button>
        </form>

        {prompt.trim() && (
          <p className="hint">
            {matched
              ? `“${prompt.trim()}” plays through the real ${matched.replace(/_/g, ' ')} sample library.`
              : 'No known instrument in that description — the sound will be AI-generated. Name an instrument (cello, flute, rhodes…) to use real samples.'}
          </p>
        )}

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

      <Section num="02" title="LIBRARY">
        <div className="instrument-list">
          {instruments.map((i) => (
            <div key={i.id} className="instrument-row">
              <span className="instrument-name">{i.name}</span>
              <span className="instrument-prompt">{i.prompt}</span>
              {i.id.startsWith('f-') ? (
                <span className="instrument-notes">factory</span>
              ) : (
                <button className="instrument-x" onClick={() => onRemove(i.id)} title="remove">
                  ×
                </button>
              )}
            </div>
          ))}
        </div>
      </Section>
    </main>
  );
}
