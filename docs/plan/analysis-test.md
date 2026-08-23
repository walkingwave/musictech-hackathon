# Analysis Validation Pipeline — Implementation Plan

## Goal

Add a standalone validation pipeline that lets the team listen to and inspect the signal produced **after input cleaning and musical analysis, but before Stable Audio 3 generation**.

For each submitted vocal or beatbox recording, the pipeline must produce exactly two primary artifacts:

1. `cleaned.wav` — the cleaned, normalized analysis signal.
2. `metadata.json` — analysis output containing tempo, key, chords, melody, downbeat, and pitches.

This is a diagnostic/test workflow. It validates whether preprocessing and analysis are usable before the application creates MIDI guides or invokes Stable Audio 3.

## Scope

### In scope

- Safe audio decode, resampling, controlled mono conversion, and conservative cleaning.
- Existing musical analysis: BPM, key/mode, downbeat, bar/chord grid, and melody notes.
- Persisted `cleaned.wav` and human-readable `metadata.json`.
- CLI-first workflow for repeatable development/testing.
- Optional API route after the CLI works.
- Automated validation against synthetic fixtures and a small manually reviewed real-recording set.

### Out of scope

- Stable Audio 3 generation, guide rendering, MIDI arrangement, alignment, or mixing.
- Automatic repair of wrong musical analysis.
- Mandatory neural denoising, dereverberation, source separation, or pitch correction.
- A production-quality recording editor.

## Proposed artifact layout

Keep test output separate from generation sessions:

```text
backend/test/test_run/analysis_test_<timestamp>/
  cleaned.wav
  metadata.json
```

`<run-id>` should be a UUID or deterministic timestamp-plus-input-stem identifier. Do not overwrite an earlier test run by default.

## Metadata contract

`metadata.json` should contain the requested musical results and enough provenance to compare runs.

```json
{
  "schema_version": 1,
  "source": {
    "original_filename": "recording.webm",
    "source_sample_rate": 48000,
    "source_channels": 1,
    "duration_seconds": 12.48
  },
  "cleaned_audio": {
    "path": "cleaned.wav",
    "sample_rate": 44100,
    "channels": 1,
    "duration_seconds": 12.31,
    "peak_dbfs": -3.0,
    "rms_dbfs": -20.4,
    "leading_trim_seconds": 0.12,
    "trailing_trim_seconds": 0.05,
    "warnings": []
  },
  "analysis": {
    "tempo_bpm": 100.0,
    "key": "A",
    "mode": "minor",
    "downbeat_offset_seconds": 0.21,
    "chords": [
      { "bar": 0, "start_seconds": 0.21, "end_seconds": 2.61, "chord": "Am" },
      { "bar": 1, "start_seconds": 2.61, "end_seconds": 5.01, "chord": "F" }
    ],
    "melody": [
      {
        "midi_pitch": 69,
        "pitch_name": "A4",
        "pitch_class": 9,
        "start_seconds": 0.24,
        "end_seconds": 0.68,
        "duration_seconds": 0.44
      }
    ],
    "pitches": [69, 72, 76]
  }
}
```

### Required field definitions

| Requested component | Metadata field | Source |
|---|---|---|
| Tempo | `analysis.tempo_bpm` | `Analysis.bpm` |
| Key | `analysis.key` and `analysis.mode` | `Analysis.key`, `Analysis.mode` |
| Chords | `analysis.chords` | `Analysis.bars` |
| Melody | `analysis.melody` | `melody.track()` note events |
| Downbeat | `analysis.downbeat_offset_seconds` | `Analysis.downbeat_offset_s` |
| Pitches | `analysis.pitches` and per-note `midi_pitch` | melody note MIDI values |

Use MIDI pitch integers as the canonical machine-readable pitch representation. Include pitch names only for human inspection.

## Pipeline design

```text
input recording
  → decode and validate
  → conservative preprocessing
  → cleaned.wav
  → existing analysis.analyze(cleaned_audio)
  → melody.track(cleaned_audio) for detailed note events
  → metadata.json
```

### 1. Decode and validate

Create a single ingestion function that:

- accepts a file path;
- decodes to float32 audio;
- records source sample rate, channels, duration, peak, and RMS;
- rejects empty, unreadable, too-short, excessively long, or near-silent input with actionable errors;
- supports browser-produced WebM/Opus through an FFmpeg conversion fallback if SoundFile cannot decode it.

Preserve the source file untouched. The validation output is derived data.

### 2. Conservative preprocessing

Implement `backend/preprocess.py` with an API similar to:

```python
@dataclass
class InputQuality:
    source_sample_rate: int
    source_channels: int
    duration_seconds: float
    peak_dbfs: float
    rms_dbfs: float
    clipped_fraction: float
    leading_trim_seconds: float
    trailing_trim_seconds: float
    warnings: list[str]


def preprocess_for_analysis(input_path: Path) -> tuple[np.ndarray, int, InputQuality]:
    ...
```

Operation order:

1. Decode as float32.
2. Deliberately downmix to mono for current analysis code.
3. Resample to `config.SAMPLE_RATE` (44.1 kHz).
4. Remove DC offset.
5. Conservatively trim only leading/trailing silence, retaining 100–250 ms padding.
6. Apply a modest high-pass filter only when appropriate:
   - singing/humming: start with 60–80 Hz;
   - beatbox: bypass or lower to roughly 25–35 Hz.
7. Normalize with headroom, e.g. peak near −3 dBFS.
8. Produce warnings instead of destructive repair for clipping, low level, noise, reverb, or likely phase problems.

