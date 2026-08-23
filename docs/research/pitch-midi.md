# Hum-to-MIDI Pitch Tracking Research

## Scope

**Research question:** Why can the current hum-to-MIDI path fail to preserve a clearly hummed melody, and which practical transcription tools should replace or augment it?

**Date conducted:** 2026-08-23  
**Project:** Backing Track Generator

## Executive summary

The current tracker is `librosa.pyin` followed by median smoothing, semitone rounding, duration filtering, and note merging. This is a reasonable lightweight F0 baseline, but it is not an onset-aware audio-to-MIDI system. It can therefore lose short pitch changes, merge intended repeated notes, and emit erroneous notes when its confidence threshold is lowered. Separately, Stable Audio audio-to-audio is a generative transformation, not a deterministic MIDI renderer; even perfect MIDI will not guarantee that the final generated audio reproduces a hummed phrase.

**Recommendation:** make transformed MIDI and its deterministic guide WAV the fidelity-critical outputs. Add a pluggable transcription layer and evaluate two candidates on project-owned hum recordings: **Spotify Basic Pitch** as the preferred audio-to-MIDI candidate, and **torchcrepe/CREPE F0 plus explicit note segmentation** as the pitch-contour candidate. Keep pYIN as a no-extra-dependency fallback. Do not use MT3 as the first replacement: it is designed for general multi-instrument transcription and is disproportionately heavy for short monophonic hums.

## Current-path diagnosis

```text
hum WAV
  -> librosa.pyin F0/confidence
  -> median smoothing
  -> rounded MIDI pitches
  -> segment equal-pitch runs
  -> duration filter + same-pitch merge
  -> key snapping / rhythmic quantization
  -> MIDI guide WAV
  -> Stable Audio audio-to-audio
```

The current implementation can alter a source melody at several independent stages:

1. pYIN marks frames unvoiced or low-confidence; those frames create gaps rather than notes.
2. Smoothing and integer semitone rounding remove slides and can collapse nearby note changes.
3. Fixed minimum-duration and merge-gap rules trade missed notes against spurious fragments.
4. The melody transform snaps non-scale notes and quantizes timing. These should be optional user choices, not mandatory “cleanup,” when source fidelity is the product promise.
5. Stable Audio at a high noise/strength can reinterpret guide pitch, rhythm, articulation, and note count. It must be evaluated separately from MIDI extraction.

The session should retain the raw F0 curve, frame confidence, and each post-processing decision. A saved final note list alone cannot explain whether an intended note was rejected by voicing, confidence, smoothing, duration, or merging.

## Candidate tools

### 1. Spotify Basic Pitch — preferred MIDI-transcription evaluation candidate

Basic Pitch is an open-source audio-to-MIDI system from Spotify. Its published description positions it as a lightweight polyphonic pitch-transcription system, which makes it more directly suited to producing note onsets/offsets and MIDI than a bare F0 tracker. For a monophonic hum, its onset-aware note output is the important property; it should be tested rather than assumed to win on every microphone/noise condition. [3, 4]

**Use in this project:** optional backend adapter returning note events and confidence-like provenance; preserve pYIN fallback if the model/runtime is unavailable.

**Benefits:** direct MIDI output; likely better separation of repeated same-pitch notes than contour-only segmentation; open source.

**Risks:** additional model/runtime/download footprint; polyphonic design does not automatically mean best monophonic hum accuracy; must establish latency and license/deployment fit in this repository.

### 2. CREPE / torchcrepe — preferred F0-contour evaluation candidate

CREPE is a data-driven convolutional pitch estimator evaluated in the ICASSP paper below. It estimates continuous fundamental frequency rather than complete MIDI notes. `torchcrepe` is a practical PyTorch implementation family commonly used to run CREPE-like inference; it still needs the project to implement onset/offset segmentation, confidence gating, and MIDI conversion. [1]

**Use in this project:** replace `librosa.pyin` only when a continuous, inspectable F0 contour is needed; segment it with adaptive voiced/unvoiced hysteresis and note-change confirmation rather than one global median filter.

**Benefits:** neural F0 estimator; continuous contour is valuable for diagnosing hums, slides, and octave errors; PyTorch may already be present for local Stable Audio installations.

