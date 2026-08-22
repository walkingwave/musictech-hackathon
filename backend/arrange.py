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

from . import melody
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


def _arrange_harmony(analysis: Analysis, vocal: np.ndarray, sr: int) -> pretty_midi.PrettyMIDI:
    """Track the vocal's pitch, then sing a diatonic third above it.

    This is the part most directly derived from the user's own melody,
    which makes it the strongest demo moment — and the most fragile, since
    it depends on clean monophonic pitch tracking.
    """
    midi, instrument = _new_midi(analysis, "harmony")
    mono = librosa.to_mono(vocal) if vocal.ndim > 1 else vocal

    for note in melody.track(mono, sr):
        harmonized = transpose_diatonic(note.pitch, steps=2, key=analysis.key, mode=analysis.mode)
        _add(instrument, harmonized, note.start, note.end, velocity=80)

    return midi


ARRANGERS = {
    "bass": _arrange_bass,
    "piano": _arrange_piano,
    "drums": _arrange_drums,
    "harmony": _arrange_harmony,
}
