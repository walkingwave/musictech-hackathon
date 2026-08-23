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
            // Takes are not generated at a requested pitch — each lands
            // wherever it lands and reports it, and playback transposes
            // from there.
            actualPitch: s.actual_pitch,
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

  /**
   * Sound a note now and hold it until released. Used for live playing,
   * where the length is not known when the key goes down — `play` cannot
   * serve that case because it needs a duration up front.
   *
   * Returns a handle whose `stop()` ramps the note out.
   */
  const noteOn = useCallback(
    (instrument, pitch, { gain = 1, destination } = {}) => {
      const entry = loadedRef.current.get(instrument?.id);
      if (!entry?.samples.length) return null;

      const ctx = context();
      ctx.resume();
      const sample = pick(entry.samples, pitch);
      const source = ctx.createBufferSource();
      source.buffer = sample.buffer;
      source.playbackRate.value = 2 ** ((pitch - sample.actualPitch) / 12);
      // Hold by looping: a three-second one-shot would otherwise stop
      // dead under a longer press. Loop from past the attack so the
      // transient is not retriggered, but clamp it — trimming can leave a
      // sample short enough that a fixed loop start would sit beyond its
      // end, which silently produces no sound at all.
      const attack = Math.min(0.25, sample.buffer.duration * 0.25);
      source.loop = true;
      source.loopStart = attack;
      source.loopEnd = sample.buffer.duration;

      const env = ctx.createGain();
      env.gain.setValueAtTime(0, ctx.currentTime);
      env.gain.linearRampToValueAtTime(gain, ctx.currentTime + 0.005);
      source.connect(env).connect(destination || ctx.destination);
      source.start();

      return {
        stop() {
          const now = ctx.currentTime;
          const release = 0.12;
          env.gain.cancelScheduledValues(now);
          env.gain.setValueAtTime(env.gain.value, now);
          env.gain.linearRampToValueAtTime(0.0001, now + release);
          // Stop after the ramp, not on it, or the release is cut off and
          // clicks — the thing the ramp exists to prevent.
          source.stop(now + release + 0.02);
        },
      };
    },
    [context],
  );

  return { load, play, noteOn, isLoaded, loading, context };
}
