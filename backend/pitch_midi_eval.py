"""Evaluate a tracker against a local JSON manifest of annotated hum MIDI.

Manifest entries: {"audio": "take.wav", "notes": [{"pitch": 60, "start": 0,
"end": 0.5}]}; audio paths are resolved relative to the manifest.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import soundfile as sf

from . import melody
from .pitch_tracking import Note


def _score(expected: list[Note], actual: list[Note], onset_tolerance: float) -> tuple[int, int, int]:
    used: set[int] = set()
    hits = 0
    for note in expected:
        choices = [
            (abs(note.start - candidate.start), index)
            for index, candidate in enumerate(actual)
            if index not in used and candidate.pitch == note.pitch
            and abs(note.start - candidate.start) <= onset_tolerance
        ]
        if choices:
            _, index = min(choices)
            used.add(index)
            hits += 1
    return hits, len(actual) - hits, len(expected) - hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text())
    base = args.manifest.parent
    totals = {"expected": 0, "actual": 0, "hit50": 0, "hit100": 0}
    elapsed = 0.0
    for case in cases:
        audio, sr = sf.read(base / case["audio"], dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        started = time.perf_counter()
        result = melody.track_with_diagnostics(audio, sr)
        elapsed += time.perf_counter() - started
        expected = [Note(**note) for note in case["notes"]]
        totals["expected"] += len(expected)
        totals["actual"] += len(result.notes)
        totals["hit50"] += _score(expected, result.notes, 0.050)[0]
        totals["hit100"] += _score(expected, result.notes, 0.100)[0]
    for label in ("hit50", "hit100"):
        hit = totals[label]
        precision = hit / max(1, totals["actual"])
        recall = hit / max(1, totals["expected"])
        f1 = 2 * precision * recall / max(1e-12, precision + recall)
        print(f"onset {label[3:]}ms: precision={precision:.3f} recall={recall:.3f} f1={f1:.3f}")
    print(f"notes expected={totals['expected']} actual={totals['actual']} runtime={elapsed:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
