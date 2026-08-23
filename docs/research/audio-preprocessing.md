# Audio Preprocessing for Vocal, Voice, and Beatbox Input to Stable Audio 3

## Scope

Review the project’s current input-preprocessing implementation and recommend practical audio-cleaning and preparation steps before Stable Audio 3 audio-to-audio use.

**Date conducted:** 2026-08-22  
**Project:** Backing Track Generator / Music Tech Hackathon Montréal

## Executive summary

The project does **not** currently send the recorded vocal directly to Stable Audio 3 (SA3). It analyzes the vocal for tempo, downbeat, key, chords, and melody; it then renders a synthetic, target-instrument guide and sends that guide to SA3 audio-to-audio. This is the correct default architecture: it keeps timing and musical structure editable rather than asking a generative model to infer them faithfully from a vocal.

Current vocal preparation is minimal: decode with SoundFile, average multi-channel input to mono, and resample to 44.1 kHz. There is no input validation, clipping/noise report, silence trim, DC removal, high-pass filtering, normalization, denoising, dereverberation, or voice/beatbox-specific routing.

**Recommendation:** add a conservative `preprocess.py` stage before analysis. Preserve the original upload, safely decode and validate it, deliberately downmix, remove DC offset, trim only exterior silence with padding, apply a modest content-aware high-pass filter, normalize with headroom, and show quality warnings. Do not make aggressive denoising, dereverberation, compression, pitch correction, or transient smoothing mandatory: they can harm the onset, consonant, vibrato, pitch, and beatbox-transient evidence used downstream.

For SA3, retain the synthetic-guide path. Ensure guides are explicitly 44.1 kHz stereo, clipped-free, fade-bounded, exact-length, and musically sparse. Official SA3 material documents stereo 44.1 kHz waveform representation, audio-to-audio editing, and a source-preservation/variation tradeoff controlled by noise; it does **not** establish a universal loudness target, mandatory denoiser, or guaranteed beat/key/stem preservation.

## Current implementation

### Signal path

```text
Browser recording/upload
  → POST /api/analyze
  → pipeline.analyze_vocal()
  → SoundFile decode → mono average → 44.1 kHz resample
  → Session.create() stores sessions/<id>/vocal.wav
  → analysis.analyze(): onset, tempo, downbeat, pitch, key, chords
  → arrange + render_guide(): synthetic target-part guide
  → SA3 audio-to-audio receives the guide, not the vocal
  → align(): output tempo/phase correction
```

`backend/pipeline.py::analyze_vocal()` decodes with `soundfile.read(..., dtype="float32")`, averages channels with `audio.mean(axis=1)`, and resamples non-44.1 kHz audio using `librosa.resample`. It then saves the processed result as `vocal.wav` and invokes analysis.

The analysis is sensitive to input quality:

- `analysis.py` computes onset strength for tempo/downbeat estimation.
- `melody.py` uses `librosa.pyin` for monophonic pitch tracking.
- Key estimation combines Temperley profiles with phrase-ending/final-note evidence.
- Per-bar chord guesses use chroma features under a fixed 4/4 assumption.

The API and browser currently accept `audio/*` uploads or a `MediaRecorder` WebM recording without file-size, duration, media-type, channel-count, clipping, or decode validation. Whether libsndfile/SoundFile decodes the presentation machine’s WebM/Opus recordings must be tested; it is not guaranteed by the current code.

### Current SA3 preparation

The guide is generated at 44.1 kHz. `LocalBackend` duplicates the mono guide to stereo before calling the local SA3 model. The API backend serializes the mono guide directly as WAV, so mono-versus-stereo API behavior should be explicitly tested. Generated output is reduced to mono before alignment, so current stem output discards SA3 stereo information.

## Verified Stable Audio 3 considerations

