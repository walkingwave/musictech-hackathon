import { useCallback, useMemo, useRef, useState } from 'react';

// A clip-based multitrack timeline. Tracks hold clips; each clip is a decoded
// AudioBuffer placed at an arbitrary start time, so a backing stem can be
// longer than the input vocal and the vocal can sit anywhere on the timeline.
// Playback schedules every clip against one shared transport clock.
//
// AudioBuffers are not serializable, so they live in a ref keyed by clip id;
// clip *metadata* (position, length, provenance) lives in React state.
//
// Track:  { id, name, kind, muted, soloed, volume, clips: [Clip] }
// Clip:   { id, start, duration, offset, part, prompt, seed, backendUsed }
//   start    seconds on the timeline where the clip begins
//   offset   seconds into the buffer where playback starts (for split clips)
//   duration seconds of the buffer to play from offset

let counter = 0;
const uid = (p) => `${p}-${++counter}`;

export function useTimeline() {
  const ctxRef = useRef(null);
  const buffersRef = useRef({}); // clipId -> AudioBuffer
  const nodesRef = useRef([]); // active source nodes during playback
  const gainsRef = useRef({}); // trackId -> GainNode during playback
  const startAtRef = useRef(0);
  const offsetRef = useRef(0);
  const rafRef = useRef(null);

  const [tracks, setTracks] = useState([]);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [loop, setLoopState] = useState(null); // {a, b} timeline seconds or null
  const loopRef = useRef(null);
  const setLoop = useCallback((region) => {
    loopRef.current = region;
    setLoopState(region);
  }, []);

  const context = useCallback(() => {
    if (!ctxRef.current) ctxRef.current = new (window.AudioContext || window.webkitAudioContext)();
    return ctxRef.current;
  }, []);

  const duration = useMemo(() => {
    let max = 0;
    for (const t of tracks)
      for (const c of t.clips) max = Math.max(max, c.start + c.duration);
    return max;
  }, [tracks]);

  const getBuffer = useCallback((clipId) => buffersRef.current[clipId], []);

  // --- structure ---------------------------------------------------------

  const addTrack = useCallback((name, kind) => {
    const id = uid('trk');
    setTracks((prev) => [
      ...prev,
      { id, name, kind: kind || 'audio', muted: false, soloed: false, volume: 1, clips: [] },
    ]);
    return id;
  }, []);

  const addClip = useCallback((trackId, buffer, meta = {}) => {
    const id = uid('clip');
    buffersRef.current[id] = buffer;
    const clip = {
      id,
      start: meta.start ?? 0,
      offset: meta.offset ?? 0,
      duration: meta.duration ?? buffer.duration,
      part: meta.part ?? null,
      prompt: meta.prompt ?? '',
      seed: meta.seed ?? null,
      backendUsed: meta.backendUsed ?? null,
      startBar: meta.startBar ?? 0,
    };
    setTracks((prev) =>
      prev.map((t) => (t.id === trackId ? { ...t, clips: [...t.clips, clip] } : t)),
    );
    return id;
  }, []);

  // Convenience: create a track for a part and drop one clip on it.
  const addTrackWithClip = useCallback(
    (name, kind, buffer, meta = {}) => {
      const trackId = uid('trk');
      const clipId = uid('clip');
      buffersRef.current[clipId] = buffer;
      const clip = {
        id: clipId,
        start: meta.start ?? 0,
        offset: meta.offset ?? 0,
        duration: meta.duration ?? buffer.duration,
        part: meta.part ?? null,
        prompt: meta.prompt ?? '',
        seed: meta.seed ?? null,
        backendUsed: meta.backendUsed ?? null,
        startBar: meta.startBar ?? 0,
      };
      setTracks((prev) => [
        ...prev,
        { id: trackId, name, kind: kind || 'audio', muted: false, soloed: false, volume: 1, clips: [clip] },
      ]);
      return { trackId, clipId };
    },
    [],
  );

  const moveClip = useCallback((trackId, clipId, newStart) => {
    setTracks((prev) =>
      prev.map((t) =>
        t.id === trackId
          ? {
              ...t,
              clips: t.clips.map((c) =>
                c.id === clipId ? { ...c, start: Math.max(0, newStart) } : c,
              ),
            }
          : t,
      ),
    );
  }, []);

  const removeClip = useCallback((trackId, clipId) => {
    delete buffersRef.current[clipId];
    setTracks((prev) =>
      prev.map((t) =>
        t.id === trackId ? { ...t, clips: t.clips.filter((c) => c.id !== clipId) } : t,
      ),
    );
  }, []);

  const removeTrack = useCallback((trackId) => {
    setTracks((prev) => {
      const t = prev.find((x) => x.id === trackId);
      if (t) t.clips.forEach((c) => delete buffersRef.current[c.id]);
      return prev.filter((x) => x.id !== trackId);
    });
  }, []);

  // Replace a time region [regStart, regEnd] (timeline seconds) of one clip
  // with a freshly generated buffer — the section-regenerate splice. Splits
  // the original into head / new / tail clips that share the same buffer.
  const replaceRegion = useCallback((trackId, clipId, regStart, regEnd, newBuffer, meta = {}) => {
    setTracks((prev) =>
      prev.map((t) => {
        if (t.id !== trackId) return t;
        const idx = t.clips.findIndex((c) => c.id === clipId);
        if (idx < 0) return t;
        const c = t.clips[idx];
        const clipEnd = c.start + c.duration;
        const a = Math.max(c.start, regStart);
        const b = Math.min(clipEnd, regEnd);
        const pieces = [];

        if (a > c.start) {
          const headId = uid('clip');
          buffersRef.current[headId] = buffersRef.current[c.id];
          pieces.push({ ...c, id: headId, start: c.start, offset: c.offset, duration: a - c.start });
        }
        const midId = uid('clip');
        buffersRef.current[midId] = newBuffer;
        pieces.push({
          id: midId,
          start: a,
          offset: 0,
          duration: meta.duration ?? newBuffer.duration,
          part: c.part,
          prompt: meta.prompt ?? c.prompt,
          seed: meta.seed ?? null,
          backendUsed: meta.backendUsed ?? null,
          startBar: meta.startBar ?? c.startBar ?? 0,
        });
        if (b < clipEnd) {
          const tailId = uid('clip');
          buffersRef.current[tailId] = buffersRef.current[c.id];
          pieces.push({
            ...c,
            id: tailId,
            start: b,
            offset: c.offset + (b - c.start),
            duration: clipEnd - b,
          });
        }
        const clips = [...t.clips.slice(0, idx), ...pieces, ...t.clips.slice(idx + 1)];
        return { ...t, clips };
      }),
    );
  }, []);

  // Patch arbitrary fields of one clip (used by edge-trim: start/offset/duration).
  const updateClip = useCallback((trackId, clipId, patch) => {
    setTracks((prev) =>
      prev.map((t) =>
        t.id === trackId
          ? { ...t, clips: t.clips.map((c) => (c.id === clipId ? { ...c, ...patch } : c)) }
          : t,
      ),
    );
  }, []);

  // Split one clip at a timeline time into two clips sharing the same buffer.
  const splitClip = useCallback((trackId, clipId, atTime) => {
    setTracks((prev) =>
      prev.map((t) => {
        if (t.id !== trackId) return t;
        const idx = t.clips.findIndex((c) => c.id === clipId);
        if (idx < 0) return t;
        const c = t.clips[idx];
        if (atTime <= c.start + 0.02 || atTime >= c.start + c.duration - 0.02) return t;
        const leftDur = atTime - c.start;
        const rightId = uid('clip');
        buffersRef.current[rightId] = buffersRef.current[c.id];
        const left = { ...c, duration: leftDur };
        const right = {
          ...c,
          id: rightId,
          start: atTime,
          offset: c.offset + leftDur,
          duration: c.duration - leftDur,
        };
        return { ...t, clips: [...t.clips.slice(0, idx), left, right, ...t.clips.slice(idx + 1)] };
      }),
    );
  }, []);

  const duplicateClip = useCallback((trackId, clipId) => {
    setTracks((prev) =>
      prev.map((t) => {
        if (t.id !== trackId) return t;
        const c = t.clips.find((x) => x.id === clipId);
        if (!c) return t;
        const copyId = uid('clip');
        buffersRef.current[copyId] = buffersRef.current[c.id];
        return { ...t, clips: [...t.clips, { ...c, id: copyId, start: c.start + c.duration }] };
      }),
    );
  }, []);

  const setTrackProp = useCallback((trackId, prop, value) => {
    setTracks((prev) => prev.map((t) => (t.id === trackId ? { ...t, [prop]: value } : t)));
    // Live-apply volume/mute/solo during playback.
    if (prop === 'volume') {
      const g = gainsRef.current[trackId];
      if (g) g.gain.value = value;
    }
  }, []);

  // --- transport ---------------------------------------------------------

  const applyTrackGains = useCallback(
    (list) => {
      const soloActive = list.some((t) => t.soloed);
      list.forEach((t) => {
        const g = gainsRef.current[t.id];
        if (!g) return;
        const audible = !t.muted && (!soloActive || t.soloed);
        g.gain.value = audible ? t.volume : 0;
      });
    },
    [],
  );

  const stopNodes = useCallback(() => {
    nodesRef.current.forEach((n) => {
      try {
        n.stop();
      } catch {
        /* already stopped */
      }
    });
    nodesRef.current = [];
    gainsRef.current = {};
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
  }, []);

  const stop = useCallback(() => {
    stopNodes();
    offsetRef.current = 0;
    setPosition(0);
    setPlaying(false);
  }, [stopNodes]);

  const play = useCallback(
    (fromOffset) => {
      stopNodes();
      const ctx = context();
      ctx.resume();
      const from = fromOffset != null ? fromOffset : offsetRef.current;
      offsetRef.current = from;
      const startAt = ctx.currentTime + 0.06;
      startAtRef.current = startAt;

      tracks.forEach((t) => {
        const gain = ctx.createGain();
        gain.connect(ctx.destination);
        gainsRef.current[t.id] = gain;
        t.clips.forEach((c) => {
          const buffer = buffersRef.current[c.id];
          if (!buffer) return;
          const clipEnd = c.start + c.duration;
          if (clipEnd <= from) return; // already past
          const src = ctx.createBufferSource();
          src.buffer = buffer;
          src.connect(gain);
          if (c.start >= from) {
            // Starts in the future.
            src.start(startAt + (c.start - from), c.offset, c.duration);
          } else {
            // Straddles the playhead — begin partway in.
            const into = from - c.start;
            src.start(startAt, c.offset + into, c.duration - into);
          }
          nodesRef.current.push(src);
        });
      });

      applyTrackGains(tracks);
      setPlaying(true);

      const total = duration;
      const tick = () => {
        const pos = offsetRef.current + Math.max(0, ctx.currentTime - startAtRef.current);
        const lp = loopRef.current;
        // Loop back to the region start when the playhead passes its end.
        if (lp && lp.b - lp.a > 0.05 && pos >= lp.b) {
          play(lp.a);
          return;
        }
        if (pos >= total) {
          if (lp && lp.b - lp.a > 0.05) {
            play(lp.a);
            return;
          }
          stop();
          return;
        }
        setPosition(pos);
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
    },
    [context, stopNodes, tracks, applyTrackGains, duration, stop],
  );

  const pause = useCallback(() => {
    const ctx = context();
    offsetRef.current += Math.max(0, ctx.currentTime - startAtRef.current);
    stopNodes();
    setPlaying(false);
  }, [context, stopNodes]);

  const seek = useCallback(
    (time) => {
      const t = Math.max(0, Math.min(time, duration || time));
      offsetRef.current = t;
      setPosition(t);
      if (playing) play(t);
    },
    [duration, playing, play],
  );

  return {
    tracks,
    playing,
    position,
    duration,
    context,
    getBuffer,
    addTrack,
    addClip,
    addTrackWithClip,
    moveClip,
    updateClip,
    splitClip,
    duplicateClip,
    removeClip,
    removeTrack,
    replaceRegion,
    setTrackProp,
    play,
    pause,
    stop,
    seek,
    loop,
    setLoop,
  };
}
