"""Small music-theory helpers shared by analysis and arranging.

Deliberately minimal: note names, triads, and diatonic scales. Just enough
to place correct notes on a grid, not a general theory library.
"""

from __future__ import annotations

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Semitone offsets from the root, per chord quality.
TRIADS = {
    "maj": [0, 4, 7],
    "min": [0, 3, 7],
}

# Semitone offsets from the tonic, per mode.
SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],  # natural minor
}

# Krumhansl-Schmuckler key profiles. Correlating a piece's average chroma
# against rotations of these is the standard way to guess a key.
KRUMHANSL = {
    "major": [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    "minor": [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
}


def note_to_pitch_class(name: str) -> int:
    """"A" -> 9. Accepts flats: "Bb" -> 10."""
    name = name.strip()
    base = NOTE_NAMES.index(name[0].upper())
    for accidental in name[1:]:
        if accidental == "#":
            base += 1
        elif accidental == "b":
            base -= 1
    return base % 12


def pitch_class_to_note(pc: int) -> str:
    return NOTE_NAMES[pc % 12]


def parse_chord(chord: str) -> tuple[int, str]:
    """"Am" -> (9, "min"). "F" -> (5, "maj")."""
    quality = "min" if chord.endswith("m") else "maj"
    root_name = chord[:-1] if quality == "min" else chord
    return note_to_pitch_class(root_name), quality


def chord_to_midi(chord: str, octave: int) -> list[int]:
    """Chord symbol -> MIDI note numbers for its triad in a given octave.

    Octave 4 means the root lands between MIDI 60 and 71 (middle C region).
    """
    root_pc, quality = parse_chord(chord)
    root_midi = (octave + 1) * 12 + root_pc
    return [root_midi + interval for interval in TRIADS[quality]]


def scale_pitch_classes(key: str, mode: str) -> list[int]:
    """The seven pitch classes of a key, e.g. A minor -> [9,11,0,2,4,5,7]."""
    tonic = note_to_pitch_class(key)
    return [(tonic + step) % 12 for step in SCALES[mode]]


def transpose_diatonic(midi_note: int, steps: int, key: str, mode: str) -> int:
    """Move a note up by `steps` scale degrees, staying inside the key.

    Used by the harmony arranger: a diatonic third above the melody is
    sometimes 3 semitones and sometimes 4, depending where in the scale
    the note sits. Snapping to the scale is what makes it sound right.

    Notes outside the key snap to the nearest scale tone first.

    Works by converting the note to an absolute scale-degree index
    (octave * 7 + degree), adding `steps` there, and converting back.
    That keeps octave wrapping automatic instead of hand-rolled.
    """
    tonic = note_to_pitch_class(key)

    # Semitones above the tonic, and which octave of the key we're in.
    offset = midi_note - tonic
    key_octave, semitone_in_key = divmod(offset, 12)

    # Nearest scale degree to this semitone offset.
    degrees = SCALES[mode]
    degree = min(range(len(degrees)), key=lambda i: abs(degrees[i] - semitone_in_key))

    # Move in scale-degree space, letting the octave carry.
    absolute_degree = key_octave * 7 + degree + steps
    target_octave, target_degree = divmod(absolute_degree, 7)

    return tonic + target_octave * 12 + degrees[target_degree]
