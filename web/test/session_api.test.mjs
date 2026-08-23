import test from 'node:test';
import assert from 'node:assert/strict';

import { deleteSession, getSession, listSessions } from '../src/api.js';

function response(body, status = 200) {
  return { ok: status < 400, status, text: async () => JSON.stringify(body), json: async () => body };
}

test('session API client uses the expected list, load, and delete routes', async () => {
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (path, options = {}) => {
    calls.push([path, options]);
    return response(path === '/api/sessions' ? [] : { id: 'abcdef123456' });
  };
  try {
    await listSessions();
    await getSession('abcdef123456');
    await deleteSession('abcdef123456');
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(calls.map(([path]) => path), [
    '/api/sessions', '/api/session/abcdef123456', '/api/session/abcdef123456',
  ]);
  assert.equal(calls[2][1].method, 'DELETE');
});
