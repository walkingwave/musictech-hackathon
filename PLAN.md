# Backing Track Generator — Stable Audio 3

Music Tech Hackathon Montreal, Aug 22–23 2026 · Stability AI track

## Context

Hackathon is **live now** (PHI, Montreal). Stability AI track brief: "build a community-facing project for musicians using Stable Audio 3." Prize $500/participant, up to $2,000/team. Winner presents at MUTEK.

**Goal:** user records or uploads a vocal melody; the app generates individual backing stems the user selects — bassline, piano chords, drums, vocal harmony — each locked to the original's tempo, key and harmony. Stems export to a DAW.

**Core technical problem.** SA3 has no melody or chord conditioning and no multi-stem output. Its three modes are text-to-audio, audio-to-audio (`init_audio` + `init_noise_level` locally, `strength` via API), and inpainting with time masks. Feeding the vocal directly as `init_audio` fails both ways — low noise leaves the vocal audible in the output, high noise destroys the timing — and the result is a mix, not an isolated stem.

**Solution: guide-track conditioning.** Condition on a *synthetic guide*, not on the vocal. Analyze the vocal for BPM, key and per-bar chords; synthesize a crude but structurally correct guide for the requested part; use that as `init_audio`. The rhythm and harmony live in the noised latent, so SA3 preserves the skeleton and rewrites the timbre. The output is already in time and in key, and contains only the target instrument.

Repo is greenfield — one README, no code. First task is committing this plan as `PLAN.md`.

## Compute

Hardware: **Apple M5, 16GB RAM, 10 cores.** System Python 3.9.6, no `uv` installed.

| Backend | Model | Role |
|---|---|---|
| Local `stable_audio_3` | `small-music` 0.6B | Dev loop and offline demo safety. Free, seconds per generation on M5, full param control. |
| Stability API | `large` 2.7B, API-only | Quality renders. Sponsor granted $20; **new keys available on request**, so credits are not binding. |

`medium` (1.4B) is excluded: it needs CUDA + Flash Attention 2 (Ampere SM80+), which the Mac cannot provide and free Colab T4 (Turing SM75) cannot either. Modal's free tier is a fallback only if the API path collapses entirely.

Both backends expose the same knob in the same direction (higher = more divergence from the input), so `init_noise_level` and `strength` map identity. Stability's KB recommends starting at **0.8**, dropping to 0.6–0.75 if the output wanders off the guide.

Cost note: Stable Audio 2.5 bills 20 credits = $0.20 per generation, **flat regardless of duration**. Expect `large` in the same band. Consequence: generate long sections in one call rather than looping per-bar calls.

## Pipeline

```
vocal.wav
  1. ANALYZE       BPM, downbeat offset, key, per-bar chords
  2. ARRANGE       chord/beat grid -> MIDI for the requested part
  3. RENDER GUIDE  numpy synth -> guide.wav (in time, in key, deliberately ugly)
  4. SA3           audio-to-audio, init_audio=guide, noise ~0.8,
                   prompt = instrument + user style + BPM + key
  5. ALIGN         beat-track output, time-stretch to target BPM,
                   cross-correlate onsets, trim to grid
  6. LAYER         next part's guide optionally mixed with approved stems
```

## File layout

```
backend/
  analysis.py       vocal -> musical structure
  arrange.py        structure -> MIDI per part
  render_guide.py   MIDI -> guide wav (pure numpy synth)
  sa3_backend.py    backend registry + adapter (local | api)
  prompts.py        per-part prompt templates
  align.py          post-generation grid alignment
  api.py            FastAPI app
  cli.py            headless single-part runner for the dev loop
frontend/
  index.html        single page
  app.js            recorder, backend selector, multitrack player
  styles.css
sessions/{uuid}/    per-session working dir (vocal, guides, stems, MIDI, meta.json)
PLAN.md             this document, committed to the repo
```

## Modules

### `analysis.py`

- Tempo and beats: `librosa.beat.beat_track`.
- Downbeat: onset-strength phase over a 4/4 assumption; pick the beat offset maximizing summed onset strength.
- Key: `librosa.feature.chroma_cqt` averaged, scored against Krumhansl-Schmuckler major/minor profiles.
- Chords: per-bar chroma matched against 24 major/minor triad templates.

