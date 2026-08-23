import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

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

const HISTORY_LIMIT = 100;

export function useTimeline(sampler) {
  const ctxRef = useRef(null);
  const buffersRef = useRef({}); // clipId -> AudioBuffer
  const nodesRef = useRef([]); // active source nodes during playback
  const gainsRef = useRef({}); // trackId -> GainNode during playback
  const startAtRef = useRef(0);
  const offsetRef = useRef(0);
  const rafRef = useRef(null);

  const [tracks, setTracks] = useState([]);
  // Tempo, so MIDI clips can turn their beat-based notes into seconds.
  const bpmRef = useRef(100);
  const setBpm = useCallback((value) => {
    bpmRef.current = value || 100;
  }, []);

  // --- undo / redo -------------------------------------------------------
  //
  // Snapshots of the whole `tracks` array. Cheap because clips are small
  // metadata objects; the audio itself lives in buffersRef, keyed by clip
  // id, and is never freed (see removeClip) so any snapshot stays playable.
  const pastRef = useRef([]);
  const futureRef = useRef([]);
  const [historyTick, setHistoryTick] = useState(0);
  const bumpHistory = () => setHistoryTick((v) => v + 1);

  // Snapshot the current state as one undo step. Discrete edits call this
  // themselves; continuous gestures (dragging a clip, trimming an edge)
  // call it once on pointer-down so a whole drag collapses into one undo
  // instead of one per mouse-move.
  const beginGesture = useCallback(() => {
    setTracks((prev) => {
      pastRef.current = [...pastRef.current, prev].slice(-HISTORY_LIMIT);
      futureRef.current = [];
      return prev;
    });
    bumpHistory();
  }, []);

  const undo = useCallback(() => {
    setTracks((prev) => {
      if (!pastRef.current.length) return prev;
      const restored = pastRef.current[pastRef.current.length - 1];
      pastRef.current = pastRef.current.slice(0, -1);
      futureRef.current = [...futureRef.current, prev];
      return restored;
    });
    bumpHistory();
  }, []);

  const redo = useCallback(() => {
    setTracks((prev) => {
      if (!futureRef.current.length) return prev;
      const restored = futureRef.current[futureRef.current.length - 1];
      futureRef.current = futureRef.current.slice(0, -1);
      pastRef.current = [...pastRef.current, prev].slice(-HISTORY_LIMIT);
      return restored;
    });
    bumpHistory();
  }, []);

  const canUndo = pastRef.current.length > 0;
  const canRedo = futureRef.current.length > 0;
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

  // The sampler needs the same AudioContext as the transport, or notes
  // scheduled against one clock play against another.
  useEffect(() => {
    if (sampler?.context) ctxRef.current = sampler.context();
  }, [sampler]);

  const getBuffer = useCallback((clipId) => buffersRef.current[clipId], []);

  // --- structure ---------------------------------------------------------

  const addTrack = useCallback((name, kind) => {
    const id = uid('trk');
    beginGesture();
    setTracks((prev) => [
      ...prev,
      { id, name, kind: kind || 'audio', muted: false, soloed: false, volume: 1, clips: [] },
    ]);
    return id;
  }, [beginGesture]);

  const addClip = useCallback((trackId, buffer, meta = {}) => {
    const id = uid('clip');
    beginGesture();
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
  }, [beginGesture]);

  // A MIDI track: its clip carries notes rather than audio, and stays
  // silent until rendered. The instrument prompt lives on the track, so
  // every clip on it is rendered with the same sound.
  const addMidiTrack = useCallback(
    (name, instrument, meta = {}) => {
      const trackId = uid('trk');
      const clipId = uid('clip');
      beginGesture();
      const clip = {
        id: clipId,
        start: meta.start ?? 0,
        offset: 0,
        duration: meta.duration ?? 8,
        // Length in beats is what the piano roll and the backend speak in;
        // `duration` stays in seconds for the timeline itself.
        durationBeats: meta.durationBeats ?? 16,
        notes: meta.notes ?? [],
        part: null,
        prompt: instrument,
        seed: null,
        backendUsed: null,
        startBar: 0,
      };
      setTracks((prev) => [
        ...prev,
        {
          id: trackId,
          name,
          kind: 'midi',
          instrument,
          muted: false,
          soloed: false,
          volume: 1,
          clips: [clip],
        },
      ]);
      return { trackId, clipId };
    },
    [beginGesture],
  );

  // Replace a MIDI clip's notes. Not a history step per edit — the piano
  // roll calls this on every drag frame — so callers snapshot on
  // pointer-down instead, the same way clip dragging does.
  const setClipNotes = useCallback((trackId, clipId, notes) => {
    setTracks((prev) =>
      prev.map((t) =>
        t.id === trackId
          ? { ...t, clips: t.clips.map((c) => (c.id === clipId ? { ...c, notes } : c)) }
          : t,
      ),
    );
  }, []);

  // Attach rendered audio to an existing clip, so a MIDI clip becomes
  // audible without losing the notes that produced it.
  const attachBuffer = useCallback((trackId, clipId, buffer, meta = {}) => {
    buffersRef.current[clipId] = buffer;
    setTracks((prev) =>
      prev.map((t) =>
        t.id === trackId
          ? {
              ...t,
              clips: t.clips.map((c) =>
                c.id === clipId
                  ? { ...c, duration: meta.duration ?? buffer.duration, ...meta }
                  : c,
              ),
            }
          : t,
      ),
    );
  }, []);

  // Convenience: create a track for a part and drop one clip on it.
  const addTrackWithClip = useCallback(
    (name, kind, buffer, meta = {}) => {
      const trackId = uid('trk');
      const clipId = uid('clip');
      beginGesture();
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
        // Remembered so the project can re-fetch this audio after a
        // reload; AudioBuffers themselves cannot be persisted.
        audioUrl: meta.audioUrl ?? null,
      };
      setTracks((prev) => [
        ...prev,
        { id: trackId, name, kind: kind || 'audio', muted: false, soloed: false, volume: 1, clips: [clip] },
      ]);
      return { trackId, clipId };
    },
    [beginGesture],
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

  // Buffers are deliberately NOT freed on delete: an undo has to be able to
  // bring the clip back, and the audio is the one part it cannot rebuild.
  // Leaks are bounded by the length of one session.
  const removeClip = useCallback(
    (trackId, clipId) => {
      beginGesture();
      setTracks((prev) =>
        prev.map((t) =>
          t.id === trackId ? { ...t, clips: t.clips.filter((c) => c.id !== clipId) } : t,
        ),
      );
    },
    [beginGesture],
  );

  const removeTrack = useCallback(
    (trackId) => {
      beginGesture();
      setTracks((prev) => prev.filter((x) => x.id !== trackId));
    },
    [beginGesture],
  );

  // Replace a time region [regStart, regEnd] (timeline seconds) of one clip
  // with a freshly generated buffer — the section-regenerate splice. Splits
  // the original into head / new / tail clips that share the same buffer.
  const replaceRegion = useCallback((trackId, clipId, regStart, regEnd, newBuffer, meta = {}) => {
    beginGesture();
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
  }, [beginGesture]);

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
  const splitClip = useCallback(
    (trackId, clipId, atTime) => {
      beginGesture();
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
    },
    [beginGesture],
  );

  // Cut a selected region out of a clip as its own clip, leaving the head and
  // tail behind. This is what makes "highlight a section, then drag it away"
  // work: the caller drags the returned clip immediately afterwards.
  //
  // The new id is generated up front and returned synchronously, so the
  // caller can start moving it before React has applied the state update.
  const extractRegion = useCallback(
    (trackId, clipId, a, b) => {
      const midId = uid('clip');
      beginGesture();
      setTracks((prev) =>
        prev.map((t) => {
          if (t.id !== trackId) return t;
          const idx = t.clips.findIndex((c) => c.id === clipId);
          if (idx < 0) return t;
          const c = t.clips[idx];
          const clipEnd = c.start + c.duration;
          const from = Math.max(c.start, Math.min(a, b));
          const to = Math.min(clipEnd, Math.max(a, b));
          if (to - from < 0.02) return t;

          const pieces = [];
          if (from > c.start + 0.02) {
            const headId = uid('clip');
            buffersRef.current[headId] = buffersRef.current[c.id];
            pieces.push({ ...c, id: headId, duration: from - c.start });
          }

          buffersRef.current[midId] = buffersRef.current[c.id];
          pieces.push({
            ...c,
            id: midId,
            start: from,
            offset: c.offset + (from - c.start),
            duration: to - from,
          });

          if (to < clipEnd - 0.02) {
            const tailId = uid('clip');
            buffersRef.current[tailId] = buffersRef.current[c.id];
            pieces.push({
              ...c,
              id: tailId,
              start: to,
              offset: c.offset + (to - c.start),
              duration: clipEnd - to,
            });
          }

          return { ...t, clips: [...t.clips.slice(0, idx), ...pieces, ...t.clips.slice(idx + 1)] };
        }),
      );
      return midId;
    },
    [beginGesture],
  );

  const duplicateClip = useCallback(
    (trackId, clipId) => {
      beginGesture();
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
    },
    [beginGesture],
  );

  // Solo is a whole-mix decision — one track going solo silences every other
  // track — so a single toggle has to re-derive the gain of all of them.
  const applyTrackGains = useCallback((list) => {
    const soloActive = list.some((t) => t.soloed);
    list.forEach((t) => {
      const g = gainsRef.current[t.id];
      if (!g) return;
      const audible = !t.muted && (!soloActive || t.soloed);
      const target = audible ? t.volume : 0;
      // A step change in gain mid-playback is an audible click, so ramp over
      // 10ms instead. cancelScheduledValues first, or a fast series of clicks
      // leaves earlier ramps queued and the last one does not win.
      const now = g.context.currentTime;
      g.gain.cancelScheduledValues(now);
      g.gain.setValueAtTime(g.gain.value, now);
      g.gain.linearRampToValueAtTime(target, now + 0.01);
    });
  }, []);

  const setTrackProp = useCallback(
    (trackId, prop, value) => {
      setTracks((prev) => {
        // Solo is exclusive, like a mixing desk: soloing one track drops the
        // solo on every other, so the mix always answers "which single track
        // am I hearing". Un-soloing just clears it.
        const next = prev.map((t) => {
          if (t.id === trackId) return { ...t, [prop]: value };
          return prop === 'soloed' && value && t.soloed ? { ...t, soloed: false } : t;
        });
        // Volume, mute and solo have to take effect on the click, not at the
        // next play. The gain nodes live outside React, so update them from
        // the list just built — reading `tracks` here would be a render behind.
        applyTrackGains(next);
        return next;
      });
    },
    [applyTrackGains],
  );

  // --- transport ---------------------------------------------------------

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

  const clear = useCallback(() => {
    stop();
    buffersRef.current = {};
    pastRef.current = [];
    futureRef.current = [];
    setTracks([]);
    setLoop(null);
    bumpHistory();
  }, [stop, setLoop]);

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
          // A MIDI clip has no audio of its own: its notes are triggered
          // on the track's sampler, so the part plays exactly as written
          // and editing never needs a re-render.
          if (Array.isArray(c.notes) && t.instrument && sampler?.isLoaded(t.instrument)) {
            const secondsPerBeat = 60 / (c.bpm || bpmRef.current || 100);
            c.notes.forEach((n) => {
              const at = c.start + n.start * secondsPerBeat;
              const length = Math.max(0.05, n.length * secondsPerBeat);
              if (at + length <= from) return;
              const offset = Math.max(0, from - at);
              sampler.play(t.instrument, n.pitch, startAt + (at - from) + offset, length - offset, {
                gain: (n.velocity ?? 90) / 127,
                destination: gain,
              });
            });
            return;
          }

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
    [context, stopNodes, tracks, applyTrackGains, duration, stop, sampler],
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
    addMidiTrack,
    setClipNotes,
    attachBuffer,
    moveClip,
    updateClip,
    splitClip,
    extractRegion,
    duplicateClip,
    removeClip,
    removeTrack,
    replaceRegion,
    setTrackProp,
    play,
    pause,
    stop,
    clear,
    seek,
    loop,
    setLoop,
    setBpm,
    beginGesture,
    undo,
    redo,
    canUndo,
    canRedo,
    historyTick,
  };
}
