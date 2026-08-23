# Scoped DeepSeek Chat Agent For Song Generation

  ## Summary

  Replace the current ask-bar interpretation flow with a small DeepSeek-backed agent that does the same job as today’s chat input, but through a proper LLM command layer.

  Target behavior:

  - User types: “make me a dreamy bossa nova track with soft drums, upright bass, and Rhodes.”
  - Frontend sends the message plus current session context to backend.
  - Backend calls DeepSeek.
  - DeepSeek returns a structured generation plan.
  - Backend validates it.
  - Frontend executes the returned plan using the existing generation flow.
  - Generated audio is added to the timeline exactly like today.

  This is not yet the full “agent edits timeline/audio regions” system. It is the first scoped step: LLM chat → structured generation plan → existing SA3 API calls → frontend timeline output.

  ## Key Changes

  - Add DeepSeek as the primary interpreter provider.
  - Keep the existing rule/keyword parser as fallback when DEEPSEEK_API_KEY is missing or DeepSeek fails.
  - Stop using web/src/parseRequest.js as the source of truth for chat planning.
  - Keep the current frontend execution model:
      - frontend receives tracks to generate
      - calls existing /api/generate
      - decodes returned audio
      - adds clips to timeline

  - Keep existing SA3 backend unchanged:
      - mock
      - local
      - api

  ## Implementation Changes

  - Backend config:
      - Add env vars:
          - DEEPSEEK_API_KEY
          - BTG_AGENT_PROVIDER=deepseek
          - BTG_AGENT_MODEL=deepseek-v4-flash

      - Use DeepSeek OpenAI-compatible base URL:
          - https://api.deepseek.com

      - Use non-thinking mode for v1:
          - thinking: { "type": "disabled" }

  - Backend agent module:
      - Create/replace interpreter logic with a provider-neutral agent function:
          - input: user text + optional session context
          - output: validated AgentPlan

      - AgentPlan should mirror what the frontend already needs:
          - tracks
          - style
          - groove
          - optional bpm
          - optional key
          - optional mode
          - optional bars
          - notes

      - Each track keeps the existing fields:
          - part
          - name
          - instrument
          - style

  - DeepSeek call:
      - Use OpenAI-compatible SDK or direct httpx.
      - Request JSON output or function/tool calling.
      - Preferred v1: JSON output with strict Pydantic validation after receipt.
      - If JSON parse or validation fails, fall back to current rule parser.
      - If no API key exists, return rule parser output and mark interpreter as rules.

  - API:
      - Keep /api/interpret as the frontend endpoint.
      - Change its implementation so it calls the DeepSeek-backed agent first.
      - Response shape remains compatible with the current frontend:
          - tracks
          - style
          - groove
          - bpm
          - key
          - mode
          - bars
          - notes
          - interpreter

      - interpreter should be:
          - deepseek
          - or rules

  - Frontend:
      - Remove the local keyword parser from the actual execution path.
      - Ask bar sends raw text to /api/interpret.
      - While waiting, show Interpreting….
      - After plan returns, generate each returned track using the existing onGenerateStem flow.
      - Optional preview text can still exist, but it should be clearly marked as local/rough and must not block execution.

  - Prompting:
      - System prompt tells DeepSeek:
          - choose from existing parts only: bass, piano, guitar, drums, harmony, free
          - part means arrangement role, not final instrument
          - preserve existing session tempo/key/style unless user explicitly changes them
          - return only JSON matching the schema
          - if user asks for an unknown instrument, use free unless a role is obvious
          - never invent unsupported operations in this v1

  ## Test Plan

  - Unit tests:
      - Validate DeepSeek JSON output against AgentPlan.
      - Invalid part names are dropped or converted to free.
      - Invalid BPM/key/mode/bars are rejected or ignored.
      - Missing API key falls back to rules.
      - Malformed DeepSeek response falls back to rules.

  - Prompt fixtures:
      - “bass, drums and piano, bossa nova”
      - “make me a dreamy indie song with soft drums and warm bass”
      - “add a xylophone and timpani”
      - “give me four more tracks”
      - “same vibe but slower, in D minor”
      - “add something weird and metallic”
      - “make a full arrangement”

  - Frontend manual test:
      - Start backend and Vite frontend.
      - Type a chat request.
      - Confirm /api/interpret returns interpreter: deepseek.
      - Confirm generated tracks appear on the timeline.
      - Remove DEEPSEEK_API_KEY.
      - Confirm same UI still works with interpreter: rules.

  - Regression tests:
      - Existing /api/generate still works.
      - Existing /api/generate-from-reference still works.
      - Existing timeline add/regen behavior is unchanged.
      - mock backend still allows offline demo.

  ## Assumptions

  - V1 uses hosted DeepSeek, not a fully local model.
  - No model training or fine-tuning is needed.
  - DeepSeek only creates the plan; it does not directly call SA3.
  - The app remains responsible for executing validated commands.
  - Current keyword parsing remains as fallback, not primary behavior.
  - Region/audio-editing agent commands are deferred to the next iteration.