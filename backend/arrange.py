"""Stage 2: turn the analysis into MIDI for each backing part.

These arrangers are deliberately simple and deterministic. Their job is
not to sound good — it is to put the right notes at the right times so
Stable Audio 3 has a correct skeleton to work from. The MIDI is also
exported to the user, so it doubles as a DAW-editable starting point.

To add a part: write an `_arrange_<name>` function and register it in
ARRANGERS.
"""

from __future__ import annotations

import librosa
import numpy as np
import pretty_midi

from .models import Analysis, Part
from .theory import chord_to_midi, parse_chord, transpose_diatonic

# General MIDI program numbers, so exported MIDI opens with sane sounds.
GM_PROGRAMS = {"bass": 33, "piano": 0, "harmony": 52}  # finger bass, grand piano, choir aahs

# General MIDI percussion note numbers.
DRUM_KICK, DRUM_SNARE, DRUM_HAT = 36, 38, 42


def arrange(part: Part, analysis: Analysis, vocal: np.ndarray, sr: int) -> pretty_midi.PrettyMIDI:
    """Build the MIDI for one part. `vocal` is only used by the harmony arranger."""
    if part not in ARRANGERS:
        raise ValueError(f"unknown part: {part}")
    return ARRANGERS[part](analysis, vocal, sr)


def _new_midi(analysis: Analysis, part: Part) -> tuple[pretty_midi.PrettyMIDI, pretty_midi.Instrument]:
    midi = pretty_midi.PrettyMIDI(initial_tempo=analysis.bpm)
    instrument = pretty_midi.Instrument(
        program=GM_PROGRAMS.get(part, 0),
        is_drum=(part == "drums"),
        name=part,
    )
    midi.instruments.append(instrument)
    return midi, instrument


def _add(instrument: pretty_midi.Instrument, pitch: int, start: float, end: float, velocity: int) -> None:
    """Add a note, clamping pitch into the valid MIDI range."""
    instrument.notes.append(
        pretty_midi.Note(
            velocity=velocity,
            pitch=int(np.clip(pitch, 0, 127)),
            start=float(start),
            end=float(max(end, start + 0.01)),
        )
    )


# --- bass ---------------------------------------------------------------


def _arrange_bass(analysis: Analysis, vocal: np.ndarray, sr: int) -> pretty_midi.PrettyMIDI:
    """Root-fifth pattern on the beat: root, root, fifth, root."""
    midi, instrument = _new_midi(analysis, "bass")
    beat = analysis.seconds_per_beat

    for bar in analysis.bars:
        root_pc, _ = parse_chord(bar.chord)
        root = 36 + root_pc  # octave 2
        pattern = [root, root, root + 7, root]

        for i, pitch in enumerate(pattern):
            start = bar.start + i * beat
            if start >= bar.end:
                break
            _add(instrument, pitch, start, min(start + beat * 0.9, bar.end), velocity=95)

    return midi


# --- piano --------------------------------------------------------------


def _arrange_piano(analysis: Analysis, vocal: np.ndarray, sr: int) -> pretty_midi.PrettyMIDI:
    """Block triads on beats 1 and 3, held for two beats each."""
    midi, instrument = _new_midi(analysis, "piano")
    beat = analysis.seconds_per_beat

    for bar in analysis.bars:
        triad = chord_to_midi(bar.chord, octave=4)
        for beat_index in (0, 2):
            start = bar.start + beat_index * beat
            if start >= bar.end:
                break
            end = min(start + beat * 2, bar.end)
            for pitch in triad:
                _add(instrument, pitch, start, end, velocity=75)

    return midi


# --- drums --------------------------------------------------------------


def _arrange_drums(analysis: Analysis, vocal: np.ndarray, sr: int) -> pretty_midi.PrettyMIDI:
    """Basic backbeat: kick on 1 and 3, snare on 2 and 4, hats on eighths."""
    midi, instrument = _new_midi(analysis, "drums")
    beat = analysis.seconds_per_beat

    for bar in analysis.bars:
        for beat_index in range(4):
            start = bar.start + beat_index * beat
            if start >= bar.end:
                break

            if beat_index in (0, 2):
                _add(instrument, DRUM_KICK, start, start + 0.1, velocity=100)
            else:
                _add(instrument, DRUM_SNARE, start, start + 0.1, velocity=90)

            # Eighth-note hats, quieter on the offbeat.
            for half in (0.0, 0.5):
                hat_start = start + half * beat
                if hat_start < bar.end:
                    _add(instrument, DRUM_HAT, hat_start, hat_start + 0.05,
                         velocity=70 if half == 0.0 else 55)

    return midi


# --- harmony ------------------------------------------------------------

# Confidence floor for pitch tracking. Frames below this are treated as
# unvoiced (breaths, consonants, silence) and produce no harmony note.
PYIN_CONFIDENCE = 0.5

# Ignore blips shorter than this; they are usually tracking artifacts.
MIN_NOTE_DURATION = 0.08


def _arrange_harmony(analysis: Analysis, vocal: np.ndarray, sr: int) -> pretty_midi.PrettyMIDI:
    """Track the vocal's pitch, then sing a diatonic third above it.

    This is the part most directly derived from the user's own melody,
    which makes it the strongest demo moment — and the most fragile, since
    it depends on clean monophonic pitch tracking.
    """
    midi, instrument = _new_midi(analysis, "harmony")
    mono = librosa.to_mono(vocal) if vocal.ndim > 1 else vocal

    for pitch, start, end in _track_melody(mono, sr):
        harmonized = transpose_diatonic(pitch, steps=2, key=analysis.key, mode=analysis.mode)
        _add(instrument, harmonized, start, end, velocity=80)

    return midi


def _track_melody(mono: np.ndarray, sr: int) -> list[tuple[int, float, float]]:
    """Pitch-track a monophonic vocal into (midi_pitch, start, end) notes.

    Consecutive frames at the same pitch are merged into one note, which
    is what turns a frame-rate f0 contour into something playable.
    """
    f0, voiced, confidence = librosa.pyin(
        mono,
        fmin=float(librosa.note_to_hz("C2")),
        fmax=float(librosa.note_to_hz("C6")),
        sr=sr,
    )
    times = librosa.times_like(f0, sr=sr)

    notes: list[tuple[int, float, float]] = []
    current_pitch: int | None = None
    note_start = 0.0

    for i, frequency in enumerate(f0):
        is_voiced = bool(voiced[i]) and confidence[i] >= PYIN_CONFIDENCE and np.isfinite(frequency)
        pitch = int(round(librosa.hz_to_midi(frequency))) if is_voiced else None

        if pitch != current_pitch:
            if current_pitch is not None and times[i] - note_start >= MIN_NOTE_DURATION:
                notes.append((current_pitch, note_start, float(times[i])))
            current_pitch = pitch
            note_start = float(times[i])

    # Close the final note, if the clip ends mid-phrase.
    if current_pitch is not None and times[-1] - note_start >= MIN_NOTE_DURATION:
        notes.append((current_pitch, note_start, float(times[-1])))

    return notes


ARRANGERS = {
    "bass": _arrange_bass,
    "piano": _arrange_piano,
    "drums": _arrange_drums,
    "harmony": _arrange_harmony,
}
