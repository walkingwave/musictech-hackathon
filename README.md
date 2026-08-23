# Backing Track Generator

Project Track: Stable Audio

Record or upload a **monophonic hum**, turn it into a clear, editable melody or
bassline MIDI clip, then render that exact musical guide as usable audio for Stable
Audio 3. Export the original hum, transformed MIDI, guide WAV, and final audio to a
DAW.

See [PLAN.md](PLAN.md) for the full design and rationale.

## How it works

Stable Audio 3 has no melody or chord conditioning and no stem output, so we cannot
just hand it the vocal and ask for a bassline. Instead we build a **guide track**:

```
hum.wav
  1. ANALYZE       voiced note events, BPM, key, and beat/bar grid
  2. TRANSFORM     hum contour -> melody MIDI or bassline MIDI
  3. REVIEW        user may correct tempo/key and edit the MIDI notes
  4. RENDER GUIDE  transformed MIDI -> clear synthetic guide WAV
  5. GENERATE      Stable Audio 3 audio-to-audio, init_audio = guide
  6. ALIGN         time-stretch and phase-lock output to the guide/grid
```

The transformed MIDI is the primary deliverable: it must be musically legible and
usable without generation. The guide WAV renders those same notes, so Stable Audio
receives explicit pitch and rhythm rather than raw vocal audio. For a **melody**
request, the system preserves the hummed contour, phrase timing, and rests. For a
**bassline** request, it moves that contour into a playable bass register and
simplifies it onto the detected/edited harmonic grid. The existing backing-stem
flow remains available while this becomes the default input experience.

## Setup

**macOS / Linux:**

```bash
./scripts/setup.sh
```

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Either installs `uv`, the `rubberband` binary, and the Python dependencies. On
Windows `rubberband` is optional — without it the pipeline falls back to
librosa's phase vocoder, so stem generation still works out of the box. Then:

```bash
cp .env.example .env      # optional, for the API backend  (Windows: copy .env.example .env)
uv run uvicorn backend.api:app --reload
```

Open http://127.0.0.1:8000

Works immediately on the **mock** backend — no model weights, no API key, no network.
Use it to build and test everything except the audio quality itself.

### DeepSeek chat agent

The Studio ask bar calls `/api/interpret`, which uses DeepSeek when
`DEEPSEEK_API_KEY` is set and falls back to the offline rules parser when it is
not. DeepSeek only returns a structured generation plan; the app still executes
the existing `/api/generate` pipeline and SA3 calls itself.

1. Create a DeepSeek account and API key from the DeepSeek platform.
2. Add the key to `.env`:

```bash
DEEPSEEK_API_KEY=your_key_here
BTG_AGENT_PROVIDER=deepseek
BTG_AGENT_MODEL=deepseek-v4-flash
```

Restart the backend after editing `.env`.

## Backends

Pick one in the UI header, per generation. Availability is detected live, so a
backend you cannot use is shown disabled rather than failing on click. If a
generation fails mid-flight, the server falls back to a working backend and reports
which one actually ran.

| Backend | Setup | Notes |
|---|---|---|
| `mock` | none | Returns the guide with noise. For UI work and offline demos. Runs anywhere. |
| `local` | `uv sync --extra local` + HF access | `small-music` 0.6B. Free, offline. Runs on any OS — see runtimes below. |
| `api` | `STABILITY_API_KEY` in `.env` | `large` 2.7B. Best quality, uses credits, needs network. Runs anywhere. |

The `local` backend has two runtimes and picks whichever is installed:

- **PyTorch** (`uv sync --extra local`) — Windows, Linux, macOS. `small-music`
  runs on **CPU** (works with no GPU, and on AMD, where PyTorch has no CUDA
  path); `medium` needs an NVIDIA GPU with CUDA + Flash Attention 2. CPU
  generation is slow but real. Set the DiT with `BTG_TORCH_DIT`.
- **MLX** — Apple Silicon only, faster there. Preferred automatically on a Mac.

On any machine without a local runtime installed, `local` shows disabled in the
selector. Use `mock` for offline work and `api` for best quality.

