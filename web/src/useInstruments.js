import { useCallback, useEffect, useState } from 'react';

// The instrument library — the equivalent of Ableton's device browser.
//
// An instrument is just a named prompt. It is not tied to any track: you
// build a library, then load one into a MIDI track's instrument slot, and
// you can swap it later without touching the notes. That separation is the
// whole point — the same part can be auditioned through several sounds.

const STORAGE_KEY = 'btg.instruments';

const FACTORY = [
  { id: 'f-lead', name: 'Analog Lead', prompt: 'warm analog synth lead, slight detune, round attack' },
  { id: 'f-nylon', name: 'Nylon Guitar', prompt: 'plucked nylon-string guitar, close-miked, intimate' },
  { id: 'f-rhodes', name: 'Electric Piano', prompt: 'glassy electric piano, light chorus, soft velocity' },
  { id: 'f-cello', name: 'Cello', prompt: 'bowed cello, expressive vibrato, rosin and body' },
  { id: 'f-flute', name: 'Flute', prompt: 'breathy wooden flute, airy tone' },
  { id: 'f-bass', name: 'Synth Bass', prompt: 'gritty distorted synth bass, analog filter' },
  { id: 'f-vibes', name: 'Vibraphone', prompt: 'vibraphone, motor vibrato, soft mallets' },
  { id: 'f-choir', name: 'Choir', prompt: 'warm choir pad, sustained aahs, cathedral space' },
];

let counter = 0;
const uid = () => `i-${Date.now().toString(36)}-${++counter}`;

export function useInstruments() {
  const [instruments, setInstruments] = useState(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
      // Factory instruments are merged in rather than stored, so a later
      // release can add to them without every existing project missing out.
      const custom = (stored || []).filter((i) => !i.id.startsWith('f-'));
      return [...FACTORY, ...custom];
    } catch {
      return [...FACTORY];
    }
  });

  useEffect(() => {
    const custom = instruments.filter((i) => !i.id.startsWith('f-'));
    localStorage.setItem(STORAGE_KEY, JSON.stringify(custom));
  }, [instruments]);

  const create = useCallback(({ name, prompt }) => {
    const instrument = {
      id: uid(),
      name: name?.trim() || prompt.trim().split(/[ ,]/)[0],
      prompt: prompt.trim(),
    };
    setInstruments((prev) => [...prev, instrument]);
    return instrument;
  }, []);

  const update = useCallback((id, patch) => {
    setInstruments((prev) => prev.map((i) => (i.id === id ? { ...i, ...patch } : i)));
  }, []);

  const remove = useCallback((id) => {
    setInstruments((prev) => prev.filter((i) => i.id !== id));
  }, []);

  return { instruments, create, update, remove };
}
