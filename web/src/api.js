// Thin wrapper over the backend's /api routes. Mirrors the contract in
// PLAN.md. Every call throws Error("<status>: <detail>") on failure so the
// UI can surface it in a toast.

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

export const listBackends = () => api('/backends');

export async function analyze(blob, filename) {
  const form = new FormData();
  form.append('file', blob, filename);
  return api('/analyze', { method: 'POST', body: form });
}

export const updateAnalysis = (sessionId, edit) =>
  api(`/session/${sessionId}/analysis`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(edit),
  });

export const generate = (body) => postJSON('/generate', body);

// Start a session with no source audio, for composing from nothing.
export const createBlankSession = (body) => postJSON('/session/blank', body);

// Interpret a plain-English request into a generation plan. The server uses
// Claude when it has credentials and falls back to keyword matching, so this
// always returns a plan rather than failing.
// Played notes + a prompt -> a real instrument. The notes become the guide
// track, so the performance survives and only the timbre is generated.
export const generateFromMidi = (body) => postJSON('/generate-from-midi', body);

export const interpret = (text, sessionId) =>
  postJSON('/interpret', { text, session_id: sessionId ?? null });

// Generate a clip guided by audio the user picked, rather than by a guide
// track synthesized from the chord grid. `referenceWav` is a Blob.
export async function generateFromReference({
  sessionId,
  referenceWav,
  prompt,
  noise,
  backend,
  seed,
  name,
}) {
  const form = new FormData();
  form.append('session_id', sessionId);
  form.append('prompt', prompt ?? '');
  if (noise != null) form.append('noise', String(noise));
  if (backend) form.append('backend', backend);
  if (seed != null) form.append('seed', String(seed));
  form.append('name', name || 'clip');
  form.append('audio', referenceWav, 'reference.wav');
  return api('/generate-from-reference', { method: 'POST', body: form });
}

export const vocalUrl = (sessionId) => `/api/session/${sessionId}/vocal.wav`;
export const stemUrl = (sessionId, part) =>
  `/api/session/${sessionId}/audio/stems/${part}.wav`;
export const exportUrl = (sessionId) => `/api/session/${sessionId}/export`;