Returns:
```json
{ "bpm": 92.4, "downbeat_offset_s": 0.21, "key": "A", "mode": "minor",
  "duration": 32.0,
  "bars": [{"index":0,"start":0.21,"end":2.81,"chord":"Am"}, ...] }
```

### `arrange.py`

Chord grid → `pretty_midi.PrettyMIDI`, one function per part:
- **bass** — chord roots, octave 2, root–fifth pattern on the beat
- **piano** — triad voicings, octave 4, hits on beats 1 and 3
- **harmony** — `librosa.pyin` f0 on the vocal → quantized note events → transposed a diatonic third up within the detected key
- **drums** — kick/snare/hat on the beat grid, GM drum channel

### `render_guide.py`

**Pure numpy synth** — sine/saw oscillator plus ADSR envelope; drums are noise bursts and sine thumps. Deliberately avoids fluidsynth and soundfont system dependencies: the guide only has to be structurally correct, not pretty, because SA3 replaces the timbre. This removes an install step that would otherwise threaten M0.

### `sa3_backend.py`

```python
class Backend(Protocol):
    id: str
    label: str
    def available(self) -> bool: ...
    def generate(self, prompt: str, init_audio: np.ndarray | None,
                 noise: float, duration: float, seed: int) -> np.ndarray: ...

BACKENDS = {"local": LocalBackend, "api": StabilityAPIBackend}
```

- `LocalBackend` wraps `StableAudioModel.from_pretrained("small-music")`, passing `init_audio=` and `init_noise_level=`. **Lazily instantiated** — the model is not loaded until the first local generation, so server boot stays fast.
- `StabilityAPIBackend` POSTs to the `audio-to-audio` endpoint, mapping `noise -> strength`. `available()` returns whether `STABILITY_API_KEY` is set. Disk cache keyed by `hash(prompt, guide_bytes, noise, seed, duration)` so identical requests skip the round trip.
- On API failure (401, quota, network), the caller **auto-falls back to local** and reports which backend actually ran. Essential for a live stage demo.

### `prompts.py`

Per-part templates composed as `{instrument_phrase}, {user_style}, {bpm} BPM, {key} {mode}, {negatives}` — e.g. bass → `"warm fingered electric bass, dry DI, {style}, 92 BPM, A minor, solo instrument, no drums, no vocals"`. Isolation phrasing matters: the prompt has to actively push against SA3's tendency to render a full mix.

### `align.py`

Beat-track the SA3 output, compute the ratio to target BPM, `pyrubberband.time_stretch`, cross-correlate the onset envelope against the guide for the sample offset, then trim or pad to exact bar length.

## Backend selector in the UI

The user picks local vs API per generation, from the frontend.

**Server:**
- `GET /backends` →
  ```json
  [{"id":"local","label":"Local — small-music 0.6B","available":true,
    "note":"free, offline, ~3s"},
   {"id":"api","label":"Stability API — large 2.7B","available":true,
    "note":"best quality, ~$0.20/generation"}]
  ```
  `available` is computed live (API key present, local weights downloaded), so the UI never offers a backend that would fail.
- `POST /generate` and `POST /regenerate` accept an optional `"backend"` field, falling back to the server default (`local`).
- Every response includes `"backend_used"`, which may differ from the request when auto-fallback fired.

**Client:**
- Segmented control in the header, persisted to `localStorage`. Unavailable options render disabled with a tooltip explaining why (no API key / weights not downloaded).
- Each stem row shows a small badge for the backend that produced it, read from `backend_used`.
- Because the selection applies per generation, the user can regenerate a single stem on `large` while the rest stay local — a strong live demo beat: same guide, same seed, hear the quality jump.
- If auto-fallback fires, show a toast: "Stability API unavailable — generated locally."

## Frontend

Single page, vanilla JS, WaveSurfer.js for waveforms, Web Audio API for synchronized playback.

- Record via `MediaRecorder`, or drag-and-drop upload.
- Show detected BPM, key, and the chord grid. **Chords are editable** — a solo melody underdetermines harmony, so let the user correct it. This turns the weakest part of the analysis into a feature.
- Four part checkboxes, each with a free-text style field.
- Multitrack player: per-stem mute / solo / volume, all stems locked to one transport.
- Per-stem regenerate button (new seed).
- **Download stems (WAV) + guide MIDI** as a zip. This is the "real utility for musicians" the track asks for, and the MIDI export is nearly free given the arranger already produces it.

