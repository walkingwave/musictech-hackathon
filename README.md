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

```bash
./scripts/setup.sh
```

That installs `uv`, the `rubberband` binary, and the Python dependencies. Then:

```bash
cp .env.example .env      # optional, for the API backend
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
| `mock` | none | Returns the guide with noise. For UI work and offline demos. |
| `local` | `uv sync --extra local` + HF access | `small-music` 0.6B. Free, offline, runs on Apple Silicon. |
| `api` | `STABILITY_API_KEY` in `.env` | `large` 2.7B. Best quality, uses credits, needs network. |

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
uv run python scripts/make_test_vocal.py            # 8 bars, 100 BPM, A minor

uv run btg --input samples/test_vocal.wav --part bass
uv run btg --input samples/test_vocal.wav --all --backend local
uv run btg --input samples/test_vocal.wav --part bass --style "bossa nova"
uv run btg --input samples/test_vocal.wav --part bass --sweep 0.5,0.65,0.8,0.9
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

## Known limitations

- **Chord detection on a solo vocal is weak.** One melody genuinely fits many
  progressions, and relative major/minor pairs (C major vs A minor) share every
  note. The UI exposes an editable chord grid for exactly this reason — treat the
  detected chords as a first guess.
- **4/4 is assumed** throughout.
- **Harmony depends on clean monophonic pitch tracking.** Noisy or breathy input
  degrades it.
- `librosa.beat.beat_track` must be given an onset envelope explicitly; letting it
  derive one from `y` uses median aggregation and reports 0 BPM on sustained
  material. Both `analysis.py` and `align.py` work around this.
