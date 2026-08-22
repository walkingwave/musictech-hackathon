"""Stage 1: work out the musical structure of the input vocal.

Everything downstream depends on this being roughly right, so each
estimate is exposed to the user for correction in the UI rather than
being treated as ground truth.
"""

from __future__ import annotations

import logging

import librosa
import numpy as np

from .models import Analysis, Bar
from .theory import KRUMHANSL, TRIADS, pitch_class_to_note

log = logging.getLogger(__name__)


# Used when beat tracking fails outright, so the rest of the pipeline still
# has a usable grid. The user can correct it in the UI.
FALLBACK_BPM = 120.0


def analyze(audio: np.ndarray, sr: int) -> Analysis:
    """Vocal audio -> tempo, downbeat, key and a per-bar chord grid."""
    mono = librosa.to_mono(audio) if audio.ndim > 1 else audio

    # Computed once and shared. Note this must be passed to beat_track
    # explicitly: letting beat_track derive its own envelope from `y` uses
    # median aggregation, which collapses to a flat envelope (and a
    # reported tempo of 0) on clean, sustained material like a solo vocal.
    onset_env = librosa.onset.onset_strength(y=mono, sr=sr)

    bpm, beat_times = _estimate_tempo(onset_env, sr)
    downbeat = _estimate_downbeat(onset_env, sr, beat_times)
    key, mode = _estimate_key(mono, sr)
    bars = _estimate_chords(mono, sr, bpm, downbeat, key, mode)

    return Analysis(
        bpm=float(bpm),
        downbeat_offset_s=float(downbeat),
        key=key,
        mode=mode,
        duration=len(mono) / sr,
        bars=bars,
    )


def _estimate_tempo(onset_env: np.ndarray, sr: int) -> tuple[float, np.ndarray]:
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, units="frames")
    tempo = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)

    if tempo <= 0:
        log.warning("beat tracking failed; falling back to %.0f BPM", FALLBACK_BPM)
        return FALLBACK_BPM, beat_times

    return tempo, beat_times


def _estimate_downbeat(onset_env: np.ndarray, sr: int, beat_times: np.ndarray) -> float:
    """Pick which of the first four beats is beat 1.

    We assume 4/4 and score each candidate by the total onset strength of
    the beats it would make downbeats. The strongest wins, on the theory
    that people accent beat 1.
    """
    if len(beat_times) < 4:
        return float(beat_times[0]) if len(beat_times) else 0.0

    onset_times = librosa.times_like(onset_env, sr=sr)

    best_offset, best_score = 0, -np.inf
    for offset in range(4):
        downbeats = beat_times[offset::4]
        # Onset strength sampled at each candidate downbeat.
        score = float(np.sum(np.interp(downbeats, onset_times, onset_env)))
        if score > best_score:
            best_offset, best_score = offset, score

    return float(beat_times[best_offset])


def _estimate_key(mono: np.ndarray, sr: int) -> tuple[str, str]:
    """Correlate average chroma against all 24 Krumhansl-Schmuckler profiles."""
    chroma = librosa.feature.chroma_cqt(y=mono, sr=sr)
    average = chroma.mean(axis=1)

    best = ("C", "major", -np.inf)
    for mode, profile in KRUMHANSL.items():
        for tonic in range(12):
            rotated = np.roll(profile, tonic)
            score = float(np.corrcoef(average, rotated)[0, 1])
            if score > best[2]:
                best = (pitch_class_to_note(tonic), mode, score)

    return best[0], best[1]


def _estimate_chords(
    mono: np.ndarray,
    sr: int,
    bpm: float,
    downbeat: float,
    key: str,
    mode: str,
) -> list[Bar]:
    """Match each bar's average chroma against 24 triad templates.

    A solo vocal genuinely underdetermines harmony — one melody fits many
    chord progressions. Treat the result as a starting point the user
    edits, which is why the UI exposes the chord grid.
    """
    seconds_per_bar = (60.0 / bpm) * 4
    duration = len(mono) / sr
    chroma = librosa.feature.chroma_cqt(y=mono, sr=sr)
    chroma_times = librosa.times_like(chroma, sr=sr)

    templates = _triad_templates()

    bars: list[Bar] = []
    index = 0
    start = downbeat
    while start < duration:
        end = min(start + seconds_per_bar, duration)

        window = (chroma_times >= start) & (chroma_times < end)
        if window.any():
            average = chroma[:, window].mean(axis=1)
            chord = max(templates, key=lambda name: float(average @ templates[name]))
        else:
            chord = f"{key}m" if mode == "minor" else key

        bars.append(Bar(index=index, start=float(start), end=float(end), chord=chord))
        index += 1
        start = end

    return bars


def _triad_templates() -> dict[str, np.ndarray]:
    """24 binary chroma vectors, one per major and minor triad."""
    templates: dict[str, np.ndarray] = {}
    for root in range(12):
        for quality, intervals in TRIADS.items():
            vector = np.zeros(12)
            for interval in intervals:
                vector[(root + interval) % 12] = 1.0
            name = pitch_class_to_note(root) + ("m" if quality == "min" else "")
            templates[name] = vector / np.linalg.norm(vector)
    return templates
