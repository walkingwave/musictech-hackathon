import { useCallback, useEffect, useState } from 'react';

// The instrument library — the equivalent of Ableton's device browser.
//
// An instrument is just a named prompt. It is not tied to any track: you
// build a library, then load one into a MIDI track's instrument slot, and
// you can swap it later without touching the notes. That separation is the
// whole point — the same part can be auditioned through several sounds.

const STORAGE_KEY = 'btg.instruments';

// Written for a sampler, which is a narrower brief than it looks: each
// has to be one instrument, playing one note, that can be transposed. The
// backend appends the "single note, monophonic, dry" constraints, so these
// describe only the sound. Words like "chord", "ensemble" or "pad" are
// avoided on purpose — there is no single pitch in a chord to sample, and
// the ones that named one produced samples whose pitch wandered an octave.
const FACTORY = [
  { id: 'f-flute', name: 'Flute', prompt: 'wooden concert flute, breathy airy tone' },
  { id: 'f-nylon', name: 'Nylon Guitar', prompt: 'nylon-string classical guitar, warm fingerpicked' },
  { id: 'f-cello', name: 'Cello', prompt: 'bowed cello, warm and woody, rosin on the string' },
  { id: 'f-rhodes', name: 'Electric Piano', prompt: 'electric piano, glassy bell-like tine' },
  { id: 'f-vibes', name: 'Vibraphone', prompt: 'vibraphone, struck metal bar, soft mallet' },
  { id: 'f-voice', name: 'Voice', prompt: 'single female voice singing aah, warm and clear' },
  { id: 'f-lead', name: 'Analog Lead', prompt: 'analog synthesizer lead, warm sawtooth, slight detune' },
  { id: 'f-bass', name: 'Synth Bass', prompt: 'analog synth bass, round low tone, gentle filter' },
  { id: 'f-organ', name: 'Organ', prompt: 'drawbar electric organ, steady tone' },
  { id: 'f-trumpet', name: 'Trumpet', prompt: 'muted trumpet, brassy and focused' },
  { id: 'f-marimba', name: 'Marimba', prompt: 'marimba, deep wooden bar struck with a soft mallet' },
  { id: 'f-harp', name: 'Harp', prompt: 'concert harp, plucked string ringing' },
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
