"""Generate a suite of test vocals with known BPM, key and mode.

Detection accuracy is impossible to improve without something to measure
it against, and we cannot record a dozen real takes at known tempos. These
fixtures stand in: synthetic, but voice-like enough to exercise the same
code paths, and each one carries its ground truth in a manifest.

    uv run python scripts/make_test_vocals.py

Writes samples/fixtures/*.wav plus samples/fixtures/manifest.json.

The suite deliberately includes relative major/minor pairs (A minor and
C major, E minor and G major) because those share every note and are the
case key detection most often gets wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 44100
OUT_DIR = Path(__file__).resolve().parent.parent / "samples" / "fixtures"

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
}
NOTE_TO_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Melodies as scale degrees (0 = tonic). These are the easy tier: they
# start on the tonic, move stepwise, and resolve back to the tonic, which
# is exactly the cue that separates a key from its relative.
PHRASES = [
    [0, 2, 4, 2, 0, -3, 0, 0],
    [0, 4, 2, 0, 4, 5, 4, 0],
    [0, 1, 2, 4, 2, 1, 0, 0],
    [4, 2, 0, 2, 4, 4, 2, 0],
]

# The hard tier: melodies that dwell on the mediant and dominant, and
# touch the tonic almost only at phrase endings. A singer sketching an
# idea often does exactly this.
#
# This is the case that discriminates between methods. By raw pitch
# distribution these melodies look like the relative key - the mediant is
# the most common note by a wide margin. The only thing pointing at the
# real tonic is *where the phrases land*. A detector that scores pitch
# histograms alone must get these wrong; one that weighs phrase endings
# can get them right.
#
# Note the melodies are still genuinely in the stated key. Making the
# tonic entirely absent would make them unrecoverable rather than hard.
AMBIGUOUS_PHRASES = [
    [2, 4, 3, 2, 4, 2, 1, 0],
    [4, 2, 6, 4, 2, 4, 5, 4],
    [2, 3, 4, 6, 4, 3, 2, 0],
    [6, 4, 2, 4, 6, 4, 2, 0],
]

# Note lengths in beats. Varied so beat tracking sees real rhythm rather
# than a metronomic run of quarter notes. The last row includes rests
# (negative values mean silence) to break up the onset stream.
RHYTHMS = [
    [1, 1, 1, 1, 1, 1, 1, 1],
    [1.5, 0.5, 1, 1, 0.5, 0.5, 1, 2],
    [0.5, 0.5, 1, 2, 0.5, 0.5, 1, 2],
    [1, 0.5, 0.5, -1, 1, 1, 0.5, 1.5],
]

# (name, bpm, key, mode, hard). The relative pairs are adjacent so failures
# are easy to spot in the eval output.
CASES = [
    # easy tier - clean onsets, tonic resolution, steady tempo
    ("amin_100", 100, "A", "minor", False),
    ("cmaj_100", 100, "C", "major", False),
    ("emin_90", 90, "E", "minor", False),
    ("gmaj_90", 90, "G", "major", False),
    ("dmin_72", 72, "D", "minor", False),
    ("fmaj_72", 72, "F", "major", False),
    ("amin_140", 140, "A", "minor", False),
    ("cmaj_120", 120, "C", "major", False),
    ("gmaj_128", 128, "G", "major", False),
    ("emin_75", 75, "E", "minor", False),
    # hard tier - rubato, room noise, offset start, no tonic resolution
    ("hard_amin_100", 100, "A", "minor", True),
    ("hard_cmaj_100", 100, "C", "major", True),
    ("hard_emin_90", 90, "E", "minor", True),
    ("hard_gmaj_90", 90, "G", "major", True),
    ("hard_dmin_115", 115, "D", "minor", True),
    ("hard_fmaj_115", 115, "F", "major", True),
    ("hard_bmin_84", 84, "B", "minor", True),
    ("hard_dmaj_84", 84, "D", "major", True),
]

# How far the hard tier's tempo wanders, as a fraction. Real singers
# without a click drift by a few percent across a take.
RUBATO = 0.03

# Room tone floor for the hard tier.
NOISE_FLOOR = 0.004


def degree_to_midi(degree: int, tonic_pc: int, mode: str, octave: int = 4) -> int:
    """Scale degree (can be negative or >6) -> MIDI note number."""
    steps = SCALES[mode]
    octave_shift, index = divmod(degree, 7)
    return (octave + 1) * 12 + tonic_pc + steps[index] + 12 * octave_shift


def synth_note(frequency: float, seconds: float, rng: np.random.Generator) -> np.ndarray:
    """A rough sung vowel: harmonic stack, vibrato, breathy noise floor."""
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE

    # Vibrato that fades in, as a singer's does on a held note.
    #
    # The instantaneous frequency is integrated into phase. Multiplying t
    # by the vibrato inside sin() instead would phase-modulate with a
    # depth that grows linearly across the note - turning an intended
    # 0.1-semitone wobble into a 1.5-semitone swing by the note's end.
    vibrato_depth = 0.006 * np.minimum(1.0, t / 0.25)
    instantaneous = frequency * (1 + vibrato_depth * np.sin(2 * np.pi * 5.5 * t))

    # Harmonics rolling off at 1/n, which is roughly a vowel's spectrum.
    wave = np.zeros(n, dtype=np.float32)
    for harmonic in range(1, 7):
        phase = 2 * np.pi * np.cumsum(instantaneous * harmonic) / SAMPLE_RATE
        wave += (1.0 / harmonic) * np.sin(phase)

    # Breath noise, so onset detection has broadband energy to find.
    wave += rng.standard_normal(n).astype(np.float32) * 0.02

    attack = np.minimum(1.0, t / 0.02)
    release = np.minimum(1.0, np.maximum(0.0, (seconds - t)) / 0.06)
    return (wave * attack * release * 0.25).astype(np.float32)


def render_case(bpm: float, key: str, mode: str, seed: int, hard: bool) -> np.ndarray:
    """Four phrases, each with its own rhythm, at the given tempo and key.

    The hard tier adds the things that make real recordings hard: melodies
    that avoid the tonic, tempo that drifts, a noise floor, and a start
    that is not aligned to the beat grid.
    """
    rng = np.random.default_rng(seed)
    tonic_pc = NOTE_TO_PC[key]
    phrases = AMBIGUOUS_PHRASES if hard else PHRASES
    rhythms = RHYTHMS if hard else RHYTHMS[:3]

    events: list[tuple[int, float, float]] = []  # (midi, start_s, seconds)
    position_beats = 0.0
    elapsed = 0.0

    for phrase_index in range(4):
        phrase = phrases[phrase_index % len(phrases)]
        rhythm = rhythms[phrase_index % len(rhythms)]

        for degree, beats in zip(phrase, rhythm):
            # Tempo drifts smoothly across the take rather than jumping.
            if hard:
                drift = 1 + RUBATO * np.sin(2 * np.pi * position_beats / 32)
                seconds_per_beat = (60.0 / bpm) * float(drift)
            else:
                seconds_per_beat = 60.0 / bpm

            duration = abs(beats) * seconds_per_beat
            if beats > 0:  # negative means a rest
                midi = degree_to_midi(degree, tonic_pc, mode)
                events.append((midi, elapsed, duration))

            position_beats += abs(beats)
            elapsed += duration

    # An offset start, so bar 1 does not begin at t=0.
    lead_in = float(rng.uniform(0.2, 0.9)) if hard else 0.0

    total = int((elapsed + lead_in + 1.0) * SAMPLE_RATE)
    audio = np.zeros(total, dtype=np.float32)

    if hard:
        audio += rng.standard_normal(total).astype(np.float32) * NOISE_FLOOR

    for midi, start_s, seconds in events:
        frequency = 440.0 * 2 ** ((midi - 69) / 12)
        # Slight detuning, as an unaccompanied singer drifts.
        if hard:
            frequency *= 1 + rng.normal(0, 0.004)
        # Leave a small gap so consecutive notes have distinct onsets.
        note = synth_note(frequency, max(seconds * 0.9, 0.05), rng)
        start = int((start_s + lead_in) * SAMPLE_RATE)
        audio[start:start + len(note)] += note

    return audio


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []

    for seed, (name, bpm, key, mode, hard) in enumerate(CASES):
        audio = render_case(bpm, key, mode, seed, hard)
        path = OUT_DIR / f"{name}.wav"
        sf.write(path, audio, SAMPLE_RATE)

        manifest.append({
            "file": path.name,
            "bpm": bpm,
            "key": key,
            "mode": mode,
            "hard": hard,
            "duration": round(len(audio) / SAMPLE_RATE, 2),
        })
        tier = "hard" if hard else "easy"
        print(f"  {path.name:<16} {bpm:>3} BPM  {key:>2} {mode:<6} {tier}")

    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {len(manifest)} fixtures to {OUT_DIR}")


if __name__ == "__main__":
    main()