### Local backend: Stable Audio 3 MLX

The local backend shells out to Stability AI's optimized MLX implementation. It
is Apple-Silicon-native and stores its own virtualenv and weights outside this
repo. By default this app looks for it at:

```text
../sa3-mlx-src/optimized/mlx
```

Install it with:

```bash
cd ..
git clone --depth=1 https://github.com/Stability-AI/stable-audio-3 sa3-mlx-src
cd sa3-mlx-src/optimized/mlx
./install.sh -y
```

If you install it somewhere else, set:

```bash
BTG_MLX_ROOT=/absolute/path/to/stable-audio-3/optimized/mlx
```

The app auto-detects whichever MLX weights are present and prefers `medium`
when available, then `sm-music`. To force the faster model:

```bash
BTG_MLX_DIT=sm-music
```

Verify through this app's local backend:

```bash
uv run btg --input samples/fixtures/amin_100.wav --part bass --backend local
```

Then start this app and select the `local` backend.

## CLI

Faster than the UI when tuning prompts and noise values.

```bash
uv run python scripts/make_test_vocals.py           # 18 fixtures with known BPM/key

uv run btg --input samples/fixtures/amin_100.wav --hum-target melody
uv run btg --input samples/fixtures/amin_100.wav --hum-target bass --style "warm fingered electric bass"
uv run btg --input samples/fixtures/amin_100.wav --part bass
uv run btg --input samples/fixtures/amin_100.wav --all --backend local
uv run btg --input samples/fixtures/amin_100.wav --part bass --style "bossa nova"
uv run btg --input samples/fixtures/amin_100.wav --part bass --sweep 0.5,0.65,0.8,0.9
```

