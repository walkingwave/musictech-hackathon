# Research: Agentic SA3 integration with DeepSeek, LangChain, LangGraph, and MCP

**Research question.** How can this project add a session-aware agent that uses a model API (primarily DeepSeek) to orchestrate the existing Stable Audio 3 (SA3) workflow, optionally through LangChain, LangGraph, and/or MCP?

**Date conducted:** 2026-08-22

## Executive summary

The application already contains the important deterministic audio pipeline: vocal analysis → MIDI arrangement → guide rendering → SA3 audio-to-audio generation → alignment. An LLM should not replace that pipeline. It should be a session-aware musical copilot: interpret natural-language intent, inspect compact session metadata, propose musical changes, and selectively invoke tightly constrained backend tools.

**Recommended MVP:** add a small server-side tool-calling loop using **DeepSeek V4 Flash** as the default model. The loop calls allowlisted Python functions that wrap existing session, analysis-edit, and generation operations. Keep domain truth in the existing session metadata and send the model a bounded textual summary—not WAV files.

Do **not** add MCP, LangChain, or LangGraph as a prerequisite for the MVP:

- Add **LangGraph** when generation jobs need durable checkpoints, approval pauses, retries, cancellation, or branching.
- Add **MCP** when the same music tools need to be usable from external MCP clients, such as Claude Desktop, IDE agents, or a DAW integration.
- Use **LangChain** only if its provider portability, middleware, and integrations justify its extra abstraction; it is not required to call DeepSeek tools.

DeepSeek’s official published prices for V4 Flash are much lower than Anthropic’s published API prices for interactive text/tool orchestration. SA3 generation is a separate cost center and may dominate usage cost; an authoritative public per-generation Stability price was not verified, so it should be measured from the applicable account/API before being presented to users.

## Project grounding

The following current implementation characteristics shape the recommendation:

- `backend/pipeline.py` defines the deterministic pipeline and `generate_stem()` operation.
- `backend/sa3_backend.py` encapsulates mock, local MLX, and Stability-hosted generation, includes caching for identical hosted requests, and handles fallbacks.
- `backend/session.py` persists vocal, guides, stems, MIDI, analysis, prompts, seeds, noise, and backend provenance per session.
- `backend/api.py` already exposes analysis, analysis editing, generation, session retrieval, audio serving, and export routes.
- The maintained React app (`web/src/App.jsx`, `web/src/api.js`) has no conversational state or job/progress protocol; generation is currently sequenced client-side.
- `backend/config.py` centralizes the Stability SA3 API endpoint. Keep this server-side credential boundary intact.

## Feasible architectures

### 1. Custom server-side tool loop — recommended first

```text
React chat / assistant panel
  -> FastAPI POST /api/assistant/messages
      -> load compact SessionSummary
      -> DeepSeek tool-call request
      -> validate and execute allowlisted Python tools
      -> return tool result to model (maximum 2–3 iterations)
      -> stream assistant response and structured UI actions
```

Use DeepSeek’s native/OpenAI-compatible tool-calling protocol; the application executes functions, not the model [5].

**Benefits:** minimal dependencies; preserves the existing backend; inexpensive and quick to test; easy to bound and audit.

**Costs:** the project owns the bounded loop, session compaction, tracing, and model-provider adapter.

### 2. LangChain agent over the same internal tools

LangChain provides a configurable model-and-tools harness with middleware and integrations, built on LangGraph [2]. It is appropriate if changing between DeepSeek, Anthropic, and other providers is a near-term product requirement.

**Benefits:** tool abstractions, provider integrations, middleware, and optional LangSmith tracing.

**Costs:** dependency/abstraction overhead. Test DeepSeek against its OpenAI-compatible endpoint rather than assuming a dedicated integration has all needed features.

### 3. LangGraph workflow — add after the MVP

A suitable graph is:

```text
load_session -> assistant_decide -> validate_action
  -> [read/update analysis | draft generation | confirm costly generation]
  -> run_generation_job -> refresh_session -> assistant_reply -> END
```

LangGraph is a low-level orchestration/runtime layer for stateful, long-running workflows with persistence, durable execution, streaming, and human interruption [1]. It can combine deterministic and model-driven nodes.

**Use it when:** users can queue multiple stems, leave and return during generation, revise analysis while a job is in progress, or need cancellation/resume/retry and auditable approval states.

**Do not use it yet if:** the feature is a single synchronous chat/tool loop. It does not improve audio quality or make tools safe by itself.

### 4. MCP server — an interoperability adapter, not an agent loop

MCP tools are discoverable with `tools/list`, callable with `tools/call`, and described using JSON Schema [3]. A local or remote `btg-mcp` server can expose the music capabilities to external MCP clients.

**Use it when:** Claude Desktop, IDE agents, DAWs, or other independent clients should reuse the same music tools.

**Do not use it as the primary MVP architecture:** MCP provides neither session memory, job scheduling, UI, nor agent orchestration. DeepSeek can invoke ordinary function tools directly.

**Long-term shape:**

```text
FastAPI domain service -> internal typed tool functions -> optional MCP adapter
                                               -> provider adapters / LangGraph orchestration
```

