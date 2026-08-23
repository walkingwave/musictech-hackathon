# DeepSeek Validation CLI

## Purpose

`deepseek-test` validates the production analysis-to-interpretation path without
creating Stable Audio stems. It analyzes an input recording, derives bounded
musical context (tempo, key, mode, and bar count), then calls
`backend.interpret.interpret_with_source()` with the user request.

It never sends audio bytes, local paths, API keys, or HTTP authorization headers
to DeepSeek or stores them in artifacts.

## Usage

```bash
# Permits the offline rules fallback; useful for local contract smoke tests.
uv run deepseek-test --input samples/fixtures/amin_100.wav \
  --prompt "add upright bass and Rhodes in bossa nova" --expect-tracks

# Requires a successful hosted call using DEEPSEEK_API_KEY from .env.
uv run deepseek-test --input samples/fixtures/amin_100.wav \
  --prompt "add upright bass and Rhodes in bossa nova" \
  --require-deepseek --expect-tracks
```

## Artifacts

Each run creates a unique ignored directory:

```text
backend/test/test_run/deepseek_test_YYYY-MM-DD_HH-MM-SS/
  cleaned.wav
  metadata.json
  deepseek_request.json
  deepseek_response.json
  validation.json
```

`deepseek_request.json` records only the prompt, configured model name, and
bounded derived context. `deepseek_response.json` records the validated `Plan`
and whether the production interpreter used `deepseek` or `rules`.
`validation.json` records the checks and pass/fail status.

## Test plan

- Run artifact unit tests verify timestamp names, collision handling, and safe
  refusal to reuse non-empty explicit output directories.
- Offline interpreter-contract tests verify metadata-to-context mapping and Plan
  validation without credentials or a network request.
- Manual live smoke tests are opt-in via `--require-deepseek`; they must return
  `interpreter: deepseek`, produce valid artifacts, and contain no secrets.
- Exercise full arrangement, unusual instrument, explicit tempo/key override,
  follow-up existing-part, mood-only, and malformed/empty prompt cases.
- Treat a `rules` result as a failure in required mode, but as an expected
  fallback in offline mode.
