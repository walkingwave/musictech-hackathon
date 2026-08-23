import { useCallback, useEffect, useRef, useState } from 'react';
import * as apiClient from './api.js';
import { blobToWav } from './wav.js';
import { useTimeline } from './useTimeline.js';
import { useInstruments } from './useInstruments.js';
import { useSampler } from './useSampler.js';
import { useProject } from './useProject.js';
import Header from './components/Header.jsx';
import InputView from './components/InputView.jsx';
import Studio from './components/Studio.jsx';
import InstrumentView from './components/InstrumentView.jsx';

const PART_LABEL = { bass: 'Bass', drums: 'Drums', piano: 'Piano', harmony: 'Harmony' };

export default function App() {
  const [view, setView] = useState('input');
  const [backends, setBackends] = useState([]);
  const [backend, setBackend] = useState(() => {
    const stored = localStorage.getItem('backend');
    return stored && stored !== 'mock' ? stored : 'local';
  });
  const [sessionId, setSessionId] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [fileName, setFileName] = useState(null);
  // A user-chosen session name, editable in the top bar. Falls back to the
  // uploaded filename, then "Untitled". Persisted so a reload keeps it.
  const [projectName, setProjectName] = useState(() => localStorage.getItem('projectName') || '');
  const [prompt, setPrompt] = useState('');
  const [selected, setSelected] = useState(() => new Set(['bass', 'drums', 'piano', 'harmony']));
  const [bars, setBars] = useState(16); // target backing length
  const [generating, setGenerating] = useState(false);
  const [toast, setToast] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [sessionPickerOpen, setSessionPickerOpen] = useState(false);
  // Studio tempo/key — auto-populated from analysis, editable in the studio
  // (works even with no upload, where they default and just drive the grid).
  const [studioBpm, setStudioBpm] = useState(120);
  const [studioKey, setStudioKey] = useState('C');
  const [studioMode, setStudioMode] = useState('major');

  const sampler = useSampler();
  const engine = useTimeline(sampler);
  const library = useInstruments();
  const vocalWavRef = useRef(null); // the analyzed vocal as a WAV blob
  const vocalAddedRef = useRef(false);

  const flash = useCallback((message) => {
    setToast(message);
    window.setTimeout(() => setToast((c) => (c === message ? null : c)), 4000);
  }, []);

  // MIDI notes are stored in beats, so playback needs the current tempo.
  useEffect(() => engine.setBpm(studioBpm), [engine, studioBpm]);

  // Reload used to start from nothing. The project now restores itself:
  // metadata from localStorage, audio re-fetched from the backend, which
  // still holds every stem.
  const project = useProject({
    engine,
    sessionId,
    setSessionId,
    studio: {
      bpm: studioBpm, key: studioKey, mode: studioMode,
      setBpm: setStudioBpm, setKey: setStudioKey, setMode: setStudioMode,
    },
    onRestored: (saved) => {
      if (saved.tracks?.length) setView('studio');
      flash('Project restored');
      // Re-load every restored track's instrument, or the MIDI parts come
      // back visible but silent until each slot is manually reloaded.
      // Sequential: generated instruments contend for the local model.
      (async () => {
        const seen = new Set();
        for (const t of saved.tracks || []) {
          const inst = t.kind === 'midi' && t.instrument;
          if (!inst || seen.has(inst.id)) continue;
          seen.add(inst.id);
          await sampler.load(inst, { backend }).catch(() => {});
        }
      })();
    },
  });
  const refreshSessions = useCallback(async () => {
    const list = await apiClient.listSessions();
    setSessions(list);
    return list;
  }, []);

  // The server quietly falls back to a working backend when the chosen one
  // can't run (e.g. `local` picked but Hugging Face access not granted yet).
  // Silent fallback looks like "the model sounds wrong" — so say what ran and,
  // when the server told us, the actual error (HTTP status included).
  const warnOnFallback = useCallback(
    (result) => {
      const used = result?.backend_used;
      if (used && backend && used !== backend) {
        const why = result?.fallback_error;
        flash(why
          ? `${why} — generated with "${used}" instead`
          : `"${backend}" couldn't run — generated with "${used}" instead`);
      }
    },
    [backend, flash],
  );

  // Wipe the editor back to an empty project. Server-side files are kept —
  // both "close" and "new" are local operations; only Delete touches the disk.
  const clearProject = useCallback(() => {
    engine.clear();
    project.clear();
    setSessionId(null);
    setAnalysis(null);
    setFileName(null);
    setPrompt('');
    setView('input');
    vocalWavRef.current = null;
    vocalAddedRef.current = false;
  }, [engine, project]);

  const closeProject = useCallback(() => {
    clearProject();
    flash('Project closed — server files were kept');
  }, [clearProject, flash]);

  const newProject = useCallback(() => {
    clearProject();
    flash('New project — record or upload to start');
  }, [clearProject, flash]);

  const loadProject = useCallback(async (id) => {
    try {
      const meta = await apiClient.getSession(id);
      if (!meta.analysis) throw new Error('session has not been analyzed yet');
      engine.clear();
      project.clear();
      setSessionId(id);
      setAnalysis(meta.analysis);
      setStudioBpm(meta.analysis.bpm);
      setStudioKey(meta.analysis.key);
      setStudioMode(meta.analysis.mode);
      setFileName(meta.display_name || `Session ${id}`);
      vocalWavRef.current = null;
      vocalAddedRef.current = false;
      for (const stem of Object.values(meta.stems || {})) {
        const url = `/api/session/${id}/audio/stems/${encodeURIComponent(stem.name)}.wav?v=${stem.seed || ''}`;
        const response = await fetch(url);
        if (!response.ok) continue;
        const buffer = await engine.context().decodeAudioData(await response.arrayBuffer());
        engine.addTrackWithClip(stem.name, stem.part, buffer, {
          start: 0, part: stem.part, prompt: stem.prompt, seed: stem.seed,
          backendUsed: stem.backend_used, duration: stem.duration || buffer.duration, audioUrl: url,
        });
      }
      setView('studio');
      setSessionPickerOpen(false);
      flash('Project loaded');
    } catch (error) {
      flash(`Could not load project — ${error.message}`);
    }
  }, [engine, project, flash]);

  // Deleting the project you have open also has to clear it out of the editor;
  // deleting some other project from the picker must leave the open one alone.
  const deleteProject = useCallback(async (id, name) => {
    if (!id || !window.confirm(`Delete ${name || `session ${id}`} permanently?`)) return;
    try {
      await apiClient.deleteSession(id);
      if (id === sessionId) closeProject();
      await refreshSessions();
      flash('Project deleted');
    } catch (error) {
      flash(`Could not delete project — ${error.message}`);
    }
  }, [sessionId, closeProject, refreshSessions, flash]);

  const deleteCurrentProject = useCallback(
    () => deleteProject(sessionId, fileName),
    [deleteProject, sessionId, fileName],
  );

  const openSessionPicker = useCallback(async () => {
    try {
      await refreshSessions();
      setSessionPickerOpen(true);
    } catch (error) {
      flash(`Could not list projects — ${error.message}`);
    }
  }, [refreshSessions, flash]);

  const isLostSession = (error) => /404|no such session/i.test(error.message || '');
  const resetSession = useCallback(() => {
    setSessionId(null);
    setAnalysis(null);
    setFileName(null);
    setView('input');
    vocalAddedRef.current = false;
    flash('Session expired — record or upload again');
  }, [flash]);

  useEffect(() => {
    apiClient
      .listBackends()
      .then((list) => {
        setBackends(list);
        setBackend((current) => {
          if (list.some((b) => b.id === current && b.available)) return current;
          const usable = list.find((b) => b.available);
          return usable ? usable.id : current;
        });
      })
      .catch((error) => flash(`Could not load backends — ${error.message}`));
  }, [flash]);

  const selectBackend = (id) => {
    setBackend(id);
    localStorage.setItem('backend', id);
  };

  const displayName = projectName || fileName || 'Untitled';

  const renameSession = (name) => {
    setProjectName(name);
    localStorage.setItem('projectName', name);
  };

  // The whole project as a zip: every stem, its MIDI, the vocal and a
  // provenance manifest — the deliverable a musician drops into a DAW.
  const exportProject = () => {
    if (!sessionId) return flash('Nothing to export yet — generate something first');
    const a = document.createElement('a');
    a.href = apiClient.exportUrl(sessionId);
    a.download = `${displayName.replace(/[^\w-]+/g, '_') || 'project'}.zip`;
    a.click();
  };

  // Share the export link. Uses the native share sheet when the browser has
  // one (mobile, some desktops), else copies the link to the clipboard.
  const shareProject = async () => {
    if (!sessionId) return flash('Nothing to share yet — generate something first');
    const url = new URL(apiClient.exportUrl(sessionId), window.location.origin).href;
    try {
      if (navigator.share) {
        await navigator.share({ title: displayName, url });
        return;
      }
      await navigator.clipboard.writeText(url);
      flash('Project export link copied to clipboard');
    } catch {
      /* user dismissed the share sheet — nothing to do */
    }
  };

  const toggleStem = (id) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const submitVocal = async (blob, filename) => {
    flash('Analyzing…');
    try {
      const wav = await blobToWav(blob);
      const wavName = filename.replace(/\.[^.]+$/, '') + '.wav';
      const result = await apiClient.analyze(wav, wavName);
      setSessionId(result.session_id);
      setAnalysis(result.analysis);
      setStudioBpm(result.analysis.bpm);
      setStudioKey(result.analysis.key);
      setStudioMode(result.analysis.mode);
      setFileName(wavName);
      vocalWavRef.current = wav;
      vocalAddedRef.current = false;
      flash('Analyzed — set your prompt and generate');
    } catch (error) {
      setFileName(null);
      flash(`Analysis failed — ${error.message}`);
    }
  };

  // One network generate. Returns the raw result; never touches timeline state
  // so it can be reused for the initial batch, clip regen, and section regen.
  // Composing without a recording still needs a session, since every stage
  // keys off an Analysis. Make a blank one on first use.
  const ensureSession = useCallback(async () => {
    if (sessionId) return sessionId;
    const result = await apiClient.createBlankSession({
      bpm: studioBpm,
      key: studioKey,
      mode: studioMode,
      bars,
    });
    setSessionId(result.session_id);
    setAnalysis(result.analysis);
    return result.session_id;
  }, [sessionId, studioBpm, studioKey, studioMode, bars]);

  const generateStem = useCallback(
    async (opts) => {
      const id = sessionId || (await ensureSession());
      const result = await apiClient.generate({
        session_id: id,
        part: opts.part,
        style: opts.style != null ? opts.style : prompt,
        noise: opts.noise,
        bars: opts.bars,
        start_bar: opts.start_bar,
        name: opts.name,
        instrument: opts.instrument,
        production: opts.production,
        voice_index: opts.voice_index,
        voice_count: opts.voice_count,
        backend,
        seed: opts.seed,
      });
      warnOnFallback(result);
      return result;
    },
    [sessionId, prompt, backend, ensureSession, warnOnFallback],
  );

  const ensureVocalTrack = useCallback(async () => {
    if (vocalAddedRef.current || !vocalWavRef.current) return;
    const buffer = await engine.context().decodeAudioData(await vocalWavRef.current.arrayBuffer());
    engine.addTrackWithClip('Vocal', 'vocal', buffer, { start: 0, part: 'vocal' });
    vocalAddedRef.current = true;
  }, [engine]);

  const generate = async (analysisEdit) => {
    setGenerating(true);
    try {
      await apiClient.updateAnalysis(sessionId, analysisEdit);
      await ensureVocalTrack();
      // Sequential — the local model is single-instance, parallel calls
      // would just contend for it.
      for (const part of selected) {
        const result = await generateStem({ part, style: prompt, bars });
        const buffer = await engine
          .context()
          .decodeAudioData(await (await fetch(result.audio_url)).arrayBuffer());
        engine.addTrackWithClip(PART_LABEL[part] || part, part, buffer, {
          start: 0,
          part,
          prompt,
          seed: result.seed,
          backendUsed: result.backend_used,
          duration: result.duration || buffer.duration,
          startBar: 0,
          audioUrl: result.audio_url,
        });
      }
      setView('studio');
    } catch (error) {
      if (isLostSession(error)) resetSession();
      else flash(`Could not generate — ${error.message}`);
    } finally {
      setGenerating(false);
    }
  };

  // Generate guided by an existing track's audio rather than a synthesized
  // guide. Creates a blank session first if the user never uploaded a vocal.
  const studioGenerateFromReference = useCallback(
    async (opts) => {
      const id = sessionId || (await ensureSession());
      try {
        return await apiClient.generateFromReference({
          sessionId: id,
          referenceWav: opts.referenceWav,
          prompt: opts.prompt,
          noise: opts.noise,
          backend,
          seed: opts.seed,
          name: opts.name,
        });
      } catch (error) {
        if (isLostSession(error)) resetSession();
        throw error;
      }
    },
    [sessionId, backend, resetSession, ensureSession],
  );

  // Render a MIDI clip's notes with its instrument prompt. Creates the
  // session if this is the first thing the user does, so the instrument
  // flow works as a starting point and not only as an addition.
  const renderMidi = useCallback(
    async ({ notes, prompt, name, bars: clipBars }) => {
      const result = await apiClient.generateFromMidi({
        session_id: sessionId,
        notes,
        prompt,
        name,
        bars: clipBars,
        backend,
        bpm: studioBpm,
        key: studioKey,
        mode: studioMode,
      });
      if (!sessionId) setSessionId(result.session_id);
      warnOnFallback(result);
      return result;
    },
    [sessionId, backend, studioBpm, studioKey, studioMode, warnOnFallback],
  );

  // Adding an instrument to the library also gives you somewhere to play
  // it: a MIDI track with that instrument already in its slot. The slot is
  // swappable afterwards, so this is a shortcut, not a binding.
  const createInstrument = useCallback(
    ({ name, prompt }) => {
      const instrument = library.create({ name, prompt });
      const secondsPerBar = (60 / (studioBpm || 100)) * 4;
      engine.addMidiTrack(instrument.name, instrument, {
        start: 0,
        duration: secondsPerBar * 4,
        durationBeats: 16,
        notes: [],
      });
      setView('studio');
      // Start fetching the sound immediately — the track arrives playable
      // instead of waiting for the slot to be reopened and reloaded.
      sampler
        .load(instrument, { backend })
        .catch((error) => flash(`Could not load ${instrument.name} — ${error.message}`));
    },
    [engine, library, studioBpm, sampler, backend, flash],
  );

  // The agent answers with tempo, key and mode as part of the brief ("a slow
  // sad song" is not 120 BPM in C major). Applying them has to be BOTH: the
  // Studio boxes so the user sees what changed, and the session's analysis so
  // the guide tracks and prompts of everything generated afterwards use them.
  // Setting only the local state left the fields showing one tempo while the
  // backend kept generating at another.
  const applyMusicalSettings = useCallback(
    async ({ bpm, key, mode }) => {
      if (bpm) setStudioBpm(bpm);
      if (key) setStudioKey(key);
      if (mode) setStudioMode(mode);
      if (!bpm && !key && !mode) return;
      try {
        if (sessionId) {
          const updated = await apiClient.updateAnalysis(sessionId, {
            bpm: bpm ?? null,
            key: key ?? null,
            mode: mode ?? null,
          });
          setAnalysis(updated);
          return;
        }
        // No session yet: create it AT these settings. Leaving it to the first
        // generate meant the session was created from the state this call had
        // only just asked React to update, so a "slow sad ballad" was
        // generated at whatever the boxes said before — 120 BPM, C major —
        // and the corrected values only landed on the next request.
        const result = await apiClient.createBlankSession({
          bpm: bpm ?? studioBpm,
          key: key ?? studioKey,
          mode: mode ?? studioMode,
          bars,
        });
        setSessionId(result.session_id);
        setAnalysis(result.analysis);
      } catch (error) {
        flash(`Tempo and key were not saved — ${error.message}`);
      }
    },
    [sessionId, studioBpm, studioKey, studioMode, bars, flash],
  );

  // Words -> notes. The agent writes a phrase, it lands on a MIDI track with
  // a matching instrument already in the slot, and it stays editable in the
  // piano roll — the point of MIDI over a rendered stem.
  const composeMidiTrack = useCallback(
    async ({ text, bars, style }) => {
      const phrase = await apiClient.composeMidi({
        text,
        session_id: sessionId,
        bars,
        // The Studio's boxes are more current than whatever was detected from
        // the original vocal, so they win over the session's own analysis.
        bpm: studioBpm,
        key: studioKey,
        mode: studioMode,
        style,
      });
      const instrument = library.create({
        name: phrase.name,
        prompt: phrase.instrument,
      });
      const beats = Math.max(1, (phrase.bars || 4) * 4);
      const secondsPerBeat = 60 / (studioBpm || 100);
      engine.addMidiTrack(phrase.name, instrument, {
        start: 0,
        duration: beats * secondsPerBeat,
        durationBeats: beats,
        notes: phrase.notes,
      });
      // Fetch the sound straight away, or the notes arrive visible but silent.
      sampler
        .load(instrument, { backend })
        .catch((error) => flash(`Could not load ${instrument.name} — ${error.message}`));
      return phrase;
    },
    [sessionId, studioBpm, studioKey, studioMode, library, engine, sampler, backend, flash],
  );

  // Passed to the studio for clip / section regenerate. Surfaces lost sessions.
  const studioGenerate = useCallback(
    async (opts) => {
      try {
        return await generateStem(opts);
      } catch (error) {
        if (isLostSession(error)) resetSession();
        throw error;
      }
    },
    [generateStem, resetSession],
  );

  return (
    <>
      <Header
        view={view}
        onView={setView}
        sessionName={displayName}
        onRenameSession={renameSession}
        tracksReady={engine.tracks.length > 0}
        canExport={!!sessionId}
        onExportProject={exportProject}
        onShareProject={shareProject}
        backends={backends}
        backend={backend}
        onBackend={selectBackend}
        onNewProject={newProject}
        onOpenSession={openSessionPicker}
        onCloseSession={closeProject}
        onDeleteSession={deleteCurrentProject}
        sessionActive={!!sessionId}
        busy={generating}
      />

      {view === 'instrument' ? (
        <InstrumentView
          onCreate={createInstrument}
          onRemove={library.remove}
          instruments={library.instruments}
        />
      ) : view === 'input' ? (
        <InputView
          analysis={analysis}
          fileName={fileName}
          backends={backends}
          backend={backend}
          onBackend={selectBackend}
          prompt={prompt}
          onPrompt={setPrompt}
          selected={selected}
          onToggleStem={toggleStem}
          bars={bars}
          onBars={setBars}
          onSubmitVocal={submitVocal}
          onGenerate={generate}
          generating={generating}
        />
      ) : (
        <Studio
          engine={engine}
          bpm={studioBpm}
          keyName={studioKey}
          mode={studioMode}
          detected={!!analysis}
          onBpm={setStudioBpm}
          onKey={setStudioKey}
          onMode={setStudioMode}
          onGenerateStem={studioGenerate}
          onComposeMidi={composeMidiTrack}
          onApplySettings={applyMusicalSettings}
          onGenerateFromReference={studioGenerateFromReference}
          onRenderMidi={renderMidi}
          sessionId={sessionId}
          instruments={library.instruments}
          sampler={sampler}
          backend={backend}
        />
      )}

      {sessionPickerOpen && (
        <div className="modal-backdrop" role="presentation">
          <section className="session-picker" role="dialog" aria-modal="true" aria-label="Open project">
            <div className="row"><h2>Projects</h2><button onClick={() => setSessionPickerOpen(false)}>Close</button></div>
            {!sessions.length ? <p>No saved projects.</p> : sessions.map((item) => (
              <div className="session-entry" key={item.id}>
                <button className="session-row" onClick={() => loadProject(item.id)}>
                  <strong>{item.display_name}</strong><span>{item.analysis ? `${Math.round(item.analysis.bpm)} BPM · ${item.analysis.key} ${item.analysis.mode}` : 'Unanalyzed'} · {item.track_names.length} tracks</span>
                </button>
                <button
                  className="session-del"
                  title={`Delete ${item.display_name}`}
                  aria-label={`Delete ${item.display_name}`}
                  onClick={() => deleteProject(item.id, item.display_name)}
                >
                  Delete
                </button>
              </div>
            ))}
          </section>
        </div>
      )}
      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
