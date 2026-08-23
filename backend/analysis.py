"""Stage 1: work out the musical structure of the input vocal.

Everything downstream depends on this being roughly right, so each
estimate is exposed to the user for correction in the UI rather than
being treated as ground truth.
"""

from __future__ import annotations

import logging

import librosa
import numpy as np

# librosa lazy-loads submodules, and `librosa.feature.rhythm` is not
# reachable through attribute access alone - it has to be imported.
import librosa.feature.rhythm

from . import melody
from .models import Analysis, Bar
from .theory import TEMPERLEY, TRIADS, pitch_class_to_note

log = logging.getLogger(__name__)


# Used when beat tracking fails outright, so the rest of the pipeline still
# has a usable grid. The user can correct it in the UI.
FALLBACK_BPM = 120.0

# Tempos are folded into this range by doubling or halving. Beat trackers
# routinely lock onto a wrong metrical level - reporting 168 for an 84 BPM
# ballad, say - and the octave is almost always the recoverable part of
# the error. Chosen wide enough to hold real tempos at their notated value.
BPM_RANGE = (70.0, 160.0)

# Widening the tempo prior. librosa's default (std_bpm=1.0, start_bpm=120)
# snaps to 120 whenever onset evidence is weak, which on a solo vocal is
# often. A wider prior lets the actual onsets win.
TEMPO_STD_BPM = 2.0


def analyze(audio: np.ndarray, sr: int, notes: list[melody.Note] | None = None) -> Analysis:
    """Vocal audio -> tempo, downbeat, key and a per-bar chord grid."""
    mono = librosa.to_mono(audio) if audio.ndim > 1 else audio

    # Computed once and shared. Note this must be passed to beat_track
    # explicitly: letting beat_track derive its own envelope from `y` uses
    # median aggregation, which collapses to a flat envelope (and a
    # reported tempo of 0) on clean, sustained material like a solo vocal.
    onset_env = librosa.onset.onset_strength(y=mono, sr=sr)

    # Pitch-tracked once and shared by key detection and chord estimation.
    notes = notes if notes is not None else melody.track(mono, sr)

    bpm, beat_times = _estimate_tempo(onset_env, sr)
    downbeat = _estimate_downbeat(onset_env, sr, beat_times)
    key, mode = _estimate_key(notes)
    bars = _estimate_chords(mono, sr, bpm, downbeat, key, mode)

    return Analysis(
        bpm=float(bpm),
        downbeat_offset_s=float(downbeat),
        key=key,
        mode=mode,
        duration=len(mono) / sr,
        bars=bars,
    )


def rebuild_bar_grid(analysis: Analysis) -> None:
    """Re-slice the bars after the user corrects the tempo, in place.

    Bar boundaries are derived from BPM, so a corrected tempo has to
    re-cut them. Existing chords are carried over by position; bars that
    did not exist before inherit the last known chord.
    """
    previous = [bar.chord for bar in analysis.bars]
    fallback = previous[-1] if previous else analysis.key

    bars: list[Bar] = []
    index = 0
    start = analysis.downbeat_offset_s
    while start < analysis.duration:
        end = min(start + analysis.seconds_per_bar, analysis.duration)
        chord = previous[index] if index < len(previous) else fallback
        bars.append(Bar(index=index, start=float(start), end=float(end), chord=chord))
        index += 1
        start = end

    analysis.bars = bars


def _estimate_tempo(onset_env: np.ndarray, sr: int) -> tuple[float, np.ndarray]:
    """Tempo and beat positions, corrected for metrical-level errors.

    Tempo is estimated separately from beat tracking so the prior can be
    widened - `beat_track` exposes no `std_bpm` - and the result is then
    handed back to `beat_track` as a fixed tempo so the beat grid agrees
    with the reported BPM instead of re-deriving its own.
    """
    tempo = float(np.atleast_1d(
        librosa.feature.rhythm.tempo(onset_envelope=onset_env, sr=sr, std_bpm=TEMPO_STD_BPM)
    )[0])

    if tempo <= 0:
        log.warning("tempo estimation failed; falling back to %.0f BPM", FALLBACK_BPM)
        tempo = FALLBACK_BPM

    folded = _fold_tempo(tempo)
    if abs(folded - tempo) > 0.01:
        log.info("folded tempo %.1f -> %.1f BPM", tempo, folded)

    _, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, units="frames", bpm=folded
    )
    return folded, librosa.frames_to_time(beat_frames, sr=sr)


def _fold_tempo(bpm: float) -> float:
    """Double or halve until the tempo sits in a musically plausible range.

    A tracker reporting 168 for an 84 BPM song is not wrong about the
    pulse, only about which level of it to call the beat.
    """
    low, high = BPM_RANGE
    while bpm < low:
        bpm *= 2
    while bpm > high:
        bpm /= 2
    return bpm


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


# How much melodic landing evidence counts, relative to profile
# correlation (which is in [-1, 1]). Phrase endings are the strongest
# single cue for a tonic, so they outweigh the final and first note alone.
ENDING_WEIGHT = 0.35
FINAL_WEIGHT = 0.20
FIRST_WEIGHT = 0.10


def _estimate_key(notes: list[melody.Note]) -> tuple[str, str]:
    """Find the key from pitch-tracked notes.

    Profile correlation alone cannot distinguish a key from its relative -
    A minor and C major contain exactly the same twelve-tone distribution,
    so the histogram is identical and the correlation is a coin flip. What
    separates them is *where the melody lands*: phrases resolve to the
    tonic. So we score each candidate key by profile correlation plus
    bonuses for the tonic appearing at structurally important moments.
    """
    if not notes:
        log.warning("no pitched notes found; defaulting to C major")
        return "C", "major"

    histogram = melody.duration_histogram(notes)

    # Where the melody lands, as duration-weighted pitch-class evidence.
    endings = _pitch_class_weights(melody.phrase_endings(notes))
    final = notes[-1].pitch_class
    first = notes[0].pitch_class

    best = ("C", "major", -np.inf)
    for mode, profile in TEMPERLEY.items():
        for tonic in range(12):
            rotated = np.roll(profile, tonic)
            score = float(np.corrcoef(histogram, rotated)[0, 1])
            score += ENDING_WEIGHT * endings[tonic]
            score += FINAL_WEIGHT * (1.0 if final == tonic else 0.0)
            score += FIRST_WEIGHT * (1.0 if first == tonic else 0.0)

            if score > best[2]:
                best = (pitch_class_to_note(tonic), mode, score)

    log.debug("key %s %s (score %.3f)", best[0], best[1], best[2])
    return best[0], best[1]


def _pitch_class_weights(notes: list[melody.Note]) -> np.ndarray:
    """Duration-weighted pitch-class distribution over a subset of notes."""
    weights = np.zeros(12, dtype=np.float64)
    for note in notes:
        weights[note.pitch_class] += note.duration

    total = weights.sum()
    return weights / total if total > 0 else weights


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