`--sweep` is the important one: `noise` (the model's divergence from the guide) is
the single most important knob, and the right value has to be found by ear.
Output lands in `sessions/<id>/`.

### Validate input analysis

Use the analysis-only command to inspect the signal before guide generation or
Stable Audio 3. It writes a cleaned 44.1 kHz mono WAV and metadata containing
BPM, key, downbeat, chords, melody notes, and MIDI pitches:

```bash
uv run analysis-test --input samples/fixtures/amin_100.wav
uv run analysis-test --input samples/fixtures/amin_100.wav --output backend/test/test_run/amin-check
uv run analysis-test --input samples/beatbox.wav --mode beatbox
uv run analysis-test --clean  # remove prior generated test runs
```

The default output directory is a sortable timestamp such as
`backend/test/test_run/analysis_test_2026-08-22_16-43-09/`. The preprocessing is deliberately
conservative: it removes DC, trims only outer silence, applies
a content-aware high-pass filter, and normalizes with headroom. Use
`--no-trim` or `--no-high-pass` when comparing their effect on analysis.

## Layout

```
backend/
  config.py        paths and tunable defaults — start here
  models.py        Analysis, Bar, StemResult. The contract between stages.
  theory.py        note names, triads, scales, diatonic transposition
  analysis.py      stage 1
  arrange.py       stage 2 — one function per part
  render_guide.py  stage 3
  sa3_backend.py   stage 4 — mock | local | api, behind one interface
  align.py         stage 5
  pipeline.py      the only module that knows the stage order
  api.py           HTTP routes. Thin — musical logic lives in the stages.
  cli.py           headless runner
  test/             developer validation CLIs and ignored test-run artifacts
frontend/          plain HTML/CSS/JS, no build step
scripts/           setup and test-fixture generation
sessions/<id>/     vocal, guides, stems, MIDI, and a meta.json provenance record
```

### DeepSeek validation

Validate the complete analysis-to-DeepSeek planning path with an API key in `.env`:

```bash
uv run deepseek-test --input samples/fixtures/amin_100.wav \
  --prompt "add upright bass, Rhodes, and soft bossa nova drums" \
  --require-deepseek --expect-tracks
```

This writes a cleaned WAV, analysis metadata, redacted request/response plans, and
`validation.json` under `backend/test/test_run/deepseek_test_<timestamp>/`. It sends
only derived musical metadata and the request text to DeepSeek—never audio bytes,
local paths, or credentials. Omit `--require-deepseek` to permit the offline rules
fallback for a no-network smoke test.

### Frontend interpretation-to-generation integration test

With the backend running, exercise the same HTTP flow used by the Studio ask
bar. The default `mock` backend reaches the generation adapter without loading
SA3; use `--backend local` only for an intentional MLX SA3 smoke test.

```bash
uv run uvicorn backend.api:app --reload
node frontend/test/interpret_generate_test.mjs --backend mock
node frontend/test/interpret_generate_test.mjs --backend local --require-deepseek
```

Each invocation stores redacted request/response JSON and two validation
results under `frontend/test/test_run/frontend_interpret_generate_test_<timestamp>/`.
Run the dependency-free runner tests with `cd frontend/test && npm test`.

### Sessions

Projects persist under `sessions/`. Use the Studio header **Open** button to
load a saved project, **Close** to clear it from this browser while retaining
server files, and **Delete** to permanently remove it after confirmation.
Deletion is rejected while that session is generating. Session list responses
contain summaries only; audio remains available through the existing session
audio routes.

### Adding a backing part

1. Add the name to `PARTS` in `models.py`
2. Write `_arrange_<name>` in `arrange.py` and register it in `ARRANGERS`
3. Add an instrument phrase and an isolation clause in `prompts.py`
4. Add it to `PARTS` in `frontend/app.js`

## Measuring detection accuracy

Tempo and key detection have a fixture suite with ground truth. **Run this after
any change to `analysis.py` or `melody.py`** — it is the only way to tell a real
improvement from a lucky guess on one file.

```bash
uv run python scripts/make_test_vocals.py   # 18 synthetic vocals, known BPM/key
uv run python scripts/eval_analysis.py      # score against ground truth
```

Two tiers. The **easy** tier is clean: steady tempo, melodies that resolve to the
tonic. The **hard** tier adds what real recordings have — ±3% rubato, room noise,
detuning, an offset start, and melodies that dwell on the mediant and only touch
the tonic at phrase endings.

Current: **18/18 tempo, 18/18 key.**

Key detection scores two ways. *Exact* means tonic and mode both right. *Note-set*
means the right pitches but possibly the wrong tonic — that is the relative-key
failure (C major for A minor), and it is the one worth watching, because pitch
histograms alone cannot fix it.

### How key detection works, and why

A key and its relative contain **exactly the same pitches**, so any method that
scores a pitch histogram is guessing between them. What separates them is where
the melody *lands*: phrases resolve to the tonic. So `analysis.py` scores each of
the 24 candidate keys by profile correlation **plus** bonuses for the tonic
appearing at phrase endings, at the final note, and at the first note.

Ablation on the hard tier:

| Method | hard tier |
|---|---|
| Temperley profile + melodic cues (current) | 8/8 |
| Temperley profile alone | 4/8 |
| Krumhansl profile alone | 0/8 |

## Known limitations

- **Chord detection on a solo vocal is weak.** One melody genuinely fits many
  progressions. The UI exposes an editable chord grid for exactly this reason —
  treat the detected chords as a first guess.
- **4/4 is assumed** throughout.
- **Harmony depends on clean monophonic pitch tracking.** Noisy or breathy input
  degrades it.
- Everything above is measured on *synthetic* fixtures. Real voices have more
  vibrato, breath and consonant noise. Re-check against real recordings.

### Two library traps worth knowing

- `librosa.beat.beat_track` must be given an onset envelope explicitly. Letting it
  derive one from `y` uses median aggregation, which goes flat on sustained
  material and reports **0 BPM**. Both `analysis.py` and `align.py` work around it.
- Pitch contours must be **median-filtered before rounding to semitones**.
  Vibrato that crosses a semitone boundary otherwise chops one held note into a
  stutter of fragments, destroying the note-duration evidence key detection
  depends on. See `SMOOTHING_FRAMES` in `melody.py`.
