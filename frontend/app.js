/*
 * Frontend for the Backing Track Generator.
 *
 * Three pieces, in order down the file:
 *   1. state + API calls
 *   2. rendering (backend picker, chord grid, part rows, track rows)
 *   3. playback (Web Audio, all stems on one transport)
 *
 * Kept as one plain file with no build step: teammates can edit and hit
 * refresh, which matters more than architecture at hackathon pace.
 */

const PARTS = ['bass', 'piano', 'drums', 'harmony'];

const state = {
  sessionId: null,
  analysis: null,
  backend: localStorage.getItem('backend') || 'mock',
  stems: {},        // part -> { audio_url, backend_used, seed }
  buffers: {},      // part | 'vocal' -> decoded AudioBuffer
  gains: {},        // part | 'vocal' -> GainNode
  volumes: {},      // part | 'vocal' -> 0..1.5
  muted: new Set(),
  soloed: new Set(),
};

let audioContext = null;
let playingSources = [];

// --- api ----------------------------------------------------------------

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.json();
}

const postJSON = (path, body) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

// --- backend picker -----------------------------------------------------

async function loadBackends() {
  const backends = await api('/backends');
  const container = document.getElementById('backend-options');
  container.innerHTML = '';

  // If the remembered choice is no longer available (no API key, weights
  // not downloaded), fall back to the first one that is.
  if (!backends.some((b) => b.id === state.backend && b.available)) {
    const usable = backends.find((b) => b.available);
    if (usable) selectBackend(usable.id);
  }

  backends.forEach((backend) => {
    const button = document.createElement('button');
    button.className = 'segment';
    button.textContent = backend.label;
    button.title = backend.available
      ? backend.note
      : `Unavailable — ${backend.note}`;
    button.disabled = !backend.available;
    button.classList.toggle('active', backend.id === state.backend);
    button.onclick = () => {
      selectBackend(backend.id);
      loadBackends();
    };
    container.appendChild(button);
  });
}

function selectBackend(id) {
  state.backend = id;
  localStorage.setItem('backend', id);
}

// --- step 1: input ------------------------------------------------------

let recorder = null;

document.getElementById('record').onclick = async () => {
  const button = document.getElementById('record');

  if (recorder && recorder.state === 'recording') {
    recorder.stop();
    button.textContent = 'Record';
    button.classList.remove('recording');
    return;
  }

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = (event) => chunks.push(event.data);
  recorder.onstop = () => {
    stream.getTracks().forEach((track) => track.stop());
    submitVocal(new Blob(chunks, { type: recorder.mimeType }), 'recording.webm');
  };

  recorder.start();
  button.textContent = 'Stop';
  button.classList.add('recording');
  setStatus('Recording — sing or hum a melody');
};

document.getElementById('upload').onchange = (event) => {
  const file = event.target.files[0];
  if (file) submitVocal(file, file.name);
};

async function submitVocal(blob, filename) {
  setStatus('Analyzing…');

  const preview = document.getElementById('vocal-preview');
  preview.src = URL.createObjectURL(blob);
  preview.hidden = false;

  const form = new FormData();
  form.append('file', blob, filename);

  try {
    const result = await api('/analyze', { method: 'POST', body: form });
    state.sessionId = result.session_id;
    state.analysis = result.analysis;
    setStatus('');
    renderAnalysis();
    renderParts();
    show('step-analysis', 'step-parts', 'step-mix');
    document.getElementById('export').href = `/api/session/${state.sessionId}/export`;
    loadBuffer('vocal', `/api/session/${state.sessionId}/vocal.wav`);
  } catch (error) {
    setStatus('');
    toast(`Analysis failed — ${error.message}`);
  }
}

// --- step 2: analysis + chord editing -----------------------------------

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

function renderAnalysis() {
  const { bpm, key, mode, bars } = state.analysis;

  document.getElementById('stat-bpm').value = bpm.toFixed(1);
  document.getElementById('stat-bars').textContent = bars.length;
  document.getElementById('stat-mode').value = mode;

  const keySelect = document.getElementById('stat-key');
  keySelect.innerHTML = '';
  NOTE_NAMES.forEach((name) => {
    const option = document.createElement('option');
    option.value = option.textContent = name;
    option.selected = name === key;
    keySelect.appendChild(option);
  });

  const grid = document.getElementById('chord-grid');
  grid.innerHTML = '';
  bars.forEach((bar, index) => {
    const input = document.createElement('input');
    input.value = bar.chord;
    input.title = `Bar ${index + 1}`;
    input.oninput = () => { bar.chord = input.value; };
    grid.appendChild(input);
  });
}

document.getElementById('save-analysis').onclick = async () => {
  const bpm = Number(document.getElementById('stat-bpm').value);

  // Changing the tempo re-cuts the bar grid server-side, so the chords we
  // send have to match the *current* bar count. Send them only when the
  // tempo is unchanged; otherwise save the tempo first and let the
  // re-rendered grid be edited after.
  const tempoChanged = Math.abs(bpm - state.analysis.bpm) > 0.05;

  const edit = {
    bpm,
    key: document.getElementById('stat-key').value,
    mode: document.getElementById('stat-mode').value,
  };
  if (!tempoChanged) {
    edit.chords = state.analysis.bars.map((bar) => bar.chord);
  }

  try {
    state.analysis = await api(`/session/${state.sessionId}/analysis`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(edit),
    });
    renderAnalysis();
    toast(
      tempoChanged
        ? 'Tempo saved — bar grid rebuilt, check the chords'
        : 'Saved — applies to the next generation',
    );
  } catch (error) {
    toast(`Could not save — ${error.message}`);
  }
};

