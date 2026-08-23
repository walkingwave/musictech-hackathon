# Plan: Faithful Hum-to-MIDI Transcription

## Goal

Make the hummed performance recognizable in the editable MIDI and deterministic guide WAV. Stable Audio rendering remains optional sound design; it is not the source-of-truth melody output.

This plan follows the findings in [`docs/research/pitch-midi.md`](../research/pitch-midi.md).

## Non-goals

- Do not claim that Stable Audio will deterministically reproduce every MIDI note.
- Do not replace the existing backing-stem pipeline in this work.
- Do not introduce MT3 as a default runtime.
- Do not silently “correct” a hummed note to the detected key or grid in faithful mode.

## Phase 1 — Make the current path observable

1. Add a serializable tracker result type in `backend/melody.py`:
   - raw frame timestamps;
   - raw F0 in Hz/MIDI;
   - voiced flag and confidence;
   - final segmented notes;
   - rejection/merge reason counts.
2. Change `melody.track()` to return or optionally expose that result while retaining a compatibility helper for existing `list[Note]` callers in `backend/analysis.py`, `backend/arrange.py`, and `backend/pipeline.py`.
3. Persist tracker diagnostics beside `hum_notes` in `backend/session.py` and include a bounded diagnostic summary in `/api/analyze` in `backend/api.py`.
4. Add an analysis-only endpoint or route response for the frontend to display a pitch timeline and warnings such as “only one stable pitched note was found.” Never expose model secrets or arbitrary local paths.

**Acceptance:** a failed hum session can identify whether notes were lost due to unvoiced frames, confidence rejection, duration filtering, or same-pitch merging.

## Phase 2 — Preserve the user’s performance by default

1. In `backend/hum_transform.py`, introduce explicit transform options:
   - `faithful`: preserve detected MIDI pitch, onset, duration, rests, and register;
   - `snap_to_key`: optional nearest-scale-pitch correction;
   - `quantize`: optional timing grid and strength;
   - `bass`: separate intentional bass simplification/low-register mapping.
2. Make `faithful` melody the default. Remove unconditional scale snapping and 1/8-beat timing quantization from this mode.
3. Extend `HumGenerateRequest` in `backend/api.py` and `web/src/api.js` with validated transform options.
4. In `web/src/components/InputView.jsx`, show **Faithful MIDI** as the default and place key snapping/quantization behind an advanced control. Explain that bassline conversion is intentionally less literal.
5. Write the pre-SA3 faithful MIDI and guide immediately. Permit users to download or edit them even when SA3 is unavailable or fails.

**Acceptance:** a manually inspected hum with three detected notes produces three same-pitch MIDI events with matching timing before any optional correction.

## Phase 3 — Establish a benchmark

1. Create a consented, ignored local evaluation manifest and a documented recording protocol; do not commit private voices.
2. Add a small checked-in synthetic suite for regression: repeated same pitch with a re-attack, stepwise motion, leap, short note, vibrato, and slide.
3. Implement an evaluation command, for example `uv run pitch-midi-eval --manifest <path>`, reporting pitch F1, onset F1 (50/100 ms), offset overlap, octave errors, runtime, and event count.
4. Add unit tests for segmentation edge cases in `backend/test/` and retain `scripts/eval_analysis.py` as a tempo/key regression gate.

**Acceptance:** baseline pYIN metrics are recorded before any tracker replacement; every tracker is evaluated on identical clips and tolerances.

## Phase 4 — Add pluggable tracker backends

1. Define a `PitchTracker` protocol in a new `backend/pitch_tracking.py` with a common `track(audio, sr) -> TrackingResult` contract.
2. Move current pYIN behavior into `PyinTracker`; keep it dependency-free and selectable as fallback.
3. Add an optional `BasicPitchTracker` adapter:
   - install only through a dedicated optional dependency group in `pyproject.toml`;
   - normalize Basic Pitch note output into the common event type;
   - capture model/version/runtime provenance;
   - fail clearly and fall back to pYIN when unavailable.
4. Add an optional `TorchCrepeTracker` adapter only if the benchmark shows F0 contour errors are the dominant issue:
   - retain its confidence curve;
   - segment using voiced/unvoiced hysteresis and a minimum pitch-change persistence duration;
   - do not use a single global median filter as the only note-boundary mechanism.
5. Add `BTG_PITCH_TRACKER=auto|basic-pitch|torchcrepe|pyin`; `auto` chooses an installed validated backend, otherwise pYIN.

**Acceptance:** unavailable optional models never prevent recording/analysis; tracker choice and version are saved in `meta.json`.

## Phase 5 — Decide from evidence

1. Run pYIN, Basic Pitch, and (if implemented) torchcrepe over the benchmark suite.
2. Promote a default only if it improves note and onset F1 without unacceptable cold start or CPU latency on the target Mac.
3. Test browser WebM/Opus and WAV inputs separately; decode/preprocess failures must not be mistaken for transcription failure.
4. Sweep Stable Audio noise values independently, comparing guide MIDI/WAV against generated audio. Label low-noise renders “more faithful” and high-noise renders “more creative,” not “better transcription.”

**Acceptance:** the selected default, metrics, hardware/runtime, and known failure modes are documented in `README.md`.

## Rollout order

1. Observability and faithful-mode transform.
2. Regression/evaluation harness.
3. Basic Pitch spike behind an optional dependency.
4. Promote or reject Basic Pitch using measured results.
5. Consider torchcrepe only if Basic Pitch does not solve contour fidelity.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Lower confidence admits noise as notes | retain diagnostics; compare precision and recall; expose MIDI editing |
| Key/grid correction changes the user’s tune | faithful mode defaults off for both corrections |
| Model package/model weights hurt setup or latency | optional extras, lazy loading, pYIN fallback |
| SA3 changes a correct guide | deliver MIDI/guide first; test SA3 separately at lower noise |
| Real recordings differ from synthetic fixtures | benchmark consented browser-recorded hums and report per-condition metrics |
