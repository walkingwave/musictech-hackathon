"""Monophonic pitch tracking: audio -> a list of notes.

Used by two stages for different reasons, which is why it lives on its own:

  analysis.py  key detection. Note *durations* and where phrases *land*
               are far stronger evidence of a tonic than a raw chroma
               histogram, which cannot tell a key from its relative.
  arrange.py   the harmony part, which sings a third above these notes.
"""

from __future__ import annotations

import librosa
import numpy as np
from scipy.ndimage import median_filter

from . import config
from .pitch_tracking import Note, TrackingDiagnostics, TrackingFrame, TrackingResult

# Frames below this pyin confidence are treated as unvoiced - breaths,
# consonants, silence - and produce no note.
CONFIDENCE_FLOOR = 0.30

# Ignore blips shorter than this. They are usually tracking artifacts
# during a pitch transition rather than intended notes.
MIN_DURATION = 0.06

# Vocal range to search. Wider costs time and invites octave errors.
FMIN_NOTE = "C2"
FMAX_NOTE = "C6"


# Median-filter window, in frames, applied to the pitch contour before
# rounding to semitones. Without it, vibrato swinging across a semitone
# boundary chops a single held note into a stutter of short fragments -
# which destroys exactly the duration evidence key detection depends on.
# At the default hop this is roughly 58ms: enough to suppress a one-frame
# octave glitch, but short enough that a deliberately hummed pitch change is
# not averaged away.
SMOOTHING_FRAMES = 7

# Same-pitch notes separated by less than this are one note that the
# tracker briefly lost, not two notes. Raised from 40ms after testing on
# real hums: a consonant or a breath inside a held note reads as an
# unvoiced gap of 60-90ms, and at 40ms every "da-da" syllable split one
# note into two.
MERGE_GAP = 0.09


class PyinTracker:
    """Current dependency-free tracker, normalized to the shared contract."""

    id = "pyin"
    version = librosa.__version__

    def track(self, mono: np.ndarray, sr: int) -> TrackingResult:
        f0, voiced, confidence = librosa.pyin(
            mono,
            fmin=float(librosa.note_to_hz(FMIN_NOTE)),
            fmax=float(librosa.note_to_hz(FMAX_NOTE)), sr=sr,
        )
        times = librosa.times_like(f0, sr=sr)
        usable = voiced & np.isfinite(f0) & (confidence >= CONFIDENCE_FLOOR)
        frames = [
            TrackingFrame(
                time=float(time), f0_hz=float(value) if np.isfinite(value) else None,
                midi=float(librosa.hz_to_midi(value)) if np.isfinite(value) else None,
                voiced=bool(is_voiced), confidence=float(score) if np.isfinite(score) else None,
            )
            for time, value, is_voiced, score in zip(times, f0, voiced, confidence)
        ]
        pitches = _smoothed_pitches(f0, voiced, confidence)
        segmented, discarded = _segment(pitches, times)
        notes, merged = _merge_fragments(segmented)
        diagnostics = TrackingDiagnostics(
            total_frames=len(frames), voiced_frames=int(np.sum(voiced)),
            rejected_unvoiced=int(np.sum(~voiced)),
            rejected_low_confidence=int(np.sum(voiced & ~usable)),
            segmented_notes=len(segmented), discarded_short_notes=discarded, merged_notes=merged,
        )
        if len(notes) < 2:
            diagnostics.warnings.append("fewer than two stable pitched notes were detected")
        return TrackingResult(self.id, self.version, notes, frames, diagnostics)


def tracker() -> PyinTracker | object:
    """Select an optional validated tracker, always retaining pYIN fallback."""
    if config.PITCH_TRACKER in ("basic-pitch", "auto"):
        try:
            from .basic_pitch_tracker import BasicPitchTracker
            return BasicPitchTracker()
        except RuntimeError:
            # Optional-model installation/runtime failure must never make a
            # hum unusable; provenance records that pYIN actually ran.
            pass
    return PyinTracker()


def track_with_diagnostics(mono: np.ndarray, sr: int) -> TrackingResult:
    """Pitch-track one hum and retain frame-level evidence for inspection."""
    return tracker().track(mono, sr)


def track(mono: np.ndarray, sr: int) -> list[Note]:
    """Compatibility helper for existing analysis and arranger callers."""
    return track_with_diagnostics(mono, sr).notes


