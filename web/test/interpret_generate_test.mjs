#!/usr/bin/env node
/**
 * HTTP integration test for the Studio flow:
 * fixture upload -> /api/analyze -> /api/interpret -> /api/generate -> WAV.
 *
 * The default mock backend runs the whole backend generation path without
 * loading SA3 weights. Use --backend local only for an intentional MLX SA3
 * smoke test.
 */

import { readFile } from 'node:fs/promises';
import { basename, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRunDirectory, writeJson } from './run_artifacts.mjs';

const REPO_ROOT = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const DEFAULT_FIXTURE = resolve(REPO_ROOT, 'samples/fixtures/amin_100.wav');
const DEFAULT_PROMPT = 'add an upright bass';
const PARTS = new Set(['bass', 'piano', 'guitar', 'drums', 'harmony', 'free']);

export function parseArgs(argv) {
  const options = {
    baseUrl: 'http://127.0.0.1:8000',
    backend: 'mock',
    fixture: DEFAULT_FIXTURE,
    prompt: DEFAULT_PROMPT,
    requireDeepseek: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === '--require-deepseek') options.requireDeepseek = true;
    else if (['--base-url', '--backend', '--fixture', '--prompt'].includes(value)) {
      const next = argv[++index];
      if (!next) throw new Error(`${value} requires a value`);
      options[({ '--base-url': 'baseUrl', '--backend': 'backend', '--fixture': 'fixture', '--prompt': 'prompt' })[value]] = next;
    } else if (value === '--help' || value === '-h') {
      options.help = true;
    } else {
      throw new Error(`unknown option: ${value}`);
    }
  }
  if (!['mock', 'local', 'api'].includes(options.backend)) {
    throw new Error('--backend must be mock, local, or api');
  }
  return options;
}

export function selectTrack(plan) {
  if (!Array.isArray(plan.tracks)) throw new Error('interpret response has no tracks array');
  const track = plan.tracks.find((candidate) => PARTS.has(candidate.part));
  if (!track) throw new Error('interpret response has no supported track');
  return track;
}

export function validateInterpretation(plan, { requireDeepseek }) {
  const failures = [];
  for (const field of ['bpm', 'key', 'mode', 'bars']) {
    if (!Object.hasOwn(plan, field)) failures.push(`interpret response lacks ${field}`);
  }
  if (plan.bpm != null && (!Number.isFinite(plan.bpm) || plan.bpm < 20 || plan.bpm > 300)) {
    failures.push('bpm must be between 20 and 300 when specified');
  }
  if (plan.key != null && !String(plan.key).trim()) failures.push('key must be non-empty when specified');
  if (plan.mode != null && !['major', 'minor'].includes(plan.mode)) {
    failures.push('mode must be major or minor when specified');
  }
  if (plan.bars != null && (!Number.isInteger(plan.bars) || plan.bars < 1 || plan.bars > 128)) {
    failures.push('bars must be an integer between 1 and 128 when specified');
  }
  if (!['deepseek', 'rules'].includes(plan.interpreter)) {
    failures.push('interpreter must be deepseek or rules');
  }
  if (requireDeepseek && plan.interpreter !== 'deepseek') {
    failures.push('DeepSeek was required but the rules fallback ran');
  }
  try {
    const track = selectTrack(plan);
    if (!String(track.name || '').trim()) failures.push('selected track has an empty name');
  } catch (error) {
    failures.push(error.message);
  }
  return failures;
}

export function buildGenerateRequest(sessionId, plan, track, backend) {
  return {
    session_id: sessionId,
    part: track.part,
    style: [plan.style, track.style].filter(Boolean).join(', ') || undefined,
    name: track.name,
    instrument: track.instrument || '',
    bars: plan.bars ?? undefined,
    backend,
    seed: 424242,
  };
}

