# Frontend integration test tools

This directory contains developer-facing HTTP integration tests for the React
Studio. They test the frontend/backend API contract rather than browser DOM
behavior.

## Target selection

`web/src/components/Studio.jsx` calls `/api/interpret` and then
`/api/generate` for each returned track. The test runner exercises those shared
backend endpoints using the same request/response shape while remaining
independent of browser DOM and build tooling.

## Run

Start the Python backend in a separate terminal, then run the dependency-free
Node tests or integration tool:

```bash
uv run uvicorn backend.api:app --reload
cd web
npm test
npm run test:integration -- --backend mock
```

`mock` is the default and tests the full HTTP flow through the generation
adapter without loading Stable Audio 3. For an explicit local-MLX SA3 smoke
test, use `--backend local`. Add `--require-deepseek` to fail rather than
accepting the offline rules fallback.

Generated artifacts are written under `web/test/test_run/` and are
ignored by Git. Each run records sanitized requests/responses and exactly two
contract checks: interpretation and generation/audio retrieval. No API keys,
authorization headers, input-audio bytes, or generated WAV bytes are saved.
