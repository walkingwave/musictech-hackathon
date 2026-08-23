import { useCallback, useEffect, useRef } from 'react';

// Keep the project across reloads.
//
// AudioBuffers cannot be serialized, so what gets saved is everything
// needed to rebuild them: the session id, the tempo and key, and each
// track's clips with the URL its audio came from. On load the metadata
// comes back immediately and the audio is re-fetched from the backend,
// which still has every stem on disk.
//
// MIDI clips restore completely from the saved notes — there is nothing to
// re-fetch, because a MIDI part is its notes.

const KEY = 'btg.project';
const SAVE_DEBOUNCE = 600;

export function useProject({ engine, sessionId, setSessionId, studio, onRestored }) {
  const restoredRef = useRef(false);
  const timerRef = useRef(null);

  // --- save --------------------------------------------------------------

  useEffect(() => {
    // Do not save until a restore has been attempted, or the empty initial
    // state overwrites the saved project before it can be read back.
    if (!restoredRef.current) return undefined;

    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      const snapshot = {
        version: 1,
        sessionId,
        bpm: studio.bpm,
        key: studio.key,
        mode: studio.mode,
        tracks: engine.tracks.map((t) => ({
          name: t.name,
          kind: t.kind,
          instrument: t.instrument ?? null,
          muted: t.muted,
          soloed: t.soloed,
          volume: t.volume,
          clips: t.clips.map((c) => ({
            start: c.start,
            duration: c.duration,
            durationBeats: c.durationBeats,
            offset: c.offset,
            notes: c.notes ?? null,
            part: c.part ?? null,
            prompt: c.prompt ?? '',
            seed: c.seed ?? null,
            backendUsed: c.backendUsed ?? null,
            audioUrl: c.audioUrl ?? null,
          })),
        })),
      };
      try {
        localStorage.setItem(KEY, JSON.stringify(snapshot));
      } catch {
        // Quota, most likely. Losing autosave is not worth breaking the app.
      }
    }, SAVE_DEBOUNCE);

    return () => clearTimeout(timerRef.current);
  }, [engine.tracks, sessionId, studio.bpm, studio.key, studio.mode]);

  // --- restore -----------------------------------------------------------

  useEffect(() => {
    let cancelled = false;

    (async () => {
      let saved = null;
      try {
        saved = JSON.parse(localStorage.getItem(KEY) || 'null');
      } catch {
        saved = null;
      }

      if (!saved?.tracks?.length) {
        restoredRef.current = true;
        return;
      }

      if (saved.sessionId) setSessionId(saved.sessionId);
      if (saved.bpm) studio.setBpm(saved.bpm);
      if (saved.key) studio.setKey(saved.key);
      if (saved.mode) studio.setMode(saved.mode);

      for (const track of saved.tracks) {
        if (cancelled) return;
        const clip = track.clips?.[0];
        if (!clip) continue;

        if (track.kind === 'midi') {
          engine.addMidiTrack(track.name, track.instrument, {
            start: clip.start,
            duration: clip.duration,
            durationBeats: clip.durationBeats,
            notes: clip.notes || [],
          });
          continue;
        }

        if (!clip.audioUrl) continue;
        try {
          const response = await fetch(clip.audioUrl);
          if (!response.ok) continue; // session cleaned up server-side
          const buffer = await engine.context().decodeAudioData(await response.arrayBuffer());
          engine.addTrackWithClip(track.name, track.kind, buffer, {
            start: clip.start,
            duration: clip.duration,
            part: clip.part,
            prompt: clip.prompt,
            seed: clip.seed,
            backendUsed: clip.backendUsed,
            audioUrl: clip.audioUrl,
          });
        } catch {
          // A stem that will not load should not stop the rest restoring.
        }
      }

      restoredRef.current = true;
      onRestored?.(saved);
    })();

    return () => {
      cancelled = true;
    };
    // Runs once on mount by design.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const clear = useCallback(() => {
    localStorage.removeItem(KEY);
  }, []);

  return { clear };
}
