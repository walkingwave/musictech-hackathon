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
from .analysis import analyze as analyze_reference
from .models import Analysis, Part, ReferencePart
from .theory import chord_to_midi, parse_chord, transpose_diatonic

# General MIDI program numbers, so exported MIDI opens with sane sounds.
GM_PROGRAMS = {"bass": 33, "chords": 0, "harmony": 52}  # finger bass, grand piano, choir aahs

# General MIDI percussion note numbers.
DRUM_KICK, DRUM_SNARE, DRUM_HAT = 36, 38, 42


def arrange(part: Part, analysis: Analysis, vocal: np.ndarray, sr: int) -> pretty_midi.PrettyMIDI:
    """Build the MIDI for one part. `vocal` is only used by the harmony arranger."""
    if part not in ARRANGERS:
        raise ValueError(f"unknown part: {part}")
    return ARRANGERS[part](analysis, vocal, sr)


def arrange_reference(
    part: ReferencePart,
    analysis: Analysis,
    reference: np.ndarray,
    sr: int,
) -> pretty_midi.PrettyMIDI:
    """Turn a user-recorded part reference into MIDI on the session grid.

    The reference carries musical intent. Its notes/onsets are detected in
    the reference recording, then mapped by beat position onto the anchor
    harmony vocal's BPM/downbeat grid so every guide starts at zero and
    shares the same duration.
    """
    if part == "drums":
        return _arrange_reference_drums(analysis, reference, sr)
    return _arrange_reference_pitched(part, analysis, reference, sr)


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


# --- chords -------------------------------------------------------------


def _arrange_chords(analysis: Analysis, vocal: np.ndarray, sr: int) -> pretty_midi.PrettyMIDI:
    """Block triads on beats 1 and 3, held for two beats each."""
    midi, instrument = _new_midi(analysis, "chords")
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


# --- reference guides ---------------------------------------------------


def _arrange_reference_pitched(
    part: ReferencePart,
    analysis: Analysis,
    reference: np.ndarray,
    sr: int,
) -> pretty_midi.PrettyMIDI:
    """Preserve a hummed reference's pitch contour, quantized to the anchor grid."""
    midi, instrument = _new_midi(analysis, part)
    mono = librosa.to_mono(reference) if reference.ndim > 1 else reference
    ref_analysis = analyze_reference(mono, sr)
    quantum = analysis.seconds_per_beat / 2

    for note in melody.track(mono, sr):
        start = _map_reference_time(note.start, ref_analysis, analysis)
        end = _map_reference_time(note.end, ref_analysis, analysis)
        start = _quantize_time(start, quantum)
        end = _quantize_time(end, quantum)

        if end <= start:
            end = start + quantum
        if start >= analysis.duration:
            continue

        pitch = note.pitch
        if part == "bass":
            # Keep the sung note class, but place it in a bass register.
            pitch = 36 + (note.pitch % 12)

        _add(instrument, pitch, max(0.0, start), min(end, analysis.duration), velocity=90)

    return midi


def _arrange_reference_drums(
    analysis: Analysis,
    reference: np.ndarray,
    sr: int,
) -> pretty_midi.PrettyMIDI:
    """Map beatbox/tap onsets onto the anchor grid with simple drum labels."""
    midi, instrument = _new_midi(analysis, "drums")
    mono = librosa.to_mono(reference) if reference.ndim > 1 else reference
    ref_analysis = analyze_reference(mono, sr)
    onset_env = librosa.onset.onset_strength(y=mono, sr=sr)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, units="frames")
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    quantum = analysis.seconds_per_beat / 2

    for onset_time in onset_times:
        start = _quantize_time(_map_reference_time(float(onset_time), ref_analysis, analysis), quantum)
        if not 0 <= start < analysis.duration:
            continue

        beat_index = int(round((start - analysis.downbeat_offset_s) / analysis.seconds_per_beat)) % 4
        pitch = DRUM_KICK if beat_index in (0, 2) else DRUM_SNARE
        _add(instrument, pitch, start, min(start + 0.1, analysis.duration), velocity=100)

        offbeat = start + analysis.seconds_per_beat / 2
        if offbeat < analysis.duration:
            _add(instrument, DRUM_HAT, offbeat, offbeat + 0.05, velocity=55)

    return midi


def _map_reference_time(seconds: float, reference: Analysis, anchor: Analysis) -> float:
    """Same beat position in the reference, expressed in anchor-session seconds."""
    beat_position = (seconds - reference.downbeat_offset_s) / reference.seconds_per_beat
    return anchor.downbeat_offset_s + beat_position * anchor.seconds_per_beat


def _quantize_time(seconds: float, quantum: float) -> float:
    return round(seconds / quantum) * quantum


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
    "chords": _arrange_chords,
    "drums": _arrange_drums,
    "harmony": _arrange_harmony,
}