- SA3’s SAME autoencoder operates on stereo 44.1 kHz audio. Use explicit 44.1 kHz stereo guide WAVs for a consistent local/API contract. [1, 2]
- Text-to-audio, audio-to-audio editing, inpainting, and continuation are supported. Local audio-to-audio examples provide `init_audio`, `init_noise_level`, a prompt, and duration. [1, 2]
- Lower noise tends to preserve more of the seed’s performance; higher noise permits stronger prompt-driven variation. Stability’s prompt guide gives a practical 0.3–0.9 working range, including a beatbox-to-lo-fi example at 0.5 and violin-to-electric-guitar at 0.82. This supports controlled sweeps but does not guarantee timing, pitch, or stem isolation. [4]
- The official prompt guide positions SA3 mainly for instrumental generation rather than intelligible vocals. This supports using instrumental guides instead of relying on direct vocal transformation. [4]
- Small-Music is the local, CPU-capable music-oriented model; Medium requires CUDA; Large is API-only. [1, 2]

The following are **general engineering recommendations, not SA3 requirements**: a specific LUFS target, mandatory denoising/dereverb, a fixed API input bit depth/channel count, or exact preservation of BPM/key/stem isolation.

## Recommended preprocessing pipeline

### Priority 0: preserve the existing guide-track design

Do not replace the synthetic guide with raw vocal input as the default. Direct-vocal audio-to-audio is worth a small experiment, especially for beatbox, but it is not dependable enough to be the core path: low noise may retain vocal identity/content; high noise can lose rhythmic and melodic structure.

Run an immediate backend contract test using 30-second, 44.1 kHz stereo guide WAVs through both local and API backends. Record returned duration, channels, sample rate, peak, and audible timing. Test mono and dual-mono API input once, then standardize on the better behavior.

### Priority 1: conservative preprocessing before analysis

Create a `vocal_analysis.wav` and quality report while retaining the original upload.

1. **Decode and validate**
   - Use an FFmpeg-backed decode path if browser WebM/Opus recordings must be accepted reliably.
   - Set file-size and duration limits; reject empty or near-silent files.
   - Record source container/codec when available, sample rate, channels, duration, peak, RMS, and clipped-sample fraction.

2. **Downmix deliberately**
   - Preserve the original channel layout separately.
   - Generate a dedicated mono analysis signal.
   - Flag severe left/right mismatch or phase-cancellation risk; simple averaging can reduce a phase-inverted vocal.

3. **Remove DC offset**
   - Subtract the mean or use a very-low-frequency high-pass. This is low-risk and cheap.

4. **Trim exterior silence only**
   - Use a conservative RMS/VAD threshold for leading/trailing silence, retaining roughly 100–250 ms padding.
   - Do not remove internal rests: phrase gaps are useful melodic evidence.
   - Store the original time offset in metadata if trimming changes time zero.

5. **Use a modest, content-aware high-pass filter**
   - For singing/humming with rumble, approximately 60–80 Hz is a reasonable starting experiment.
   - For beatbox, bypass or use a lower cutoff around 25–35 Hz so kick-like energy is not removed.

6. **Normalize with headroom**
   - Use robust peak/percentile/RMS normalization with headroom, e.g. a target peak around −3 dBFS.
   - Do not pretend normalization repairs clipping: flag clipped recordings and request a re-record.
   - Avoid mandatory compression/limiting; attacks and dynamics are analysis evidence.

7. **Return a quality report**
   - Surface clipped, too-quiet, excessive-silence, likely accompaniment/polyphony, noisy, or reverb-heavy warnings.
   - Keep BPM/key/chord correction as the primary recovery mechanism.

### Priority 2: prepare the synthetic guide strictly for SA3

1. Render/export 44.1 kHz stereo float WAV. For mono arrangements, create intentional dual mono.
2. Enforce exact target duration and avoid accidental leading silence.
3. Add 5–20 ms fades at the endpoints to prevent clicks.
4. Validate peak level and prevent clipping.
5. Keep the guide sparse and target-specific: clear bass attacks, unambiguous piano chord attacks, sharp drum events, or only the intended harmony line.
6. Preserve attacks and pitch cues; do not over-smooth the guide.
7. Experiment with the optional `TrackType: Instrument` prompt signal alongside current explicit instrument/isolation wording. [4]
8. Use controlled per-part noise sweeps: `0.5, 0.65, 0.8, 0.9`, evaluated by listening and alignment metrics.

