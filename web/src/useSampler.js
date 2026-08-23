import { useCallback, useRef, useState } from 'react';
import * as apiClient from './api.js';

// A sampler: a handful of generated one-shots, pitch-shifted to play
// whatever MIDI asks for.
//
// This is what keeps the notes exactly as played. Asking the model to
// render a whole melody does not work — it rewrites the tune — so it only
// makes one note at a time and the browser does the playing.
//
// A nice side effect: editing needs no generation at all. Once an
// instrument is sampled, moving notes around is instant.

export function useSampler() {
  // instrumentId -> { samples: [{pitch, actualPitch, buffer}] }
  const loadedRef = useRef(new Map());
  const ctxRef = useRef(null);
  const [loading, setLoading] = useState(null);

  const context = useCallback(() => {
    if (!ctxRef.current) ctxRef.current = new AudioContext();
    return ctxRef.current;
  }, []);

  const isLoaded = useCallback((instrument) => {
    return instrument ? loadedRef.current.has(instrument.id) : false;
  }, []);

  // Generate (or fetch from cache) the one-shots for an instrument and
  // decode them ready to play.
  const load = useCallback(
    async (instrument, { backend } = {}) => {
      if (!instrument?.prompt) throw new Error('this instrument has no description');
      if (loadedRef.current.has(instrument.id)) return loadedRef.current.get(instrument.id);

      setLoading(instrument.id);
      try {
        const { samples } = await apiClient.instrumentSamples({
          prompt: instrument.prompt,
          backend,
        });

        const decoded = await Promise.all(
          samples.map(async (s) => ({
            pitch: s.pitch,
            // What the sample actually sounds. Transposing from the
            // requested pitch would be a semitone-perfect way to be wrong
            // whenever the model drifted an octave.
            actualPitch: s.actual_pitch ?? s.pitch,
            buffer: await context().decodeAudioData(
              await (await fetch(s.url)).arrayBuffer(),
            ),
          })),
        );

        const entry = { samples: decoded };
        loadedRef.current.set(instrument.id, entry);
        return entry;
      } finally {
        setLoading(null);
      }
    },
    [context],
  );

  // The sample whose true pitch is nearest, so nothing is stretched
  // further than it has to be.
  const pick = (samples, pitch) =>
    samples.reduce((best, s) =>
      Math.abs(s.actualPitch - pitch) < Math.abs(best.actualPitch - pitch) ? s : best,
    );

  /**
   * Schedule one note. `when` and `duration` are in AudioContext seconds.
   * Returns the source so a caller can stop it early.
   */
  const play = useCallback(
    (instrument, pitch, when, duration, { gain = 1, destination } = {}) => {
      const entry = loadedRef.current.get(instrument?.id);
      if (!entry?.samples.length) return null;

      const ctx = context();
      const sample = pick(entry.samples, pitch);
      const source = ctx.createBufferSource();
      source.buffer = sample.buffer;
      source.playbackRate.value = 2 ** ((pitch - sample.actualPitch) / 12);

      // Short attack and release. Without the release a note cut shorter
      // than its sample ends on a click.
      const env = ctx.createGain();
      const release = Math.min(0.06, duration / 2);
      env.gain.setValueAtTime(0, when);
      env.gain.linearRampToValueAtTime(gain, when + 0.005);
      env.gain.setValueAtTime(gain, when + duration - release);
      env.gain.linearRampToValueAtTime(0.0001, when + duration);

      source.connect(env).connect(destination || ctx.destination);
      source.start(when);
      source.stop(when + duration + 0.02);
      return source;
    },
    [context],
  );

  return { load, play, isLoaded, loading, context };
}
