import { useCallback, useEffect, useRef, useState } from 'react';
import * as apiClient from './api.js';
import { blobToWav } from './wav.js';
import { useTimeline } from './useTimeline.js';
import Header from './components/Header.jsx';
import InputView from './components/InputView.jsx';
import Studio from './components/Studio.jsx';

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
  const [prompt, setPrompt] = useState('');
  const [selected, setSelected] = useState(() => new Set(['bass', 'drums', 'piano', 'harmony']));
  const [bars, setBars] = useState(16); // target backing length
  const [generating, setGenerating] = useState(false);
  const [toast, setToast] = useState(null);
  // Studio tempo/key — auto-populated from analysis, editable in the studio
  // (works even with no upload, where they default and just drive the grid).
  const [studioBpm, setStudioBpm] = useState(120);
  const [studioKey, setStudioKey] = useState('C');
  const [studioMode, setStudioMode] = useState('major');

  const engine = useTimeline();
  const vocalWavRef = useRef(null); // the analyzed vocal as a WAV blob
  const vocalAddedRef = useRef(false);

  const flash = useCallback((message) => {
    setToast(message);
    window.setTimeout(() => setToast((c) => (c === message ? null : c)), 4000);
  }, []);

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
        backend,
        seed: opts.seed,
      });
      return result;
    },
    [sessionId, prompt, backend, ensureSession],
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
        sessionName={fileName || 'Untitled'}
        tracksReady={engine.tracks.length > 0}
      />

      {view === 'input' ? (
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
          onGenerateFromReference={studioGenerateFromReference}
          sessionId={sessionId}
        />
      )}

      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
