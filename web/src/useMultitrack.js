import { useCallback, useEffect, useRef, useState } from 'react';

// Web Audio multitrack transport: every stem plus the vocal decoded into
// one AudioContext and started at a single shared time, so they stay in
// sync. Mute/solo/volume are recomputed live on the gain nodes. Also tracks
// a shared playhead and supports seeking, for the DAW-style track view.
export function useMultitrack() {
  const ctxRef = useRef(null);
  const buffersRef = useRef({}); // name -> AudioBuffer
  const gainsRef = useRef({}); // name -> GainNode (only while playing)
  const sourcesRef = useRef([]);
  const startAtRef = useRef(0); // ctx time the current playback started
  const offsetRef = useRef(0); // seconds into the material at that start
  const rafRef = useRef(null);

  const [tracks, setTracks] = useState([]); // ordered names with a buffer
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(() => new Set());
  const [soloed, setSoloed] = useState(() => new Set());
  const [volumes, setVolumes] = useState({}); // name -> 0..1.5
  const [duration, setDuration] = useState(0);
  const [position, setPosition] = useState(0); // playhead, seconds

  const context = useCallback(() => {
    if (!ctxRef.current) ctxRef.current = new AudioContext();
    return ctxRef.current;
  }, []);

  const loadBuffer = useCallback(
    async (name, url) => {
      const bytes = await (await fetch(url)).arrayBuffer();
      const buffer = await context().decodeAudioData(bytes);
      buffersRef.current[name] = buffer;
      setTracks((prev) => (prev.includes(name) ? prev : [...prev, name]));
      setVolumes((prev) => (name in prev ? prev : { ...prev, [name]: 1 }));
      setDuration((d) => Math.max(d, buffer.duration));
    },
    [context],
  );

  // A track is audible unless muted, or something else is soloed and it is
  // not. Recomputed on every relevant state change so it works mid-playback.
  const applyGains = useCallback(() => {
    const soloActive = soloed.size > 0;
    Object.entries(gainsRef.current).forEach(([name, gain]) => {
      const audible = !muted.has(name) && (!soloActive || soloed.has(name));
      gain.gain.value = audible ? (volumes[name] ?? 1) : 0;
    });
  }, [muted, soloed, volumes]);

  useEffect(() => {
    applyGains();
  }, [applyGains]);

  const stopSources = useCallback(() => {
    sourcesRef.current.forEach((source) => {
      try {
        source.stop();
      } catch {
        /* already stopped */
      }
    });
    sourcesRef.current = [];
    gainsRef.current = {};
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
  }, []);

  // Full stop: reset the playhead to the start.
  const stop = useCallback(() => {
    stopSources();
    offsetRef.current = 0;
    setPosition(0);
    setPlaying(false);
  }, [stopSources]);

  // Pause without moving the playhead — leaves position where it is.
  const pause = useCallback(() => {
    const ctx = context();
    offsetRef.current += Math.max(0, ctx.currentTime - startAtRef.current);
    stopSources();
    setPlaying(false);
  }, [context, stopSources]);

  const play = useCallback(
    (fromOffset) => {
      stopSources();
      const ctx = context();
      ctx.resume();
      const offset = fromOffset != null ? fromOffset : offsetRef.current;
      offsetRef.current = offset;
      const startAt = ctx.currentTime + 0.05;
      startAtRef.current = startAt;

      Object.entries(buffersRef.current).forEach(([name, buffer]) => {
        const source = ctx.createBufferSource();
        const gain = ctx.createGain();
        source.buffer = buffer;
        source.connect(gain).connect(ctx.destination);
        // Shared start time + shared offset keeps every stem in sync.
        source.start(startAt, Math.min(offset, buffer.duration));
        gainsRef.current[name] = gain;
        sourcesRef.current.push(source);
      });

      setPlaying(true);
      applyGains();

      const tick = () => {
        const pos = offsetRef.current + Math.max(0, ctx.currentTime - startAtRef.current);
        if (pos >= duration) {
          stop();
          return;
        }
        setPosition(pos);
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
    },
    [context, stopSources, applyGains, duration, stop],
  );

  // Move the playhead; keep playing from there if we were playing.
  const seek = useCallback(
    (time) => {
      const t = Math.max(0, Math.min(time, duration));
      offsetRef.current = t;
      setPosition(t);
      if (playing) play(t);
    },
    [duration, playing, play],
  );

  const toggleMute = useCallback((name) => {
    setMuted((prev) => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });
  }, []);

  const toggleSolo = useCallback((name) => {
    setSoloed((prev) => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });
  }, []);

  const setVolume = useCallback((name, value) => {
    setVolumes((prev) => ({ ...prev, [name]: value }));
  }, []);

  const getBuffer = useCallback((name) => buffersRef.current[name], []);

  return {
    tracks,
    playing,
    muted,
    soloed,
    volumes,
    duration,
    position,
    loadBuffer,
    getBuffer,
    play,
    pause,
    stop,
    seek,
    toggleMute,
    toggleSolo,
    setVolume,
  };
}