## API contract

| Route | Body | Returns |
|---|---|---|
| `GET /backends` | — | backend list with availability |
| `POST /analyze` | audio upload | `{session_id, analysis}` |
| `PATCH /session/{id}/chords` | edited chord grid | updated analysis |
| `POST /generate` | `{session, part, style_prompt, noise, backend?}` | `{stem_url, backend_used, seed}` |
| `POST /regenerate` | same + `seed?` | same |
| `GET /export/{session}` | — | zip of stems + MIDI |

## Dependencies

`uv` (install first — system Python is 3.9.6 and unusable for this), Python 3.11, `stable-audio-3`, `fastapi`, `uvicorn`, `librosa`, `soundfile`, `numpy`, `scipy`, `pretty_midi`, `pyrubberband` (needs the `rubberband` binary via Homebrew), `httpx`, `python-multipart`.

## Build order (24h)

- **M0 (1h) — hard gate.** Install `uv`; `uv sync`; download `stable-audio-3-small-music`; generate one clip on the M5. Commit `PLAN.md`. If local inference fails here, pivot to API-only immediately rather than burning hours on it.
- **M1 (3h) — prove the thesis.** analysis + bass arranger + numpy synth + one audio-to-audio call, written to wav via `cli.py`. Listen. Sweep `noise` across 0.5 / 0.65 / 0.8 / 0.9 locally and pick. **Also probe the API here** — confirming the `large` request shape early de-risks the entire final render. *Fallback if the guide mechanism doesn't hold tempo:* text-to-audio with BPM and key in the prompt plus hard alignment in M2 — worse lock, still shippable.
- **M2 (2h)** — `align.py`. Verify the bass sits on the grid against the vocal.
- **M3 (3h)** — piano, then drums, then harmony arrangers. Ship in that order: bass and piano are the safe core, harmony is the wow factor, drums carry the most audible alignment risk. Cut from the end if time runs short.
- **M4 (4h)** — FastAPI + web UI multitrack player + backend selector.
- **M5 (2h)** — chord editing, export zip + MIDI, visual polish.
- **M6 (2h)** — final renders on `large`, **pre-cache a complete demo session to disk** so the stage demo survives dead venue wifi.

## Risks

- **API shape unknown.** If the SA3 API exposes only inpainting and no audio-to-audio `strength`, local becomes the primary path rather than the fallback. Test the key the moment you have it; the adapter makes this a config change, not a rewrite.
- **Chord detection on a bare monophonic vocal is weak.** Mitigated by the editable chord grid.
- **`small-music` at 0.6B may sound thin** on isolated instruments. Since credits are not binding, lean on `large` for anything the judges hear.
- **Venue network.** The whole flow must run end-to-end on local `small-music` with wifi off. Verify before depending on the API on stage.
- **Layering bleed** — mixing approved stems into a guide for context risks those instruments reappearing in the new stem. Default off, behind a toggle.
- **Four parts is ambitious.** The build order is sequenced so an early stop still yields a coherent demo.

## Verification

1. `uv run python -m backend.cli --input samples/vocal.wav --part bass --backend local` writes `out/bass.wav`; open it against the vocal in a DAW and confirm the downbeats line up.
2. Assert in `align.py`: beat-tracked output BPM within 1% of target, first onset within 20ms of the grid.
3. Round-trip a known-tempo click-plus-vocal test file through `/analyze` and assert reported BPM matches ground truth.
4. `GET /backends` with `STABILITY_API_KEY` unset reports `api` unavailable; the UI disables that option rather than erroring on click.
5. Force an API failure (bad key) and confirm auto-fallback returns audio with `backend_used: "local"` plus the toast.
6. Generate the same stem on both backends with the same seed and guide; confirm both align to the grid and the API version sounds better.
7. End-to-end in the browser: record 8 bars → generate all four parts → solo each stem → download the zip → open the stems on separate DAW tracks and confirm they play in sync.
8. Run the full flow once with wifi off against the local backend, to prove the demo path.