// --- step 3: parts ------------------------------------------------------

function renderParts() {
  const container = document.getElementById('parts');
  container.innerHTML = '';

  PARTS.forEach((part) => {
    const row = document.createElement('div');
    row.className = 'part-row';
    row.innerHTML = `
      <span class="part-name">${part}</span>
      <input class="style" placeholder="style, e.g. bossa nova, gritty funk">
      <!-- Range starts at 0.6: below that the model returns the guide
           track essentially unchanged, so lower values are dead UI. -->
      <label class="noise">
        divergence <output>0.80</output>
        <input type="range" min="0.6" max="0.95" step="0.05" value="0.8">
      </label>
      <button class="generate">Generate</button>
      <span class="badge"></span>
    `;

    const noiseSlider = row.querySelector('input[type=range]');
    const noiseOutput = row.querySelector('output');
    noiseSlider.oninput = () => { noiseOutput.textContent = Number(noiseSlider.value).toFixed(2); };

    row.querySelector('.generate').onclick = () =>
      generatePart(part, row, {
        style: row.querySelector('.style').value,
        noise: Number(noiseSlider.value),
      });

    container.appendChild(row);
  });
}

async function generatePart(part, row, { style, noise }) {
  const button = row.querySelector('.generate');
  const badge = row.querySelector('.badge');

  button.disabled = true;

  // Local generation takes tens of seconds — a static label reads as a
  // frozen page, so count up while we wait.
  const startedAt = Date.now();
  button.textContent = 'Generating… 0s';
  const ticker = setInterval(() => {
    button.textContent = `Generating… ${Math.round((Date.now() - startedAt) / 1000)}s`;
  }, 1000);

  try {
    const result = await postJSON('/generate', {
      session_id: state.sessionId,
      part,
      style,
      noise,
      backend: state.backend,
    });

    state.stems[part] = result;

    // The server may have fallen back to a different backend than we asked
    // for. Show what actually ran, and say so if it differs.
    badge.textContent = result.backend_used;
    if (result.backend_used !== state.backend) {
      toast(`${state.backend} unavailable — generated with ${result.backend_used}`);
    }

    await loadBuffer(part, result.audio_url);
    renderTracks();
    button.textContent = `Regenerate (${Math.round((Date.now() - startedAt) / 1000)}s)`;
  } catch (error) {
    toast(`${part} failed — ${error.message}`);
    button.textContent = 'Generate';
  } finally {
    clearInterval(ticker);
    button.disabled = false;
  }
}

// --- step 4: multitrack playback ----------------------------------------

function context() {
  if (!audioContext) audioContext = new AudioContext();
  return audioContext;
}

async function loadBuffer(name, url) {
  const bytes = await (await fetch(url)).arrayBuffer();
  state.buffers[name] = await context().decodeAudioData(bytes);
}

function renderTracks() {
  const container = document.getElementById('tracks');
  container.innerHTML = '';

  ['vocal', ...PARTS.filter((part) => state.stems[part])].forEach((name) => {
    const row = document.createElement('div');
    row.className = 'track';
    row.innerHTML = `
      <span class="track-name">${name}</span>
      <button class="mute">M</button>
      <button class="solo">S</button>
      <input type="range" class="volume" min="0" max="1.5" step="0.05" value="1">
    `;

    const mute = row.querySelector('.mute');
    const solo = row.querySelector('.solo');

    mute.onclick = () => { toggle(state.muted, name); refreshRow(row, name); applyGains(); };
    solo.onclick = () => { toggle(state.soloed, name); refreshRow(row, name); applyGains(); };
    row.querySelector('.volume').oninput = (event) => {
      state.volumes[name] = Number(event.target.value);
      applyGains();
    };

    container.appendChild(row);
  });
}

function refreshRow(row, name) {
  row.querySelector('.mute').classList.toggle('active', state.muted.has(name));
  row.querySelector('.solo').classList.toggle('active', state.soloed.has(name));
}

function toggle(set, name) {
  if (set.has(name)) set.delete(name); else set.add(name);
}

/* A track is audible unless it is muted, or something else is soloed and
 * it is not. Recomputed live so mute/solo work during playback. */
function applyGains() {
  Object.entries(state.gains).forEach(([name, gain]) => {
    const soloActive = state.soloed.size > 0;
    const audible = !state.muted.has(name) && (!soloActive || state.soloed.has(name));
    gain.gain.value = audible ? (state.volumes[name] ?? 1) : 0;
  });
}

document.getElementById('play').onclick = () => {
  stopPlayback();
  const ctx = context();
  const startAt = ctx.currentTime + 0.1;

  Object.entries(state.buffers).forEach(([name, buffer]) => {
    const source = ctx.createBufferSource();
    const gain = ctx.createGain();
    source.buffer = buffer;
    source.connect(gain).connect(ctx.destination);
    // One shared start time is what keeps the stems in sync.
    source.start(startAt);

    state.gains[name] = gain;
    playingSources.push(source);
  });

  applyGains();
};

document.getElementById('stop').onclick = stopPlayback;

function stopPlayback() {
  playingSources.forEach((source) => {
    try { source.stop(); } catch { /* already stopped */ }
  });
  playingSources = [];
}

// --- small helpers ------------------------------------------------------

function show(...ids) {
  ids.forEach((id) => { document.getElementById(id).hidden = false; });
}

function setStatus(text) {
  document.getElementById('record-status').textContent = text;
}

let toastTimer = null;
function toast(message) {
  const element = document.getElementById('toast');
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 4000);
}

loadBackends();
