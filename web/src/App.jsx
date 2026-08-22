import { useCallback, useEffect, useState } from 'react';
import * as apiClient from './api.js';
import { blobToWav } from './wav.js';
import { useMultitrack } from './useMultitrack.js';
import Header from './components/Header.jsx';
import InputView from './components/InputView.jsx';
import TracksView from './components/TracksView.jsx';

export default function App() {
  const [view, setView] = useState('input');
  const [backends, setBackends] = useState([]);
  const [backend, setBackend] = useState(() => {
    // Default to the local model. 'mock' was an earlier default; drop it so
    // stale storage does not keep the app on the placeholder backend.
    const stored = localStorage.getItem('backend');
    return stored && stored !== 'mock' ? stored : 'local';
  });
  const [sessionId, setSessionId] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [fileName, setFileName] = useState(null);
  const [prompt, setPrompt] = useState('');
  const [selected, setSelected] = useState(() => new Set(['bass', 'drums', 'piano', 'harmony']));
  const [stems, setStems] = useState({}); // part -> result
  const [busyPart, setBusyPart] = useState(null);
  const [generating, setGenerating] = useState(false);
  const [toast, setToast] = useState(null);

  const engine = useMultitrack();

  const flash = useCallback((message) => {
    setToast(message);
    window.setTimeout(() => setToast((c) => (c === message ? null : c)), 4000);
  }, []);

  // The server forgets a session if it restarts. Rather than surface a raw
  // 404, drop back to the input view and ask for a fresh recording.
  const isLostSession = (error) =>
    /404|no such session/i.test(error.message || '');

  const resetSession = useCallback(() => {
    setSessionId(null);
    setAnalysis(null);
    setFileName(null);
    setStems({});
    setView('input');
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
      // Re-encode to WAV in the browser — the backend has no ffmpeg and
      // libsndfile cannot read the recorder's webm/opus.
      const wav = await blobToWav(blob);
      const wavName = filename.replace(/\.[^.]+$/, '') + '.wav';
      const result = await apiClient.analyze(wav, wavName);
      setSessionId(result.session_id);
      setAnalysis(result.analysis);
      setFileName(wavName);
      setStems({});
      engine.loadBuffer('vocal', apiClient.vocalUrl(result.session_id));
      flash('Analyzed — set your prompt and generate');
    } catch (error) {
      setFileName(null);
      flash(`Analysis failed — ${error.message}`);
    }
  };

  // Generate one stem, loading it into the mixer. Shared by the initial
  // batch and per-stem regenerate.
  const generateOne = async (part, opts = {}) => {
    setBusyPart(part);
    try {
      const result = await apiClient.generate({
        session_id: sessionId,
        part,
        // Per-track regenerate can override the prompt and divergence.
        style: opts.style != null ? opts.style : prompt,
        noise: opts.noise,
        backend,
        seed: opts.seed,
      });
      setStems((prev) => ({ ...prev, [part]: result }));
      if (result.backend_used !== backend) {
        flash(`${backend} unavailable — used ${result.backend_used}`);
      }
      await engine.loadBuffer(part, result.audio_url);
      return true;
    } catch (error) {
      if (isLostSession(error)) {
        resetSession();
        return false;
      }
      flash(`${part} failed — ${error.message}`);
      return true;
    } finally {
      setBusyPart(null);
    }
  };

  const generate = async (analysisEdit) => {
    setGenerating(true);
    try {
      const updated = await apiClient.updateAnalysis(sessionId, analysisEdit);
      setAnalysis(updated);
      // Generate selected stems in sequence — the local backend is single
      // model, so parallel calls would just contend. Stop early if the
      // session was lost mid-batch.
      for (const part of selected) {
        const ok = await generateOne(part);
        if (!ok) return;
      }
      setView('tracks');
    } catch (error) {
      if (isLostSession(error)) resetSession();
      else flash(`Could not generate — ${error.message}`);
    } finally {
      setGenerating(false);
    }
  };

  return (
    <>
      <Header
        view={view}
        onView={setView}
        sessionName={fileName || 'Untitled'}
        tracksReady={Object.keys(stems).length > 0}
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
          onSubmitVocal={submitVocal}
          onGenerate={generate}
          generating={generating}
        />
      ) : (
        <TracksView
          engine={engine}
          stems={stems}
          onRegenerate={(part, opts) =>
            generateOne(part, { ...opts, seed: Math.floor(Math.random() * 1e9) })
          }
          busyPart={busyPart}
          defaultPrompt={prompt}
          stemUrl={(part) => apiClient.stemUrl(sessionId, part)}
          vocalUrl={sessionId ? apiClient.vocalUrl(sessionId) : null}
          exportHref={sessionId ? apiClient.exportUrl(sessionId) : null}
        />
      )}

      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