def _smoothed_pitches(f0, voiced, confidence) -> list[int | None]:
    """f0 contour -> one rounded MIDI pitch per frame, or None if unvoiced.

    Smoothing happens in the MIDI domain rather than in Hz, so the filter
    window means the same thing at every pitch.
    """
    midi = np.full(len(f0), np.nan, dtype=np.float64)
    usable = voiced & np.isfinite(f0) & (confidence >= CONFIDENCE_FLOOR)
    midi[usable] = librosa.hz_to_midi(f0[usable])

    # Median-filter only the voiced frames, so unvoiced gaps do not bleed
    # into the filter window and drag pitches toward zero.
    filled = np.copy(midi)
    if usable.any():
        filled[~usable] = np.interp(
            np.flatnonzero(~usable), np.flatnonzero(usable), midi[usable]
        )
        filled = median_filter(filled, size=SMOOTHING_FRAMES, mode="nearest")
        # Octave-error folding: pyin's classic failure on a hum is a frame or
        # three that jump a clean octave (subharmonic lock). The median filter
        # softens those into wrong in-between pitches instead of removing
        # them, so fold anything an octave-ish away from its local context
        # back toward it before rounding.
        context_window = median_filter(filled, size=25, mode="nearest")
        for _ in range(2):  # a double jump needs two folds
            above = filled - context_window >= 7
            below = context_window - filled >= 7
            filled[above] -= 12
            filled[below] += 12

    return [int(round(filled[i])) if usable[i] else None for i in range(len(f0))]


def _segment(pitches: list[int | None], times: np.ndarray) -> tuple[list[Note], int]:
    """Runs of equal pitch become notes, with rejected-fragment accounting."""
    notes: list[Note] = []
    discarded = 0
    current: int | None = None
    start = 0.0

    for i, pitch in enumerate(pitches):
        if pitch != current:
            if current is not None:
                if times[i] - start >= MIN_DURATION:
                    notes.append(Note(pitch=current, start=start, end=float(times[i])))
                else:
                    discarded += 1
            current = pitch
            start = float(times[i])

    # Close the final note, if the clip ends mid-phrase.
    if current is not None:
        if times[-1] - start >= MIN_DURATION:
            notes.append(Note(pitch=current, start=start, end=float(times[-1])))
        else:
            discarded += 1

    return notes, discarded


def _merge_fragments(notes: list[Note]) -> tuple[list[Note], int]:
    """Join same-pitch notes separated by only a tracking dropout."""
    if not notes:
        return [], 0

    merged = [notes[0]]
    merged_count = 0
    for note in notes[1:]:
        previous = merged[-1]
        if note.pitch == previous.pitch and note.start - previous.end <= MERGE_GAP:
            merged[-1] = Note(pitch=previous.pitch, start=previous.start, end=note.end)
            merged_count += 1
        else:
            merged.append(note)

    return merged, merged_count


def duration_histogram(notes: list[Note]) -> np.ndarray:
    """Pitch-class histogram weighted by how long each note is held.

    Duration weighting matters: a held half note says far more about the
    key than a passing sixteenth, but a frame-count histogram already
    reflects that, whereas a note-count histogram does not.
    """
    histogram = np.zeros(12, dtype=np.float64)
    for note in notes:
        histogram[note.pitch_class] += note.duration

    total = histogram.sum()
    return histogram / total if total > 0 else histogram


# A note is treated as phrase-final if a rest of at least this long
# follows it. Kept small because singers phrase legato: the gap between
# phrases is often only a breath.
PHRASE_GAP = 0.15

# ...or if it is held at least this much longer than the local median.
# Legato singing may leave no gap at all, but a resolution is nearly
# always *held*, so duration catches the endings that silence misses.
PHRASE_LONG_RATIO = 1.4


def phrase_endings(notes: list[Note]) -> list[Note]:
    """Notes that end a phrase - followed by a rest, held long, or last.

    Melodies resolve at phrase boundaries, so these notes carry
    disproportionate evidence about the tonic. This is the cue that
    distinguishes A minor from C major, which share every pitch and are
    therefore indistinguishable by pitch distribution alone.

    Detecting endings by silence alone is not enough: on legato material
    the only phrase marker is that the resolving note is held.
    """
    if not notes:
        return []

    median_duration = float(np.median([note.duration for note in notes]))
    long_enough = median_duration * PHRASE_LONG_RATIO

    endings = []
    for i, note in enumerate(notes):
        is_last = i == len(notes) - 1
        followed_by_rest = not is_last and notes[i + 1].start - note.end >= PHRASE_GAP

        if is_last or followed_by_rest or note.duration >= long_enough:
            endings.append(note)

    return endings
