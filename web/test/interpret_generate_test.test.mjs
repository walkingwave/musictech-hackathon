import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildGenerateRequest,
  parseArgs,
  selectTrack,
  validateGeneration,
  validateInterpretation,
} from './interpret_generate_test.mjs';

const plan = {
  interpreter: 'deepseek',
  style: 'bossa nova',
  bpm: 92,
  key: 'D',
  mode: 'minor',
  bars: 8,
  tracks: [{ part: 'bass', name: 'upright bass', instrument: 'upright bass', style: '' }],
};

test('selects a supported interpreted track and builds the Studio-shaped request', () => {
  const track = selectTrack(plan);
  assert.deepEqual(buildGenerateRequest('session-1', plan, track, 'mock'), {
    session_id: 'session-1', part: 'bass', style: 'bossa nova', name: 'upright bass',
    instrument: 'upright bass', bars: 8, backend: 'mock', seed: 424242,
  });
});

test('requires DeepSeek only when explicitly requested', () => {
  assert.deepEqual(validateInterpretation({ ...plan, interpreter: 'rules' }, { requireDeepseek: false }), []);
  assert.match(
    validateInterpretation({ ...plan, interpreter: 'rules' }, { requireDeepseek: true }).join(' '),
    /DeepSeek was required/,
  );
});

test('requires and validates DeepSeek musical settings', () => {
  assert.match(
    validateInterpretation({ ...plan, bars: 0 }, { requireDeepseek: false }).join(' '),
    /bars must be an integer/,
  );
  const { bpm: _bpm, ...missingBpm } = plan;
  assert.match(
    validateInterpretation(missingBpm, { requireDeepseek: false }).join(' '),
    /lacks bpm/,
  );
});

test('generation validation rejects the wrong backend or non-WAV response', () => {
  const request = buildGenerateRequest('session-1', plan, plan.tracks[0], 'mock');
  const failures = validateGeneration(
    { part: 'bass', name: 'upright bass', audio_url: '/api/stem.wav', backend_used: 'local' },
    request,
    { contentType: 'application/json', bytes: 2 },
  );
  assert.equal(failures.length, 3);
});

test('parses safe runner options', () => {
  assert.deepEqual(parseArgs(['--backend', 'local', '--require-deepseek']).backend, 'local');
  assert.throws(() => parseArgs(['--backend', 'invalid']), /mock, local, or api/);
});
