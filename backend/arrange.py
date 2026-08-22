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
from .grooves import Groove, apply_swing, for_style
from .models import Analysis, Part
from .theory import chord_to_midi, parse_chord, transpose_diatonic

# General MIDI program numbers, so exported MIDI opens with sane sounds.
GM_PROGRAMS = {"bass": 33, "piano": 0, "guitar": 25, "harmony": 52}
# finger bass, grand piano, steel acoustic guitar, choir aahs

# General MIDI percussion note numbers.
DRUM_KICK, DRUM_SNARE, DRUM_HAT = 36, 38, 42


def arrange(
    part: Part, analysis: Analysis, vocal: np.ndarray, sr: int, style: str = ""
) -> pretty_midi.PrettyMIDI:
    """Build the MIDI for one part.

    `style` selects the rhythmic groove. It matters more than it looks:
    the guide track built from this MIDI is what fixes the output's rhythm,
    so a genre that is not expressed here cannot appear in the result no
    matter what the text prompt says.

    `vocal` is only used by the harmony arranger.
    """
    if part not in ARRANGERS:
        raise ValueError(f"unknown part: {part}")
    return ARRANGERS[part](analysis, vocal, sr, for_style(style))


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


def _arrange_bass(
    analysis: Analysis, vocal: np.ndarray, sr: int, groove: Groove
) -> pretty_midi.PrettyMIDI:
    """Bass notes where the groove puts them, on chord tones."""
    midi, instrument = _new_midi(analysis, "bass")
    beat = analysis.seconds_per_beat

    for bar in analysis.bars:
        triad = chord_to_midi(bar.chord, octave=2)
        for position, tone in groove.bass:
            start = bar.start + apply_swing(position, groove.swing) * beat
            if start >= bar.end:
                continue
            pitch = triad[tone % len(triad)]
            _add(instrument, pitch, start, min(start + beat * 0.9, bar.end), velocity=95)

    return midi


# --- chordal parts ------------------------------------------------------


def _comp(
    analysis: Analysis,
    groove: Groove,
    part: Part,
    octave: int,
    velocity: int,
) -> pretty_midi.PrettyMIDI:
    """Chord comping shared by piano and guitar — only the register differs.

    The rhythm comes entirely from the groove, which is what lets a bossa
    comp land on the offbeats while a rock comp sits on 1 and 3.
    """
    midi, instrument = _new_midi(analysis, part)
    beat = analysis.seconds_per_beat

    for bar in analysis.bars:
        triad = chord_to_midi(bar.chord, octave=octave)
        for position, length in groove.comp:
            start = bar.start + apply_swing(position, groove.swing) * beat
            if start >= bar.end:
                continue
            end = min(start + length * beat, bar.end)
            for pitch in triad:
                _add(instrument, pitch, start, end, velocity=velocity)

    return midi


def _arrange_piano(analysis, vocal, sr, groove):
    return _comp(analysis, groove, "piano", octave=4, velocity=75)


def _arrange_guitar(analysis, vocal, sr, groove):
    # An octave below the piano, where a guitar actually voices chords.
    return _comp(analysis, groove, "guitar", octave=3, velocity=70)


# --- drums --------------------------------------------------------------


def _arrange_drums(
    analysis: Analysis, vocal: np.ndarray, sr: int, groove: Groove
) -> pretty_midi.PrettyMIDI:
    """Kit pattern taken from the groove.

    This is the part where genre is most audible, and where a fixed
    backbeat did the most damage: a bossa nova request rendered with kick
    on 1 and 3 and snare on 2 and 4 simply is not bossa nova, whatever the
    text prompt asks for.
    """
    midi, instrument = _new_midi(analysis, "drums")
    beat = analysis.seconds_per_beat

    def hits(positions, note, velocity):
        for position in positions:
            start = bar.start + apply_swing(position, groove.swing) * beat
            if start < bar.end:
                _add(instrument, note, start, start + 0.1, velocity=velocity)

    for bar in analysis.bars:
        hits(groove.kick, DRUM_KICK, 100)
        hits(groove.snare, DRUM_SNARE, 90)
        # Offbeat hats sit back a little, so the pattern breathes.
        for position in groove.hat:
            start = bar.start + apply_swing(position, groove.swing) * beat
            if start < bar.end:
                on_beat = abs(position % 1.0) < 1e-6
                _add(instrument, DRUM_HAT, start, start + 0.05, velocity=70 if on_beat else 55)

    return midi


# --- harmony ------------------------------------------------------------


def _arrange_harmony(
    analysis: Analysis, vocal: np.ndarray, sr: int, groove: Groove
) -> pretty_midi.PrettyMIDI:
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
    "guitar": _arrange_guitar,
    "drums": _arrange_drums,
    "harmony": _arrange_harmony,
}
