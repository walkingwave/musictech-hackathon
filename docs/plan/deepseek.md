# DeepSeek interpreter integration

## Scope

Replace the current Claude-backed implementation of `POST /api/interpret` with DeepSeek while preserving the existing bounded workflow:

```text
Studio ask bar
  -> POST /api/interpret
  -> validated generation Plan
  -> existing sequential /api/generate calls
  -> timeline clips
```

DeepSeek interprets a plain-language request into a generation plan. It does **not** call Stable Audio 3 directly, execute arbitrary tools, edit regions, or control the frontend.

## Current implementation to preserve

The application already includes the integration surface needed for this change:

- `backend/interpret.py` defines `Plan`, `TrackSpec`, session `Context`, sanitization, and an offline keyword fallback.
- `backend/api.py` provides `POST /api/interpret`, loads session arrangement/analysis context, and provides existing generation routes.
- `web/src/components/Studio.jsx` submits ask-bar text to `/api/interpret`, generates returned tracks sequentially, and adds them to the timeline.
- `web/src/parseRequest.js` is a browser-only fallback if the interpretation request itself fails.
- Supported arrangement roles are `bass`, `piano`, `guitar`, `drums`, `harmony`, and `free`.
- Existing generation supports distinct track names, instrument descriptions, styles, target bars, blank sessions, MIDI-guided generation, reference-audio generation, and SA3 mock/local/API backends.

Do not introduce a new agent endpoint, MCP server, LangChain, LangGraph, or a tool loop for this version.

## Provider design

### Configuration

Add these environment-backed settings:

- `DEEPSEEK_API_KEY`
- `BTG_INTERPRET_MODEL`, default `deepseek-v4-flash`
- `BTG_INTERPRET_BASE_URL`, default `https://api.deepseek.com`

Use the existing `httpx` dependency for the OpenAI-compatible Chat Completions endpoint. No OpenAI SDK dependency is required.

### Request and response

Use non-thinking mode and JSON output. The request must include:

- the existing musical system prompt;
- a compact session `Context` when supplied;
- the user request;
- explicit instruction to return JSON only;
- a representative JSON shape for `Plan`.

Deserialize the model response into `Plan`, then always call `_sanitize()`. If the API key is unavailable or the request times out, returns a non-success status, produces empty/malformed JSON, or fails Pydantic validation, return the existing rules result.

`interpret()` should return both the plan and its actual source:

```python
plan, interpreter = interpret(text, context)
# interpreter is "deepseek" or "rules"
```

`POST /api/interpret` returns that actual source in its `interpreter` field; credential presence is not evidence that a DeepSeek result was used.

## Plan application rules

A valid interpreted plan is executed only through existing validated backend endpoints.

Before generating tracks, the Studio must persist valid `plan.bpm`, `plan.key`, and `plan.mode` through `PATCH /api/session/{session_id}/analysis`. Updating React state alone is insufficient because guides are built from server-side `Session.analysis`.

Pass `plan.bars` to each `onGenerateStem` call. Continue passing the combined style, `spec.name`, and `spec.instrument`. Generate sequentially because the local SA3 backend is single-instance.

For v1, valid tempo/key/mode changes apply to new generations only; existing rendered stems are not modified. The UI should state any applied changes.

## Validation and safety

- Treat all model output as untrusted input.
- Keep `_sanitize()` as the vocabulary/range boundary for parts, grooves, BPM, mode, and bars.
- Retain server-side request validation in `/api/generate`.
- Do not expose audio bytes, local filesystem paths, credentials, arbitrary HTTP, or arbitrary file operations to the model.
- Continue using existing SA3 backend selection and fallback behavior unchanged.

## Test plan

Add tests for:

1. Valid DeepSeek JSON parsing and subsequent sanitization.
2. Missing credentials, timeout, non-success HTTP responses, empty response bodies, malformed JSON, and invalid schemas falling back to rules.
3. Unsupported part/groove and invalid BPM/bars/mode values being sanitized.
4. Accurate `interpreter: "deepseek" | "rules"` reporting.
5. Interpreted BPM/key/mode being persisted before a Studio generation.
6. Interpreted `bars` reaching generation requests.

Manual acceptance checks:

- A dreamy bossa nova request with Rhodes, upright bass, and soft drums creates valid named tracks.
- Xylophone and timpani map to valid arrangement roles and retain distinct instrument descriptions.
- “Same vibe, slower, in D minor, 16 bars” persists the new analysis and generates 16-bar tracks.
- Without `DEEPSEEK_API_KEY`, the ask bar still works through rules.
- Mock, local, and hosted SA3 backends continue to work.

## Deferred work

Defer MCP, LangChain, LangGraph, model tool calls, durable conversation history, job orchestration, generation approval flows, and agent-controlled timeline/audio edits. They are not required for the scoped DeepSeek planner.

## Primary API references

- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [DeepSeek Models and Pricing](https://api-docs.deepseek.com/quick_start/pricing?tool=deepseek)