export function validateGeneration(result, request, audio) {
  const failures = [];
  if (result.part !== request.part) failures.push(`part mismatch: expected ${request.part}, got ${result.part}`);
  if (!result.name || !result.audio_url) failures.push('generation response lacks name or audio_url');
  if (result.backend_used !== request.backend) {
    failures.push(`backend mismatch: requested ${request.backend}, used ${result.backend_used}`);
  }
  if (!audio?.contentType?.includes('audio/wav')) failures.push('stem URL did not return audio/wav');
  if (!(audio?.bytes > 44)) failures.push('stem WAV response is empty or too short');
  return failures;
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.text();
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}: ${body}`);
  return JSON.parse(body);
}

async function analyze(baseUrl, fixture) {
  const bytes = await readFile(fixture);
  const form = new FormData();
  form.append('file', new Blob([bytes], { type: 'audio/wav' }), basename(fixture));
  return fetchJson(new URL('/api/analyze', baseUrl), { method: 'POST', body: form });
}

async function fetchAudio(baseUrl, audioUrl) {
  const response = await fetch(new URL(audioUrl, baseUrl));
  const bytes = (await response.arrayBuffer()).byteLength;
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}: could not retrieve generated WAV`);
  return { contentType: response.headers.get('content-type') || '', bytes };
}

export async function run(options) {
  const output = await createRunDirectory('frontend_interpret_generate_test');
  try {
    const analysis = await analyze(options.baseUrl, options.fixture);
    const interpretRequest = { text: options.prompt, session_id: analysis.session_id };
    await writeJson(output, 'request_interpret.json', interpretRequest);
    const plan = await fetchJson(new URL('/api/interpret', options.baseUrl), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(interpretRequest),
    });
    await writeJson(output, 'response_interpret.json', plan);

    const interpretationFailures = validateInterpretation(plan, options);
    const track = interpretationFailures.length ? null : selectTrack(plan);
    let generationFailures = ['generation was not attempted because interpretation validation failed'];
    let generated = null;
    let generateRequest = null;
    let audio = null;
    if (track) {
      generateRequest = buildGenerateRequest(analysis.session_id, plan, track, options.backend);
      await writeJson(output, 'request_generate.json', generateRequest);
      generated = await fetchJson(new URL('/api/generate', options.baseUrl), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(generateRequest),
      });
      await writeJson(output, 'response_generate.json', generated);
      audio = await fetchAudio(options.baseUrl, generated.audio_url);
      generationFailures = validateGeneration(generated, generateRequest, audio);
    }

    const checks = [
      { name: 'interpretation contract', passed: !interpretationFailures.length, failures: interpretationFailures },
      { name: 'generation contract', passed: !generationFailures.length, failures: generationFailures },
    ];
    const passed = checks.every((check) => check.passed);
    await writeJson(output, 'validation.json', {
      schema_version: 1,
      passed,
      backend: options.backend,
      interpreter: plan.interpreter,
      checks,
      audio,
    });
    return { output, passed, checks, interpreter: plan.interpreter, backendUsed: generated?.backend_used };
  } catch (error) {
    await writeJson(output, 'validation.json', {
      schema_version: 1, passed: false, error_type: error.constructor.name, error: error.message,
    });
    return { output, passed: false, checks: [{ name: 'runner', passed: false, failures: [error.message] }] };
  }
}

function usage() {
  console.log('node web/test/interpret_generate_test.mjs [--base-url URL] [--backend mock|local|api]');
  console.log('  [--fixture PATH] [--prompt TEXT] [--require-deepseek]');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) {
      usage();
    } else {
      const result = await run(options);
      console.log(`web integration test: ${result.output}`);
      console.log(`  interpreter: ${result.interpreter || 'not reached'}`);
      console.log(`  backend: ${result.backendUsed || 'not reached'}`);
      console.log(`  validation: ${result.passed ? 'passed' : 'failed'}`);
      if (!result.passed) process.exitCode = 1;
    }
  } catch (error) {
    console.error(`web integration test failed: ${error.message}`);
    process.exitCode = 1;
  }
}
