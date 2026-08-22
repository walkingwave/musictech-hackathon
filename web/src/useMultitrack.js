import { useCallback, useEffect, useRef, useState } from 'react';

// Web Audio multitrack transport: every stem plus the vocal decoded into
// one AudioContext and started at a single shared time, so they stay in
// sync. Mute/solo/volume are recomputed live on the gain nodes.
export function useMultitrack() {
  const ctxRef = useRef(null);
  const buffersRef = useRef({}); // name -> AudioBuffer
  const gainsRef = useRef({}); // name -> GainNode (only while playing)
  const sourcesRef = useRef([]);

  const [tracks, setTracks] = useState([]); // ordered names with a buffer
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(() => new Set());
  const [soloed, setSoloed] = useState(() => new Set());
  const [volumes, setVolumes] = useState({}); // name -> 0..1.5

  const context = useCallback(() => {
    if (!ctxRef.current) ctxRef.current = new AudioContext();
    return ctxRef.current;
  }, []);

  const loadBuffer = useCallback(
    async (name, url) => {
      const bytes = await (await fetch(url)).arrayBuffer();
      buffersRef.current[name] = await context().decodeAudioData(bytes);
      setTracks((prev) => (prev.includes(name) ? prev : [...prev, name]));
      setVolumes((prev) => (name in prev ? prev : { ...prev, [name]: 1 }));
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

  const stop = useCallback(() => {
    sourcesRef.current.forEach((source) => {
      try {
        source.stop();
      } catch {
        /* already stopped */
      }
    });
    sourcesRef.current = [];
    gainsRef.current = {};
    setPlaying(false);
  }, []);

  const play = useCallback(() => {
    stop();
    const ctx = context();
    ctx.resume();
    const startAt = ctx.currentTime + 0.1;

    Object.entries(buffersRef.current).forEach(([name, buffer]) => {
      const source = ctx.createBufferSource();
      const gain = ctx.createGain();
      source.buffer = buffer;
      source.connect(gain).connect(ctx.destination);
      source.start(startAt); // one shared start time keeps stems in sync
      gainsRef.current[name] = gain;
      sourcesRef.current.push(source);
    });

    setPlaying(true);
    applyGains();
  }, [context, stop, applyGains]);

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

  return {
    tracks,
    playing,
    muted,
    soloed,
    volumes,
    loadBuffer,
    play,
    stop,
    toggleMute,
    toggleSolo,
    setVolume,
  };
}