MCP tools must not call the React frontend or expose server filesystem paths.

## Recommended phased implementation

### Phase 0: preserve the deterministic system

1. Keep `/api/generate` and `pipeline.generate_stem()` authoritative.
2. Add regression tests for tool-argument validation and confirm every assistant action maps to an existing domain operation.
3. Continue exposing the chosen mock/local/API SA3 backend and fallback behavior to the user.

### Phase 1: minimal assistant without a framework

Add:

- `POST /api/assistant/messages`.
- A server-only `Provider` interface such as `complete(messages, tools)`.
- A DeepSeek adapter using `https://api.deepseek.com`.
- A bounded loop: at most three model calls and at most one expensive-generation *proposal* per user turn.
- Server-Sent Events or WebSockets for generation progress.

Initial tools should be read-only or low-risk: explain analysis, list tracks, suggest a style/prompt, compare provenance, and propose a chord-grid edit. The agent must not silently generate paid hosted audio.

### Phase 2: user-controlled generation

Implement a draft/approval sequence:

1. The model calls `propose_generation`.
2. The backend returns an immutable preview: estimated duration, selected backend, prompt, part, noise, seed policy, and cost-status warning.
3. React renders an approval card.
4. Only an explicit user interaction calls `generate_stem`.

This aligns with the project’s existing editable-analysis and per-track regeneration model.

### Phase 3: durable orchestration

Adopt LangGraph if multi-step/long-running behavior needs persistence. Persist checkpoints by `session_id` and `conversation_id`; do not rely exclusively on in-memory Python state.

### Phase 4: optional MCP adapter

Expose the already-approved internal tool contracts through an authenticated MCP server. The MCP specification recommends user confirmation for sensitive operations and calls for validation, access controls, rate limits, sanitization, timeouts, and audit logging [3].

## Session, context, and audio design

Keep three separate kinds of state:

| Layer | Contents | Suggested storage |
|---|---|---|
| Domain session | Existing analysis, stem provenance, audio/MIDI references | Existing `sessions/<id>/meta.json` and current files |
| Conversation state | Recent messages, compact preferences, pending confirmation, provider/model metadata | Database or JSON store, keyed by `session_id` + conversation ID |
| Job state | Queued/running/succeeded/failed/cancelled generation and progress | Durable job table/store |

Pass a compact `SessionSummary` to the LLM, for example:

```json
{
  "session_id": "opaque-id",
  "analysis": {"bpm": 100, "key": "A", "mode": "minor", "bars": ["Am", "F", "C", "G"]},
  "tracks": [{"part": "bass", "exists": true, "prompt": "...", "backend": "local"}],
  "capabilities": {"available_backends": ["mock", "local", "api"]},
  "preferences": {"style": "bossa nova"}
}
```

Do not put WAV bytes, waveform arrays, local paths, API keys, or unrestricted audio URLs in model prompts. “Audio-track access” should mean the model sees metadata and can request a **server-mediated** summary or preview. If actual audio understanding is required later, add an explicit `describe_track` service that returns bounded descriptors such as duration, loudness, spectral/onset features, and existing analysis. Evaluate a dedicated audio-capable model separately; the DeepSeek documentation reviewed here did not verify audio input support [4].

Keep a stable prompt prefix (system policy followed by session summary in a fixed order) to improve DeepSeek cache reuse. Its cache is automatic and best-effort, depends on matching prefix units, and can clear after hours or days [6].

## Suggested tool contracts and controls

Expose small capability-oriented tools. Never expose generic shell execution, arbitrary HTTP, arbitrary file access, or paths.

```json
{
  "name": "get_session_summary",
  "input_schema": {
    "type": "object",
    "properties": {"session_id": {"type": "string"}},
    "required": ["session_id"],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "update_analysis_draft",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "bpm": {"type": "number", "minimum": 20, "maximum": 300},
      "key": {"type": "string"},
      "mode": {"type": "string", "enum": ["major", "minor"]},
      "chords": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["session_id"],
    "additionalProperties": false
  }
}
```

```json
{
  "name": "propose_generate_stem",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "part": {"type": "string", "enum": ["bass", "piano", "drums", "harmony"]},
      "style": {"type": "string"},
      "noise": {"type": "number", "minimum": 0.6, "maximum": 1.0},
      "backend": {"type": "string", "enum": ["mock", "local", "api"]}
    },
    "required": ["session_id", "part", "style", "backend"],
    "additionalProperties": false
  }
}
```

Required safety controls:

- Authorize ownership of every `session_id`; use opaque IDs.
- Treat model tool arguments as untrusted and revalidate them with Pydantic/domain rules.
- Clamp prompt lengths, numeric bounds, parts, duration, and tool-call count.
- Require user approval for hosted SA3 calls, export, overwrite, deletion, and external uploads.
- Queue and rate-limit SA3 calls per user/session; reuse the existing identical-request cache.
- Return structured results, job IDs, and safe audio asset IDs—not local paths or raw provider errors.
- Log provider/model, token counts, tool arguments/results, approval actor, actual backend, seed, and failure reason.
- Present clear UI states: assistant proposed → user approved → running → completed.

