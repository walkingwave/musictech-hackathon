# Plan: Make Hum Transformation MIDI-Only

## Goal

Change the default hum feature from:

```text
hum → MIDI → guide WAV → Stable Audio prompt/render → audio stem
```

to:

```text
hum → pitch tracking → melody/bass transform → editable MIDI clip + .mid export
```

Stable Audio remains available elsewhere in the Studio as an explicit later creative-render action, not as part of transformation.

## API and pipeline

1. Add `pipeline.transform_hum_to_midi(session, target, name, options)` in `backend/pipeline.py`.
   - Read persisted `session.hum_notes`.
   - Call `hum_transform.transform()`.
   - Write only `sessions/<id>/midi/<name>.mid`.
   - Return MIDI note events in beats, duration, transform options, and tracker provenance.
   - Do not call `render_guide.render`, `sa3_backend`, `align`, or `mix`.

2. Replace the normal behavior of `POST /api/generate-from-hum` in `backend/api.py` with MIDI-only behavior, or introduce `POST /api/transform-hum` and migrate the frontend before deprecating the old route.
   - Prefer `POST /api/transform-hum` for an unambiguous contract.
   - Request: `session_id`, `target`, `name?`, `faithful`, `snap_to_key`, `quantize`, `quantize_division`.
   - Response: `session_id`, `name`, `part`, `midi_url`, `midi_notes`, `duration_beats`, `transform`, and `pitch_tracking` summary.
   - Reject `prompt`, `backend`, `noise`, `seed`, and `render_audio` on this route; they belong to rendering, not MIDI transformation.

3. Keep `/api/generate-from-hum` temporarily as a compatibility adapter returning a deprecation warning and delegating to MIDI-only behavior. Remove guide/audio fields from new frontend use.

4. Retain `pipeline.prepare_hum_transform()` and `pipeline.generate_from_hum()` only if an explicit future “Render MIDI with AI audio” feature still needs them. Otherwise delete them and remove their guide/stem side effects after callers migrate.

## Frontend

5. Rename the Input action in `web/src/components/InputView.jsx` to **Transform Hum to MIDI**.
   - Remove backend selector, audio prompt, divergence/noise, and length controls from this flow.
   - Keep Melody/Bassline target selection.
   - Keep faithful melody as default; key snapping and quantization stay advanced opt-in controls.
   - Show tracker note count and warnings before submission.

6. In `web/src/api.js`, add `transformHum(body)` for the new endpoint and retire `generateFromHum` from the Input flow.

7. In `web/src/App.jsx`, after a successful transform:
   - add exactly one MIDI track using returned `midi_notes`;
   - do not fetch/decode/add an audio stem;
   - open the Studio/piano roll and select the MIDI clip when practical;
   - show a toast that the user can edit notes or download the `.mid` file.

8. Add a MIDI download action in `web/src/components/MidiEditor.jsx` or the MIDI clip inspector using `midi_url`. Ensure project export continues including `midi/` files.

## Session and export behavior

9. Record a lightweight transformation entry in session metadata even without a stem, including target, source tracker/version, transform options, MIDI path, and timestamp. Do not misuse `stems` to represent a MIDI-only result.

10. Update session loading in `web/src/App.jsx` and `web/src/useProject.js` to restore MIDI-only transform tracks from this metadata. Existing sessions with audio stems must still load unchanged.

11. Keep `GET /api/session/{id}/midi/{filename}` and ZIP export. Include original hum, `meta.json`, and MIDI files; omit guides/stems when none exist.

## Tests

12. Unit-test `transform_hum_to_midi()`:
   - faithful melody preserves detected pitch/onset/duration;
   - optional key snapping and quantization change data only when requested;
   - bassline remains low-register/harmonically constrained;
   - a one-note hum returns the existing clear 422 validation error.

13. Add API tests for MIDI-only success:
   - response has `midi_url` and note events;
   - response has no audio/guide URL;
   - no SA3 backend method is invoked;
   - the written `.mid` is readable by `pretty_midi`.

14. Add a frontend test that transforming a hum adds a MIDI track but makes no audio fetch. Preserve existing Studio audio-generation tests.

15. Validate manually with mock, local, and API backend credentials absent: the hum-to-MIDI action must work identically because it must not consult any backend.

## Migration order

1. Implement and test the backend MIDI-only pipeline/route.
2. Switch Input UI/API client to the new route.
3. Add session metadata and restore logic.
4. Remove audio-specific controls and clarify copy.
5. Run unit, API, frontend, and export checks.
6. Deprecate/remove the old SA3 hum-render path only after no frontend caller remains.

## Risks

| Risk | Mitigation |
|---|---|
| Users expect immediate sound | provide a browser sampler/selected MIDI instrument and an explicit later render action |
| Existing sessions expect a stem | preserve old session loading and migrate only new MIDI-only transforms |
| Tracker error is mistaken for MIDI-only regression | show diagnostics and preserve faithful MIDI for editing |
| Hidden SA3 call remains | API tests assert no `sa3_backend` invocation |
