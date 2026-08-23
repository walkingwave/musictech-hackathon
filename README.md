# Backing Track Generator

Project Track: Stable Audio

Record a vocal melody, get individual backing stems — bassline, piano chords, drums,
vocal harmony — each locked to the original's tempo, key and harmony. Export the
stems and MIDI to a DAW.

See [PLAN.md](PLAN.md) for the full design and rationale.

## How it works

Stable Audio 3 has no melody or chord conditioning and no stem output, so we cannot
just hand it the vocal and ask for a bassline. Instead we build a **guide track**:

```
vocal.wav
  1. ANALYZE       BPM, downbeat, key, per-bar chords            analysis.py
  2. ARRANGE       chord grid -> MIDI for the requested part     arrange.py
  3. RENDER GUIDE  MIDI -> rough numpy synth audio               render_guide.py
  4. GENERATE      SA3 audio-to-audio, init_audio = guide        sa3_backend.py
  5. ALIGN         time-stretch and phase-lock to the grid       align.py
```

The guide is deliberately ugly — its only job is to be *structurally* right. Its
rhythm and harmony survive in the model's noised latent, so what comes back has the
correct skeleton and a real instrument's timbre.

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

### Local backend: model access

The weights are **gated** on Hugging Face — an anonymous download returns `401`.

1. Accept the licence at https://huggingface.co/stabilityai/stable-audio-3-small-music
2. Create a token at https://huggingface.co/settings/tokens
3. `uv run hf auth login`
4. `uv sync --extra local`

Do this early. It is the one setup step that can block on someone else approving you.

`medium` (1.4B) is **not** an option on a Mac: it requires CUDA and Flash Attention 2.

## CLI

Faster than the UI when tuning prompts and noise values.

```bash
uv run python scripts/make_test_vocals.py           # 18 fixtures with known BPM/key

uv run btg --input samples/fixtures/amin_100.wav --part bass
uv run btg --input samples/fixtures/amin_100.wav --all --backend local
uv run btg --input samples/fixtures/amin_100.wav --part bass --style "bossa nova"
uv run btg --input samples/fixtures/amin_100.wav --part bass --sweep 0.5,0.65,0.8,0.9
```

`--sweep` is the important one: `noise` (the model's divergence from the guide) is
the single most important knob, and the right value has to be found by ear.
Output lands in `sessions/<id>/`.

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
frontend/          plain HTML/CSS/JS, no build step
scripts/           setup and test-fixture generation
sessions/<id>/     vocal, guides, stems, MIDI, and a meta.json provenance record
```

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