DeepSeek strict tool mode is beta and requires the beta base URL and schemas where all object properties are required and `additionalProperties` is `false` [5]. Use normal server-side validation as the production safety boundary even when strict mode is enabled.

## Provider and cost decision framework

### Default: DeepSeek V4 Flash

DeepSeek’s official documentation currently lists V4 Flash off-peak/peak prices per million tokens as cache-hit input **$0.007/$0.014**, cache-miss input **$0.22/$0.44**, and output **$0.66/$1.32**. It lists tool calls, JSON output, and a 1M-token context window; prices are explicitly subject to change [4].

Use Flash for ordinary music chat, tool selection, summaries, and prompt drafting. Escalate to V4 Pro only after measuring quality failures; its listed token prices are roughly three times Flash [4]. Keep responses concise and session context compact. SA3 runtime/credits, rather than orchestration tokens, may be the dominant per-generation cost.

### Claude: quality/portability fallback

Anthropic supports application-executed client tools and an MCP connector [7]. Its official list prices reviewed here show Claude Haiku 4.5 at **$1 input / $5 output per million tokens**, Sonnet 5 at **$2 / $10**, and cache reads at 10% of base input cost [8]. On the published figures, Claude is not the low-cost default compared with DeepSeek Flash, but is a practical fallback for higher-quality interactions, tool reliability experiments, or MCP-first deployment.

### Local / no-LLM baseline

For direct commands that map to existing UI controls (for example, “generate bass in bossa nova”), a deterministic API route is cheaper and more reliable than calling an LLM solely to route the action. Retain local SA3 Small/MLX where hardware and model access make it feasible.

### SA3 feasibility

Stability’s SA3 repository describes Small-Music as CPU-capable (433M parameters, up to 120 seconds), Medium as CUDA-only (1.4B, up to 380 seconds), and Large as API-only (2.7B, up to 380 seconds). It supports text-to-audio, audio-to-audio, inpainting, and continuation [9]. These capabilities support the project’s guide-audio approach.

No authoritative public Stability per-generation pricing was verified. Do not publish an estimate until it is checked in the applicable Stability account/API documentation. Instrument each hosted request with duration and billing/credit information when available, then set user-visible budgets from observed use.

## Limitations and uncertainty

- An LLM cannot guarantee harmonic correctness, tempo lock, isolated stems, or audio quality. The existing guide rendering and `align.align()` behavior remain important.
- The current 4/4 assumption and weak solo-vocal chord inference are domain limits, not agent-orchestration problems.
- Provider pricing, model names, rate limits, SA3 availability, and licensing can change; verify them before launch.
- MCP annotations and tool results are not inherently trustworthy; the specification says annotations should be treated as untrusted unless from trusted servers [3].
- Hosted audio creates possible privacy, copyright, consent, retention, and commercial-license obligations. Obtain informed user consent and review current Stability terms before sending recordings.

## Methodology and source-selection notes

This research combined a read-only review of the implementation files listed above with official primary documentation for LangGraph, LangChain, MCP, DeepSeek, Anthropic, and SA3. Official product/specification documentation was prioritized because the question concerns current APIs, prices, and runtime behavior. No peer-reviewed research was needed for the implementation recommendation; the SA3 paper below is an arXiv preprint and peer review was not established.

## References

1. **“LangGraph overview.”** LangChain, accessed 2026-08-22. Official documentation; not peer-reviewed. https://docs.langchain.com/oss/python/langgraph/overview
2. **“LangChain overview.”** LangChain, accessed 2026-08-22. Official documentation; not peer-reviewed. https://docs.langchain.com/oss/python/langchain/overview
3. **“Tools: Model Context Protocol Specification, version 2025-06-18.”** Model Context Protocol, 2025-06-18. Official specification; not peer-reviewed. https://modelcontextprotocol.io/specification/2025-06-18/server/tools
4. **“Models & Pricing.”** DeepSeek API Docs, accessed 2026-08-22. Official documentation; not peer-reviewed. https://api-docs.deepseek.com/quick_start/pricing
5. **“Tool Calls.”** DeepSeek API Docs, accessed 2026-08-22. Official documentation; not peer-reviewed. https://api-docs.deepseek.com/guides/tool_calls
6. **“Context Caching.”** DeepSeek API Docs, accessed 2026-08-22. Official documentation; not peer-reviewed. https://api-docs.deepseek.com/guides/kv_cache
7. **“Tool use with Claude.”** Anthropic Claude Platform Docs, accessed 2026-08-22. Official documentation; not peer-reviewed. https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
8. **“Pricing.”** Anthropic Claude Platform Docs, accessed 2026-08-22. Official documentation; not peer-reviewed. https://platform.claude.com/docs/en/about-claude/pricing
9. **“Stable Audio 3.”** Stability AI, GitHub repository, accessed 2026-08-22. Official repository/documentation; not peer-reviewed. https://github.com/Stability-AI/stable-audio-3
10. **Evans, Zach et al. “Stable Audio 3.”** arXiv:2605.17991, 2026. Scholarly technical preprint; peer-review status not established. https://arxiv.org/abs/2605.17991