### Priority 3: optional enhancement experiments

| Experiment | Use case | Principal risk |
|---|---|---|
| Mild stationary-noise reduction | HVAC/hiss affects onset/f0 | Musical-noise artifacts and changed consonants |
| Dereverberation | Room echo smears onsets/chroma | Removes sustain/vibrato or creates artifacts |
| Voice-activity detection | Long silence/non-vocal regions | Cutting quiet phrase boundaries |
| Alternative pitch tracker, e.g. CREPE | `pyin` fails on noisy/breathy singing | Extra dependency and tracker disagreement |
| Beatbox-specific mode | Beatbox input | Singer-oriented filtering erases transient/low-end cues |
| Source separation | Authorized input has accompaniment | Artifacts and compute/setup cost |

Keep all enhancement options behind A/B controls. Preserve the conservative baseline: speech-enhancement systems can improve perceived cleanliness while making pitch/onset analysis worse through artifacts. [10, 11]

## Recommended product paths

### Ship: conservative cleanup plus synthetic instrumental guide

This is the best hackathon choice: low risk, minimal dependencies, clear musician feedback, and compatible with the project’s core design.

### Experiment: direct raw-vocal SA3 transformation

Test voice/hum or beatbox input at noise values 0.3, 0.5, 0.7, and 0.85 against the synthetic-guide baseline. Compare timing/key adherence, residual vocal content, and stem isolation. Do not present it as faithful conversion unless evaluated.

### Add later: separate voice and beatbox modes

- **Voice mode:** vocal-focused high-pass, pitch/key analysis, harmonic guide.
- **Beatbox mode:** preserve low end/transients, prioritize onsets/tempo, create a drum guide, and reduce reliance on pitch/chroma.

### Add later: inpainting repair

After generating a good stem, use SA3 inpainting to repair a weak fill or transition rather than regenerate an entire approved stem. [1, 4]

## Concrete implementation plan

No application code was changed by this research.

### New module

Create `backend/preprocess.py` with a quality contract such as:

```python
@dataclass
class InputQuality:
    source_sr: int
    source_channels: int
    duration_s: float
    peak_dbfs: float
    rms_dbfs: float
    clipped_fraction: float
    leading_trim_s: float
    trailing_trim_s: float
    warnings: list[str]

def preprocess_for_analysis(path) -> tuple[np.ndarray, int, InputQuality]:
    # decode → validate → controlled downmix → DC removal
    # → content-aware HPF → exterior trim/pad → headroom normalization
    ...
```

### Integration points

1. **`backend/pipeline.py::analyze_vocal`** — replace direct decode/downmix/resample logic; preserve raw upload and store quality results.
2. **`backend/session.py` / `backend/models.py`** — store input provenance and quality report separately from analysis output.
3. **`backend/api.py`** — validate upload presence, size, duration, and decodability; return warnings with analysis; use FFmpeg if WebM support is required.
4. **`frontend/index.html` / `frontend/app.js`** — add quiet-room/one-voice/headphones/no-clipping recording guidance and quality-warning/re-record UI.
5. **`backend/sa3_backend.py`** — add shared guide-to-stereo WAV validation for local and API paths; log guide channels, rate, duration, and peak.
6. **`pyproject.toml`** — document FFmpeg as a system dependency if adopted; make enhancement models optional extras only after baseline evaluation.

## Validation plan

### Input processing

Compare raw baseline, conservative preprocessing, and each optional enhancement on clean singing, noisy/reverberant/breathy/clipped takes, and beatbox recordings:

- decode success and processing time;
- source/channel/sample-rate metadata;
- clipping fraction, DC offset, peak/RMS;
- tempo/downbeat/key accuracy versus annotations;
- beatbox onset F-score where applicable;
- manual-correction rate;
- listening checks for consonants, vibrato, and kick/snare attacks.

### SA3 guide processing