Do not remove internal silences: phrase boundaries help the existing melody/key logic.

### 3. Analysis serialization

Reuse `analysis.analyze(cleaned_audio, sample_rate)` for tempo, key, mode, downbeat, and chords. Call `melody.track(cleaned_audio, sample_rate)` once for the detailed melody list.

Add a small serializer, for example `backend/analysis_export.py`, which:

- converts `Analysis` bars into the `chords` list;
- converts each `melody.Note` into MIDI pitch, pitch name, pitch class, start/end/duration;
- creates de-duplicated `pitches` in note order;
- combines source and preprocessing facts with the musical analysis;
- writes formatted JSON atomically.

Do not alter the existing `Analysis.to_dict()` contract unless the main API also needs detailed melody data. Keep this test-specific metadata shape isolated initially.

### 4. Output writing

- Write `cleaned.wav` with SoundFile at 44.1 kHz, mono, float32.
- Write `metadata.json` using `json.dumps(..., indent=2)` to a temporary file, then rename it atomically.
- Include a `schema_version` from the beginning so later preprocessing changes are comparable.
- Log the output directory, tempo/key result, number of bars, note count, and quality warnings.

## CLI interface

Add a dedicated command rather than overloading generation:

```bash
uv run analysis-test --input samples/vocal.wav
uv run analysis-test --input recording.webm --output backend/test/test_run/my-take
uv run analysis-test --input samples/beatbox.wav --mode beatbox
```

Suggested arguments:

| Argument | Purpose |
|---|---|
| `--input PATH` | Required source recording unless using `--clean` |
| `--output PATH` | Optional output directory; otherwise create a run directory |
| `--clean` | Remove all prior generated run directories/files under `backend/test/test_run/` and exit |
| `--mode auto\|voice\|beatbox` | Select preprocessing profile; begin with `auto` mapped to voice until routing exists |
| `--no-trim` | Debug option to retain exterior silence |
| `--no-high-pass` | Debug option to compare filtering impact |
| `--verbose` | Print measurements and warnings |

Register the entry point in `pyproject.toml`, e.g.:

```toml
[project.scripts]
analysis-test = "backend.test.analysis_test_cli:main"
deepseek-test = "backend.test.deepseek_test_cli:main"
```

## Optional API/UI follow-up

After CLI validation is reliable, add:

```text
POST /api/analyze-test
```

Return the run ID, metadata, and a URL for `cleaned.wav`. The frontend can then show:

- a cleaned-audio player;
- BPM/key/downbeat values;
- chord grid;
- melody-note list or simple pitch timeline;
- warnings and a re-record option.

Do not replace the current `/api/analyze` route until the validation route is proven on real recordings.

## Testing and acceptance criteria

### Unit tests

- Mono and stereo input produce valid mono 44.1 kHz `cleaned.wav`.
- Resampling occurs when source rate differs from 44.1 kHz.
- DC removal, trimming, and normalization preserve finite float32 values.
- Metadata includes all required keys and valid JSON.
- Each melody note has valid MIDI range, start/end ordering, and positive duration.
- Empty/unreadable/near-silent input returns clear errors.

### Fixture tests

Use existing synthetic vocal fixtures to assert:

- tempo/key behavior remains at the project’s current baseline;
- `cleaned.wav` is readable and has the expected sample rate/channels;
- metadata chords and downbeat are present;
- melody and pitches are non-empty for pitched fixtures.

### Real-recording smoke set

Create a consented set containing:

- clean sung melody;
- breathy/vibrato-heavy singing;
- room-noise/reverb case;
- intentionally clipped recording;
- beatbox recording;
- browser-recorded WebM/Opus clip.

For each, inspect `cleaned.wav` by ear and compare raw versus cleaned analysis. Do not accept a cleanup stage merely because it looks cleaner numerically; it must preserve pitch and onset evidence.

### Definition of done

- A developer can run one CLI command and receive only `cleaned.wav` and `metadata.json` as primary artifacts.
- `metadata.json` includes tempo, key/mode, chords, melody, downbeat, and pitches.
- The cleaned file is playable, mono, 44.1 kHz, finite, and has headroom.
- Invalid input produces understandable errors rather than an analysis traceback.
- Existing generation behavior remains unchanged.
- The team has listened to and reviewed outputs from both real singing and beatbox samples.

## Risks and decisions

| Risk | Mitigation |
|---|---|
| Cleanup damages musical evidence | Keep processing conservative; provide debug switches and raw-vs-clean comparisons. |
| WebM browser recordings fail SoundFile decode | Use/test FFmpeg fallback on the deployment machine. |
| One high-pass setting hurts beatbox | Add a beatbox profile or explicit bypass. |
| Chord analysis is weak for solo vocal | Export it as a suggestion; retain user-editable chord workflow. |
| Synthetic fixtures hide real-world failures | Gate acceptance on a small real-recording set. |
| Validation logic destabilizes generation | Keep test pipeline and metadata serializer separate until proven. |

## Implementation order

1. Add output-directory utility and metadata schema/serializer.
2. Extract current decode/downmix/resample behavior into `preprocess.py` without behavior changes.
3. Add conservative quality measurement, DC removal, trim, and normalization.
4. Add CLI command producing `cleaned.wav` and `metadata.json`.
5. Add unit/fixture tests and real-recording smoke tests.
6. Add WebM/FFmpeg fallback if browser recording fails.
7. Add optional API/UI inspection only after CLI output is trusted.
