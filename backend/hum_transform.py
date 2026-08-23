"""Deterministic conversion of a monophonic hum into usable MIDI."""
from __future__ import annotations

from dataclasses import dataclass

import pretty_midi

from .pitch_tracking import Note
from .models import Analysis
from .theory import chord_to_midi, scale_pitch_classes

TARGETS = ("melody", "bass")
MIN_NOTES = 2


@dataclass(frozen=True)
class TransformOptions:
    """User-controlled corrections; faithful melody applies none by default."""

    faithful: bool = True
    snap_to_key: bool = False
    quantize: bool = False
    quantize_division: int = 8


def transform(
    notes: list[Note], analysis: Analysis, target: str, options: TransformOptions | None = None,
) -> pretty_midi.PrettyMIDI:
    """Build editable MIDI from tracked hum notes.

    Melody faithfully retains the extracted performance unless a correction is
    explicitly requested. Bass is intentionally an arrangement transform.
    """
    if target not in TARGETS:
        raise ValueError(f"target must be one of: {', '.join(TARGETS)}")
    if len(notes) < MIN_NOTES:
        raise ValueError("could not find enough pitched notes; hum one clear melody line")
    options = options or TransformOptions()
    midi = pretty_midi.PrettyMIDI(initial_tempo=analysis.bpm)
    instrument = pretty_midi.Instrument(program=33 if target == "bass" else 40, name=target)
    midi.instruments.append(instrument)
    source = _merge_for_bass(notes) if target == "bass" else notes
    scale = scale_pitch_classes(analysis.key, analysis.mode)
    previous: int | None = None
    for note in source:
        start, end = _timing(note, analysis, options, force=(target == "bass"))
        pitch = note.pitch
        if options.snap_to_key or target == "bass":
            pitch = _nearest_scale(pitch, scale)
        if target == "bass":
            pitch = _bass_pitch(pitch, start, analysis, previous)
        instrument.notes.append(pretty_midi.Note(velocity=88, pitch=pitch, start=start, end=end))
        previous = pitch
    return midi


def _timing(note: Note, analysis: Analysis, options: TransformOptions, force: bool) -> tuple[float, float]:
    if not (options.quantize or force):
        return max(0.0, note.start), min(note.end, analysis.duration)
    division = 4 if force else max(1, min(32, options.quantize_division))
    step = analysis.seconds_per_beat / division
    start = max(0.0, round(note.start / step) * step)
    end = max(start + step, round(note.end / step) * step)
    return start, min(end, analysis.duration)


def _nearest_scale(pitch: int, scale: list[int]) -> int:
    return min(range(pitch - 2, pitch + 3), key=lambda candidate: (candidate % 12 not in scale, abs(candidate - pitch)))


def _bass_pitch(source_pitch: int, start: float, analysis: Analysis, previous: int | None) -> int:
    bar = next((b for b in analysis.bars if b.start <= start < b.end), analysis.bars[-1])
    tones = chord_to_midi(bar.chord, octave=2)
    candidates = [tone + octave * 12 for tone in tones for octave in (-1, 0, 1)]
    target = source_pitch - 24
    if previous is not None:
        target = (target + previous) / 2
    return int(min(candidates, key=lambda candidate: abs(candidate - target)))


def _merge_for_bass(notes: list[Note]) -> list[Note]:
    out: list[Note] = []
    for note in notes:
        if note.duration < 0.10:
            continue
        if out and note.pitch == out[-1].pitch and note.start - out[-1].end < 0.12:
            out[-1] = Note(note.pitch, out[-1].start, note.end)
        else:
            out.append(note)
    return out or notes
