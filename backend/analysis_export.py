"""Serialization helpers for analysis-validation artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import librosa

from .melody import Note
from .models import Analysis
from .preprocess import InputQuality

SCHEMA_VERSION = 1


def build_metadata(
    *,
    original_filename: str,
    quality: InputQuality,
    analysis: Analysis,
    notes: list[Note],
) -> dict:
    """Build the stable, human-readable analysis-test metadata contract."""
    melody = [
        {
            "midi_pitch": note.pitch,
            "pitch_name": librosa.midi_to_note(note.pitch),
            "pitch_class": note.pitch_class,
            "start_seconds": round(note.start, 6),
            "end_seconds": round(note.end, 6),
            "duration_seconds": round(note.duration, 6),
        }
        for note in notes
    ]
    pitches = list(dict.fromkeys(note.pitch for note in notes))

    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "original_filename": original_filename,
            "source_sample_rate": quality.source_sample_rate,
            "source_channels": quality.source_channels,
            "duration_seconds": quality.source_duration_seconds,
        },
        "cleaned_audio": {
            "path": "cleaned.wav",
            "sample_rate": 44100,
            "channels": 1,
            "duration_seconds": quality.cleaned_duration_seconds,
            "peak_dbfs": quality.peak_dbfs,
            "rms_dbfs": quality.rms_dbfs,
            "clipped_fraction": quality.clipped_fraction,
            "leading_trim_seconds": quality.leading_trim_seconds,
            "trailing_trim_seconds": quality.trailing_trim_seconds,
            "warnings": quality.warnings,
        },
        "analysis": {
            "tempo_bpm": round(analysis.bpm, 6),
            "key": analysis.key,
            "mode": analysis.mode,
            "downbeat_offset_seconds": round(analysis.downbeat_offset_s, 6),
            "chords": [
                {
                    "bar": bar.index,
                    "start_seconds": round(bar.start, 6),
                    "end_seconds": round(bar.end, 6),
                    "chord": bar.chord,
                }
                for bar in analysis.bars
            ],
            "melody": melody,
            "pitches": pitches,
        },
    }


def write_metadata(path: str | Path, metadata: dict) -> None:
    """Atomically write formatted JSON so partial runs cannot look valid."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
        json.dump(metadata, handle, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, destination)
