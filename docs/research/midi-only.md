# MIDI-Only Hum Transformation Research

## Scope

**Question:** Should the hum transformation feature stop sending a prompt/guide to Stable Audio 3 and instead return usable MIDI only?

**Date:** 2026-08-23

## Executive summary

Yes. For a feature whose promise is “turn my hum into a usable melody or bassline,” the deterministic transformed MIDI should be the product, not an intermediate sent to a generative audio model. Stable Audio can creatively reinterpret a guide, so it cannot provide a fidelity guarantee for the performed notes, onsets, durations, or rests. A MIDI-only path removes model startup, inference time, credits/network dependence, audio alignment, and prompt-induced divergence from the core user journey.

The current repository already has most of the required path: analysis stores `hum_notes`; `hum_transform.transform()` creates `pretty_midi.PrettyMIDI`; and `/api/generate-from-hum` can return MIDI notes. However, its default still writes a synthetic guide WAV and calls Stable Audio, then the frontend adds generated audio alongside the MIDI. The default must be inverted and the guide/SA3 stages removed from the hum-transform route.

## Current implementation

```text
hum upload
→ pitch tracking / hum_notes
→ hum_transform.transform()
→ MIDI file
→ render_guide.render()
→ Stable Audio audio-to-audio(prompt + guide WAV)
→ alignment/polish
→ audio stem plus MIDI
```

`backend/pipeline.py::prepare_hum_transform()` currently writes both MIDI and a guide WAV. `pipeline.generate_from_hum()` then builds a text prompt and calls `sa3_backend.generate_with_fallback()`. In `backend/api.py`, `HumGenerateRequest.render_audio` defaults to `True`; the frontend does not override it. This makes Stable Audio the normal path even though MIDI is created first.

## Why MIDI-only is the correct default

1. **Faithfulness:** MIDI is a deterministic representation of the chosen pitch tracker and transform rules. SA3 is generative audio-to-audio; its output can change note count, rhythm, pitch, articulation, and instrumentation.
2. **Editability:** musicians can correct transcription errors in the existing piano roll before choosing an instrument, rather than attempting to recover notes from a generated WAV.
3. **Latency and reliability:** MIDI-only needs no SA3 model, API credit, network request, fallback backend, alignment, or audio decode.
4. **Clear responsibility:** pitch tracking is measurable with note/onset metrics. Stable Audio quality is a separate creative-rendering concern and should not obscure transcription failures.
5. **DAW interoperability:** Standard MIDI files are the expected interchange format for editable melody/bassline material. [1]

## Recommended product boundary

- **Primary action:** “Transform Hum to MIDI.” It creates a melody or bassline MIDI clip and places it in the piano roll.
- **Optional later action:** “Render with AI audio” may use the edited MIDI as a guide, but is explicitly a creative render, never the transformation result.
- **Exports:** MIDI is always included. A guide WAV is not created in MIDI-only mode; it is generated only when a user explicitly requests AI rendering.
- **Prompt:** remove it from the transformation request. Keep an optional name/target and transform options (faithful, key snap, quantization). Instrument text belongs to a later instrument/sample/render action.

## Evidence and source notes

- The Stable Audio 3 repository documents audio-to-audio editing/conditioning rather than deterministic MIDI playback. This supports treating its output as generative variation, not exact transcription. [2]
- Basic Pitch is explicitly an audio-to-MIDI project, illustrating the correct category of tool for the transcription stage; it does not make Stable Audio necessary for MIDI delivery. [3]
- The MIDI 1.0 specification is the standards basis for interoperable note events and timing, though a DAW’s exact playback sound remains instrument-dependent. [1]

## Caveats

- MIDI-only does not solve tracker mistakes. The pitch tracker, diagnostics, faithful transform options, and piano-roll editing remain essential.
- A bassline is intentionally an arrangement transformation, not a literal transcription; users should be told that its register, harmony, and rhythm may change.
- A MIDI clip does not itself contain audio. The browser needs an instrument/sampler for preview; DAW export remains the reliable sound-selection workflow.

## References

1. MIDI Manufacturers Association. **MIDI 1.0 Detailed Specification.** Official standards organization; specification/documentation, not peer reviewed. https://midi.org/specifications
2. Stability AI. **Stable Audio 3 source repository and audio-generation documentation.** Official implementation/documentation, not peer reviewed. https://github.com/Stability-AI/stable-audio-3
3. Spotify. **Basic Pitch.** Official open-source audio-to-MIDI project documentation; not peer reviewed. https://basicpitch.spotify.com/ and https://github.com/spotify/basic-pitch
