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

export const vocalUrl = (sessionId) => `/api/session/${sessionId}/vocal.wav`;
export const exportUrl = (sessionId) => `/api/session/${sessionId}/export`;
