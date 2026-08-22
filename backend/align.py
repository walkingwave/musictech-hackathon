"""Stage 5: force the generated stem back onto the original's grid.

Stable Audio 3 makes no promise about tempo, even when given a guide track
and a BPM in the prompt. It usually lands close, but "close" is audible as
drift over eight bars. This module measures the drift and corrects it.

Two corrections, in order:
  1. time-stretch, so the stem's tempo matches the target
  2. shift, so its first strong onset lands on the guide's first onset
"""

from __future__ import annotations

import logging

import librosa
import numpy as np
import pyrubberband

from .config import SAMPLE_RATE

log = logging.getLogger(__name__)

# Refuse to stretch beyond this ratio. Past roughly a quarter, the model
# produced something rhythmically unrelated and stretching it just makes
# artifacts; better to leave it alone and let the user regenerate.
MAX_STRETCH = 1.25


def align(stem: np.ndarray, guide: np.ndarray, target_bpm: float) -> np.ndarray:
    """Snap `stem` to the tempo and phase of `guide`, and match its length."""
    stretched = _match_tempo(stem, target_bpm)
    shifted = _match_phase(stretched, guide)
    return _fit_length(shifted, len(guide))


def _match_tempo(stem: np.ndarray, target_bpm: float) -> np.ndarray:
    # Pass the onset envelope explicitly. beat_track's own envelope uses
    # median aggregation, which reports 0 BPM on sustained material.
    onset_env = librosa.onset.onset_strength(y=stem, sr=SAMPLE_RATE)
    detected, _ = librosa.beat.beat_track(onset_envelope=onset_env, sr=SAMPLE_RATE)
    detected = float(np.atleast_1d(detected)[0])

    if detected <= 0:
        log.warning("no tempo detected in stem; skipping stretch")
        return stem

    # Beat trackers routinely report half or double time. Fold the detected
    # tempo into the same octave as the target before comparing, or we would
    # "correct" a perfectly good stem by a factor of two.
    while detected < target_bpm / 1.5:
        detected *= 2
    while detected > target_bpm * 1.5:
        detected /= 2

    ratio = detected / target_bpm
    if not (1 / MAX_STRETCH) < ratio < MAX_STRETCH:
        log.warning("stretch ratio %.3f out of range; leaving stem unstretched", ratio)
        return stem

    return pyrubberband.time_stretch(stem, SAMPLE_RATE, ratio).astype(np.float32)


def _match_phase(stem: np.ndarray, guide: np.ndarray) -> np.ndarray:
    """Slide the stem so its rhythm lines up with the guide's.

    Cross-correlating onset *envelopes* rather than raw audio is what makes
    this work across totally different timbres — a synthesized saw guide and
    a real bass share onset positions but share no waveform.
    """
    stem_onsets = librosa.onset.onset_strength(y=stem, sr=SAMPLE_RATE)
    guide_onsets = librosa.onset.onset_strength(y=guide, sr=SAMPLE_RATE)

    if len(stem_onsets) < 2 or len(guide_onsets) < 2:
        return stem

    correlation = np.correlate(guide_onsets, stem_onsets, mode="full")
    lag_frames = int(np.argmax(correlation)) - (len(stem_onsets) - 1)

    # Onset frames use librosa's default 512-sample hop.
    lag_samples = lag_frames * 512

    if lag_samples > 0:
        return np.concatenate([np.zeros(lag_samples, dtype=np.float32), stem])
    if lag_samples < 0:
        return stem[-lag_samples:]
    return stem


def _fit_length(audio: np.ndarray, target_length: int) -> np.ndarray:
    """Trim or zero-pad so every stem is exactly the session length."""
    if len(audio) > target_length:
        return audio[:target_length]
    return np.pad(audio, (0, target_length - len(audio))).astype(np.float32)