**Risks:** it is not an end-to-end MIDI transcriber; adding it alone does not solve repeated-note onset detection; CPU latency and package size must be measured.

### 3. pYIN / librosa — retain as baseline and fallback

pYIN is a probabilistic extension of YIN for F0 estimation. It is academically established and already ships in the current dependency stack. [2]

**Use in this project:** retain behind a `PitchTracker` interface and improve observability, rather than continually tuning constants globally for every performer.

**Benefits:** no new model download; deterministic local baseline; existing integration.

**Risks:** the current post-processing, rather than pYIN alone, is a major source of note loss; it is sensitive to breath, room noise, pitch transitions, and recording quality.

### 4. MT3 — not recommended for the first iteration

MT3 is a general multi-task, multi-track music transcription model. Its research results make it relevant as a high-capability reference, but it targets a broader and more compute-intensive problem than monophonic humming. [5]

**Use in this project:** future benchmark/reference only, not the default hackathon/local path.

## Evaluation design

Do not select a library by listening to one Stable Audio render. Build a consented evaluation set of 30–50 short hum clips with manually corrected MIDI ground truth, including stepwise melodies, repeated notes, leaps, vibrato, slides, soft hums, phone/browser recordings, and mild room noise.

For each tracker and configuration, measure:

- note precision, recall, and F1 with pitch tolerance of 50 cents;
- onset F1 at 50 ms and 100 ms tolerance;
- offset/duration overlap;
- octave-error rate;
- median CPU runtime and cold-start cost;
- number of user corrections required in the MIDI editor.

Evaluate two separate contracts:

1. **hum → MIDI:** compare transcription against annotated MIDI.
2. **MIDI/guide → Stable Audio:** compare guide and final audio for pitch/rhythm adherence at several noise levels. Do not attribute an SA3 divergence to the tracker.

## Recommended decision

1. First preserve the current pYIN output and expose its diagnostics; make scale snapping and timing quantization opt-in.
2. Benchmark Basic Pitch against pYIN on the hum set. Promote it only if it materially improves note/onset F1 and remains acceptable on CPU.
3. If contour quality—not note onset quality—is the failure mode, benchmark torchcrepe plus adaptive segmentation.
4. Offer two user-visible outputs: **Faithful MIDI + guide** and **Stable Audio render**. The latter is creative variation, not a fidelity guarantee.

## Methodology and limitations

This review prioritized peer-reviewed conference publications for pYIN, CREPE, and MT3, then consulted Basic Pitch’s official project materials for its implementation/distribution. Tool suitability is an engineering recommendation, not a claim that one model will be best for every hum. Basic Pitch and torchcrepe require local benchmarking on this project’s browser-recorded, real-user audio before adoption. Stable Audio behavior is outside the scope of transcription-model accuracy and needs a separate controlled sweep.

## References

1. Kim, Jong Wook; Salamon, Justin; Li, Peter; Bello, Juan Pablo. **“CREPE: A Convolutional Representation for Pitch Estimation.”** ICASSP 2018. Peer-reviewed conference paper. DOI: https://doi.org/10.1109/ICASSP.2018.8461329
2. Mauch, Matthias; Dixon, Simon. **“pYIN: A Fundamental Frequency Estimator Using Probabilistic Threshold Distributions.”** ICASSP 2014. Peer-reviewed conference paper. DOI: https://doi.org/10.1109/ICASSP.2014.6853678
3. Bittner, Rachel M. et al. **“A Lightweight Instrument-Agnostic Model for Polyphonic Note Transcription and Multipitch Estimation.”** ISMIR 2022 (Basic Pitch). Peer-reviewed conference publication/project paper. Project: https://basicpitch.spotify.com/  Paper/project repository: https://github.com/spotify/basic-pitch
4. Spotify. **Basic Pitch documentation and source repository.** Official project source; implementation documentation, not peer reviewed. https://github.com/spotify/basic-pitch
5. Gardner, Josh et al. **“MT3: Multi-Task Multitrack Music Transcription.”** ICLR 2022. Peer-reviewed conference paper. Preprint: https://arxiv.org/abs/2111.03017