For each part/backend, verify guide rate, stereo channels, duration, and non-clipping. Evaluate the proposed noise sweep with duration error, onset/grid drift, unwanted-vocal/instrument bleed, prompt adherence, and blind listening rankings. Log seed and cache presentation outputs.

### Demo acceptance criteria

- Browser recording and WebM upload decode on the presentation machine.
- Input warnings appear instead of opaque failures.
- At least one clean vocal and one beatbox example yield usable, editable analysis.
- A guide-fed SA3 bass or piano stem aligns reliably enough for the demo.
- A complete successful session is cached before presentation.
- Mock/offline fallback remains functional.

## Methodology and limitations

A dedicated research sub-agent inspected the project’s pipeline, analysis, adapter, API, frontend input flow, configuration, plan, README, and dependency manifest. It prioritized official Stable Audio 3 documentation, source repository, technical report, model information, and prompt guidance; general scholarly sources were used only to contextualize pitch tracking and enhancement artifacts.

The Stable Audio 3 technical report is an arXiv preprint, not confirmed peer-reviewed. The exact authenticated API contract and its accepted container/channel behavior should be verified with a real current request. Existing tempo/key results are based on synthetic fixtures and do not establish real-recording robustness.

## References

1. Evans, Zach, Julian D. Parker, Matthew Rice, CJ Carr, Zack Zukowski, Josiah Taylor, and Jordi Pons. “Stable Audio 3.” *arXiv:2605.17991*, 18 May 2026. Scholarly preprint; peer-review status not established.  
   https://doi.org/10.48550/arXiv.2605.17991

2. Stability AI. *Stable Audio 3* GitHub repository and README, 2026; accessed 2026-08-22. Official source documentation; not peer-reviewed.  
   https://github.com/Stability-AI/stable-audio-3

3. Stability AI. “Introducing Stable Audio 3 & SAME (Semantically-Aligned Music Autoencoder).” Stability AI Research, 20 May 2026. Official vendor research announcement; not peer-reviewed.  
   https://stability.ai/research/stable-audio-3

4. Stability AI. “Stable Audio 3 Prompt Guide.” Stability AI Knowledge Base, accessed 2026-08-22. Official product guidance; not peer-reviewed.  
   https://kb.stability.ai/knowledge-base/stable-audio-3-prompt-guide

5. Stability AI. “Stable Audio 3 Small Music.” Hugging Face model card, accessed 2026-08-22. Official model card; not peer-reviewed.  
   https://huggingface.co/stabilityai/stable-audio-3-small-music

6. Backing Track Generator. `README.md`. Project primary documentation, accessed 2026-08-22. Not peer-reviewed.

7. Backing Track Generator. `PLAN.md`. Project design/delivery plan, accessed 2026-08-22. Not peer-reviewed.

8. Backing Track Generator. `backend/pipeline.py`, `backend/analysis.py`, `backend/config.py`, `backend/session.py`, `backend/sa3_backend.py`, `backend/api.py`, `frontend/app.js`, `frontend/index.html`, and `pyproject.toml`. Project implementation/configuration, accessed 2026-08-22. Not peer-reviewed.

9. Kim, Jong Wook, Justin Salamon, Peter Li, and Juan Pablo Bello. “CREPE: A Convolutional Representation for Pitch Estimation.” *IEEE International Conference on Acoustics, Speech and Signal Processing*, 2018. Peer-reviewed conference paper.  
   https://arxiv.org/abs/1802.06182

10. Guan, Haixin, et al. “Reducing Speech Distortion and Artifacts for Speech Enhancement by Loss Function.” *Interspeech 2024*. Peer-reviewed conference paper.  
    https://www.isca-archive.org/interspeech_2024/guan24_interspeech.pdf

11. Wu, et al. “Low-complexity artificial noise suppression methods for deep learning-based speech enhancement.” *EURASIP Journal on Audio, Speech, and Music Processing*, 2021. Peer-reviewed journal article.  
    https://link.springer.com/article/10.1186/s13636-021-00204-9
