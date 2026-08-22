"""Synthesize a test vocal with known BPM and key.

Gives the pipeline something deterministic to run against before anyone
has recorded a real take, and gives the analysis stage a ground truth to
be checked against.

    uv run python scripts/make_test_vocal.py

Writes samples/test_vocal.wav: 8 bars at 100 BPM in A minor.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 44100
BPM = 100
BARS = 8
KEY = "A minor"

# A simple melody over Am-F-C-G, two bars per chord, as MIDI note numbers.
MELODY = [69, 72, 71, 69, 65, 69, 67, 65, 72, 76, 74, 72, 67, 71, 74, 67]


def main() -> None:
    seconds_per_beat = 60.0 / BPM
    note_seconds = seconds_per_beat  # one note per beat
    total = int(BARS * 4 * note_seconds * SAMPLE_RATE)
    audio = np.zeros(total, dtype=np.float32)

    for i, pitch in enumerate(MELODY * (BARS * 4 // len(MELODY))):
        frequency = 440.0 * 2 ** ((pitch - 69) / 12)
        n = int(note_seconds * SAMPLE_RATE)
        t = np.arange(n, dtype=np.float32) / SAMPLE_RATE

        # A few harmonics and light vibrato, so pitch tracking and chroma
        # have something more voice-like than a bare sine to work with.
        vibrato = 1 + 0.005 * np.sin(2 * np.pi * 5.5 * t)
        wave = sum(
            (1.0 / harmonic) * np.sin(2 * np.pi * frequency * harmonic * t * vibrato)
            for harmonic in (1, 2, 3)
        )

        envelope = np.minimum(1.0, np.minimum(t * 40, (note_seconds - t) * 20))
        start = i * n
        audio[start:start + n] += (wave * envelope * 0.3).astype(np.float32)

    out = Path(__file__).resolve().parent.parent / "samples" / "test_vocal.wav"
    out.parent.mkdir(exist_ok=True)
    sf.write(out, audio, SAMPLE_RATE)
    print(f"wrote {out}  ({BARS} bars, {BPM} BPM, {KEY})")


if __name__ == "__main__":
    main()
